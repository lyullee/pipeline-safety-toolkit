"""Tank geometry, reviewed level/volume inversion and uniform-phase inventory using existing providers."""

import math
from functools import lru_cache
from importlib.metadata import version
from pathlib import Path
from typing import Annotated, Literal

import numpy as np
from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest
from .datasets import file_digest
from .thermo import CheckedProvider, CoolPropProvider, StateRequest, ThermodynamicState


class TankHead(Contract):
    kind: Literal["flat", "conical", "ellipsoidal", "spherical", "torispherical", "guppy"] = "flat"
    depth_m: Positive | None = None
    dish_radius_ratio: Positive | None = None
    knuckle_radius_ratio: float | None = Field(default=None, gt=0, lt=0.5)

    @model_validator(mode="after")
    def complete(self):
        if self.kind == "flat":
            if any(v is not None for v in (self.depth_m, self.dish_radius_ratio, self.knuckle_radius_ratio)):
                raise ValueError("flat tank heads do not accept hidden depth or radius parameters")
        elif self.kind == "torispherical":
            if (
                self.depth_m is not None
                or self.dish_radius_ratio is None
                or self.knuckle_radius_ratio is None
            ):
                raise ValueError(
                    "torispherical heads need explicit dish and knuckle ratios, without a separate depth"
                )
            if self.dish_radius_ratio < 0.5 or self.dish_radius_ratio <= self.knuckle_radius_ratio:
                raise ValueError("the declared torispherical radii cannot enclose the tank diameter")
        elif (
            self.depth_m is None
            or self.dish_radius_ratio is not None
            or self.knuckle_radius_ratio is not None
        ):
            raise ValueError("this head requires an explicit depth, without torispherical radius parameters")
        return self


class AxisymmetricTank(Contract):
    kind: Literal["axisymmetric"] = "axisymmetric"
    inner_diameter_m: Positive
    straight_length_m: Nonnegative
    horizontal: bool
    side_a: TankHead = Field(default_factory=TankHead)
    side_b: TankHead = Field(default_factory=TankHead)
    evidence_id: Name

    @model_validator(mode="after")
    def geometry(self):
        if self.straight_length_m == 0 and self.side_a.kind == self.side_b.kind == "flat":
            raise ValueError("a tank needs positive internal volume")
        for head in (self.side_a, self.side_b):
            if head.kind == "guppy" and not self.horizontal:
                raise ValueError("the current geometry backend does not qualify vertical guppy heads")
            if head.kind == "spherical" and head.depth_m > self.inner_diameter_m / 2:
                raise ValueError(
                    "a spherical head deeper than a hemisphere exceeds the declared diameter envelope"
                )
        return self


class CalibratedTankCurve(Contract):
    kind: Literal["calibrated_curve"] = "calibrated_curve"
    heights_m: tuple[Nonnegative, ...] = Field(min_length=2, max_length=10000)
    volumes_m3: tuple[Nonnegative, ...] = Field(min_length=2, max_length=10000)
    evidence_id: Name
    interpolation: Literal["piecewise_linear"] = "piecewise_linear"

    @model_validator(mode="after")
    def monotone(self):
        if len(self.heights_m) != len(self.volumes_m3) or self.heights_m[0] != 0 or self.volumes_m3[0] != 0:
            raise ValueError(
                "tank calibration needs aligned internal-bottom height and volume pairs starting at zero"
            )
        for values in (self.heights_m, self.volumes_m3):
            if any(b <= a for a, b in zip(values, values[1:], strict=False)):
                raise ValueError(
                    "tank calibration height and volume must strictly increase without extrapolation"
                )
        return self


TankGeometry = Annotated[AxisymmetricTank | CalibratedTankCurve, Field(discriminator="kind")]


class TankObservation(Contract):
    tank_id: Name
    clock_id: Name
    time_s: Nonnegative
    quantity: Literal["level_from_internal_bottom", "liquid_volume"]
    value: float | None
    unit: Literal["m", "m^3"]
    valid: bool = True
    evidence_id: Name

    @model_validator(mode="after")
    def units(self):
        expected = "m" if self.quantity == "level_from_internal_bottom" else "m^3"
        if self.unit != expected:
            raise ValueError("tank level and volume observations need their explicit SI unit")
        return self


class DeclaredTankDensity(Contract):
    kind: Literal["declared"] = "declared"
    phase: Literal["liquid", "gas"]
    fluid: Name
    density_kg_m3: Positive
    property_basis: Name
    evidence_id: Name


class PropertyTankDensity(Contract):
    kind: Literal["property_state"] = "property_state"
    state: StateRequest
    evidence_id: Name


TankDensity = Annotated[DeclaredTankDensity | PropertyTankDensity, Field(discriminator="kind")]


class TankInventoryRequest(Contract):
    geometry: TankGeometry
    observation: TankObservation
    liquid_density: TankDensity | None = None
    headspace_density: TankDensity | None = None
    inverse_method: Literal["brenth", "spline"] = "brenth"
    volume_tolerance_m3: Positive = 1e-9
    height_tolerance_m: Positive = 1e-9
    relative_tolerance: Positive = 1e-9


@lru_cache(maxsize=1)
def _fluids_identity():
    import inspect

    from fluids.geometry import TANK

    return {
        "id": "fluids.geometry.TANK",
        "version": version("fluids"),
        "source_sha256": file_digest(Path(inspect.getfile(TANK))),
    }


def _shape(geometry, inverse_method):
    if isinstance(geometry, CalibratedTankCurve):
        return (
            geometry.heights_m[-1],
            geometry.volumes_m3[-1],
            lambda h: float(np.interp(h, geometry.heights_m, geometry.volumes_m3)),
            lambda v: float(np.interp(v, geometry.volumes_m3, geometry.heights_m)),
            {"id": "reviewed_piecewise_linear_calibration", "source_digest": digest(geometry)},
        )
    from fluids.geometry import TANK

    parameters = {
        "D": geometry.inner_diameter_m,
        "L": geometry.straight_length_m,
        "horizontal": geometry.horizontal,
    }
    for key, head in (("sideA", geometry.side_a), ("sideB", geometry.side_b)):
        parameters[key] = None if head.kind == "flat" else head.kind
        if head.depth_m is not None:
            parameters[key + "_a"] = head.depth_m
        if head.dish_radius_ratio is not None:
            parameters[key + "_f"] = head.dish_radius_ratio
            parameters[key + "_k"] = head.knuckle_radius_ratio
    tank = TANK(**parameters)
    return (
        float(tank.h_max),
        float(tank.V_total),
        lambda h: float(tank.V_from_h(h, method="full")),
        lambda v: float(tank.h_from_V(v, method=inverse_method)),
        _fluids_identity() | {"parameters": parameters, "inverse_method": inverse_method},
    )


def _density(specification, role, provider):
    if specification is None:
        return None, None, None
    if isinstance(specification, DeclaredTankDensity):
        if specification.phase != role:
            return None, None, "declared_density_phase_mismatch:" + role
        return specification.density_kg_m3, specification.model_dump(mode="json"), None
    request = specification.state
    state = CheckedProvider(CoolPropProvider() if provider is None else provider).resolve(request)
    state = ThermodynamicState.model_validate(state.model_dump(mode="python"))
    available = {
        "P": state.pressure_pa_abs,
        "T": state.temperature_k,
        "Dmass": state.density_kg_m3,
        "Hmass": state.enthalpy_j_kg,
        "Smass": state.entropy_j_kg_k,
    }
    if any(
        k in available and not math.isclose(v, available[k], rel_tol=1e-8, abs_tol=1e-8)
        for k, v in request.inputs.items()
    ):
        raise ValueError("the property provider changed the requested tank density state")
    acceptable = state.phase in (
        {"liquid", "supercritical_liquid"} if role == "liquid" else {"gas", "supercritical_gas"}
    )
    if state.phase == "twophase":
        acceptable = not request.single_phase_required and request.inputs.get("Q") == (
            0 if role == "liquid" else 1
        )
    if not acceptable:
        return None, state.model_dump(mode="json"), "property_density_phase_mismatch:" + role
    return state.density_kg_m3, state.model_dump(mode="json"), None


def evaluate_tank_inventory(request: TankInventoryRequest, context: RunContext, *, property_provider=None):
    """Resolve static geometry and supplied uniform phase densities; do not evolve tank thermodynamics or flow."""
    request = TankInventoryRequest.model_validate(request.model_dump(mode="python"))
    context = RunContext.model_validate(context.model_dump(mode="python"))
    observation = request.observation
    if observation.tank_id != context.asset_id or observation.clock_id != context.clock_id:
        raise ValueError("tank observation asset and clock must match the calculation context")
    evidence = {e.id for e in context.evidence}
    needed = {request.geometry.evidence_id, observation.evidence_id}
    needed.update(s.evidence_id for s in (request.liquid_density, request.headspace_density) if s is not None)
    if len(evidence) != len(context.evidence) or needed - evidence:
        raise ValueError("tank geometry, observations and densities need unique context evidence")
    issues, geometry, values, states, checks = [], None, {}, {}, {}
    if not observation.valid or observation.value is None:
        status = "needs_data"
        issues.append("tank_observation_missing_or_invalid")
    else:
        h_max, total, volume_at, height_at, geometry = _shape(request.geometry, request.inverse_method)
        if not math.isfinite(h_max) or not math.isfinite(total) or h_max <= 0 or total <= 0:
            raise ValueError("the tank geometry backend returned invalid height or capacity")
        grid_h = np.linspace(0.0, h_max, 17)
        grid_v = [volume_at(float(h)) for h in grid_h]
        tolerance = max(request.volume_tolerance_m3, request.relative_tolerance * total)
        checks = {
            "maximum_internal_height_m": h_max,
            "total_internal_volume_m3": total,
            "volume_tolerance_m3": tolerance,
            "monotonicity_probe_count": len(grid_v),
        }
        if (
            any(not math.isfinite(v) or v < 0 or v > total + tolerance for v in grid_v)
            or any(b < a for a, b in zip(grid_v, grid_v[1:], strict=False))
            or abs(grid_v[0]) > tolerance
            or abs(grid_v[-1] - total) > tolerance
        ):
            raise ValueError("the native tank volume map failed endpoint, domain or monotonicity checks")
        level_input = observation.quantity == "level_from_internal_bottom"
        if not 0 <= observation.value <= (h_max if level_input else total):
            status = "out_of_scope"
            issues.append("tank_observation_outside_internal_geometry")
        else:
            if level_input:
                level, volume = observation.value, volume_at(observation.value)
                if level == 0 or level == h_max:
                    checks["native_boundary_volume_m3"] = volume
                    volume = 0.0 if level == 0 else total
            else:
                volume = observation.value
                level = 0.0 if volume == 0 else (h_max if volume == total else height_at(volume))
            volume_error = abs(volume_at(level) - volume)
            reverse_level = 0.0 if volume == 0 else (h_max if volume == total else height_at(volume))
            height_error = abs(reverse_level - level)
            checks.update(volume_residual_m3=volume_error, height_residual_m=height_error)
            if (
                not math.isfinite(level)
                or not math.isfinite(volume)
                or not 0 <= level <= h_max
                or not 0 <= volume <= total
                or volume_error > tolerance
                or height_error > max(request.height_tolerance_m, request.relative_tolerance * h_max)
            ):
                status = "withheld"
                issues.append("tank_inverse_or_volume_residual_not_qualified")
            else:
                status = "computed"
                values = {
                    "liquid_level_m": level,
                    "liquid_volume_m3": volume,
                    "headspace_volume_m3": total - volume,
                    "total_internal_volume_m3": total,
                    "maximum_internal_height_m": h_max,
                    "fill_volume_fraction": volume / total,
                    "volume_residual_m3": volume_error,
                    "height_residual_m": height_error,
                }
                masses = []
                for role, phase_volume, specification in (
                    ("liquid", volume, request.liquid_density),
                    ("gas", total - volume, request.headspace_density),
                ):
                    if phase_volume == 0:
                        mass, density, state, issue = 0.0, None, None, None
                    else:
                        density, state, issue = _density(specification, role, property_provider)
                        mass = None if density is None else phase_volume * density
                        if mass is not None and not math.isfinite(mass):
                            raise ValueError("tank phase inventory exceeds the finite reporting domain")
                    if issue:
                        issues.append(issue)
                    states[role] = state
                    values[role + "_density_kg_m3"] = density
                    values[role + "_mass_kg"] = mass
                    masses.append(mass)
                values["total_fluid_mass_kg"] = None if any(m is None for m in masses) else math.fsum(masses)
    body = {
        "schema": "pipeline_integrity.tank_inventory.v1",
        "status": status,
        "mass_status": "computed" if values.get("total_fluid_mass_kg") is not None else "needs_data",
        "context": context.model_dump(mode="json"),
        "request": request.model_dump(mode="json"),
        "geometry_provider": geometry,
        "geometry_checks": checks,
        "values": values,
        "phase_density_states": states,
        "issues": issues,
        "uniform_density_per_phase_assumed": True,
        "equilibrium_or_tank_dynamics_solved": False,
        "field_calibrated": False,
        "implementation_sha256": file_digest(Path(__file__)),
    }
    return body | {"result_digest": digest(body)}


def lh2dt_tank_geometry(geometry: AxisymmetricTank, *, factory=None):
    """Map the receiver's supported horizontal flat/ellipsoidal shape and compare native volume profiles."""
    geometry = AxisymmetricTank.model_validate(geometry.model_dump(mode="python"))
    if (
        not geometry.horizontal
        or geometry.side_a != geometry.side_b
        or geometry.side_a.kind not in {"flat", "ellipsoidal"}
    ):
        raise ValueError("the receiver requires horizontal geometry with identical flat or ellipsoidal heads")
    if factory is None:
        from lh2dt.geometry import HorizontalVesselGeometry

        factory = HorizontalVesselGeometry
    depth = 0.0 if geometry.side_a.kind == "flat" else geometry.side_a.depth_m
    parameters = {
        "inner_diameter_m": geometry.inner_diameter_m,
        "straight_length_m": geometry.straight_length_m,
        "head_depth_m": depth,
    }
    native = factory(**parameters)
    import inspect

    try:
        receiver_source_sha256 = file_digest(Path(inspect.getfile(type(native))))
    except (TypeError, OSError):
        receiver_source_sha256 = None
    height, volume, volume_at, _, provider = _shape(geometry, "brenth")
    rows = [
        {
            "height_m": float(h),
            "fluids_volume_m3": volume_at(float(h)),
            "receiver_volume_m3": float(native.at_height(float(h)).liquid_volume_m3),
        }
        for h in np.linspace(0.0, height, 17)
    ]
    if any(
        not math.isfinite(r[k]) or r[k] < 0 for r in rows for k in ("fluids_volume_m3", "receiver_volume_m3")
    ):
        raise ValueError("the receiving tank geometry returned a nonfinite or negative volume")
    error = max(abs(r["fluids_volume_m3"] - r["receiver_volume_m3"]) for r in rows)
    if not math.isclose(native.volume_m3, volume, rel_tol=1e-8, abs_tol=1e-10) or error > max(
        1e-9, volume * 1e-7
    ):
        raise ValueError("the receiving tank geometry changed the declared capacity or level-volume relation")
    record = {
        "schema": "pipeline_integrity.tank_geometry_exchange.v1",
        "source_geometry": geometry.model_dump(mode="json"),
        "source_digest": digest(geometry),
        "receiver_parameters": parameters,
        "receiver_class": type(native).__module__ + "." + type(native).__qualname__,
        "receiver_source_sha256": receiver_source_sha256,
        "geometry_provider": provider,
        "native_total_volume_m3": float(native.volume_m3),
        "volume_profile": rows,
        "maximum_volume_difference_m3": error,
        "qualified_scope": "volume_geometry_only",
        "wetted_area_or_dynamics_qualified": False,
        "field_calibrated": False,
    }
    return native, record | {"result_digest": digest(record)}
