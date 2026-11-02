"""Reviewed static compressor maps chained through the existing equipment owner.

Map coordinates and thermodynamic stations are explicit.  A static envelope
check does not simulate a surge cycle, a rotor, or an anti-surge controller.
"""

from __future__ import annotations

import json
import math
import platform
import tomllib
from dataclasses import asdict
from importlib.metadata import distribution, version
from pathlib import Path
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from . import contracts, equipment_exchange, thermo
from .contracts import Contract, Name, Nonnegative, Positive, RunContext, SignalFrame, digest, json_value
from .datasets import file_digest
from .equipment_exchange import EquipmentRequest, LH2EquipmentClient
from .thermo import StateRequest

SCHEMA = "pipeline_integrity.compressor_train.v1"
REVIEWS = (
    "same_fluid_map_reference_and_total_state_convention",
    "static_map_support_and_operating_margins",
    "steady_forward_mass_and_serial_ports",
    "cooling_boundary_capacity_and_pressure_loss",
    "external_mechanical_and_drive_loss_boundary",
)
UNITS = {
    "pressure": "Pa_abs_total",
    "temperature": "K_total",
    "mass_flow": "kg/s",
    "angular_speed": "rad/s",
    "power": "W",
    "specific_energy": "J/kg",
    "efficiency": "1",
    "time": "s",
}


class CompressorMap(Contract):
    id: Name
    fluid: Literal["Hydrogen", "ParaHydrogen"] | None
    station_basis: Literal["stagnation_total_to_total"] = "stagnation_total_to_total"
    efficiency_definition: Literal["total_to_total_isentropic"] = "total_to_total_isentropic"
    coordinate_convention: Literal["actual", "reference_corrected"]
    reference_pressure_pa_abs: Positive | None = None
    reference_temperature_K: Positive | None = None
    speed_rad_s: tuple[Positive, ...] = Field(min_length=2, max_length=50)
    mass_flow_kg_s: tuple[Positive, ...] = Field(min_length=2, max_length=200)
    pressure_ratio: tuple[tuple[Positive | None, ...], ...]
    isentropic_efficiency: tuple[tuple[Positive | None, ...], ...]
    surge_flow_kg_s: tuple[Positive | None, ...]
    choke_flow_kg_s: tuple[Positive | None, ...]
    inlet_pressure_range_pa_abs: tuple[Positive, Positive] | None
    inlet_temperature_range_K: tuple[Positive, Positive] | None
    evidence_id: Name

    @model_validator(mode="after")
    def shape(self):
        n, m = len(self.speed_rad_s), len(self.mass_flow_kg_s)
        if any(
            b <= a
            for axis in (self.speed_rad_s, self.mass_flow_kg_s)
            for a, b in zip(axis, axis[1:], strict=False)
        ):
            raise ValueError("native map axes must strictly increase; knots are not sorted or extrapolated")
        if any(
            len(v) != n or any(len(row) != m for row in v)
            for v in (self.pressure_ratio, self.isentropic_efficiency)
        ):
            raise ValueError("map tables must follow speed rows and mass-flow columns")
        if len(self.surge_flow_kg_s) != n or len(self.choke_flow_kg_s) != n:
            raise ValueError("each speed needs its own declared surge and choke boundary")
        if any(v is not None and v <= 1 for row in self.pressure_ratio for v in row):
            raise ValueError("compressor pressure ratios must exceed one")
        if any(v is not None and v > 1 for row in self.isentropic_efficiency for v in row):
            raise ValueError("map efficiencies must lie in (0,1]")
        if self.coordinate_convention == "actual" and (
            self.reference_pressure_pa_abs is not None or self.reference_temperature_K is not None
        ):
            raise ValueError("actual-coordinate maps do not apply an implicit reference correction")
        for bounds in (self.inlet_pressure_range_pa_abs, self.inlet_temperature_range_K):
            if bounds is not None and bounds[0] >= bounds[1]:
                raise ValueError("map inlet applicability ranges must have positive width")
        lo, hi = self.mass_flow_kg_s[0], self.mass_flow_kg_s[-1]
        for surge, choke in zip(self.surge_flow_kg_s, self.choke_flow_kg_s, strict=True):
            if surge is not None and choke is not None and not lo <= surge < choke <= hi:
                raise ValueError("declared surge/choke boundaries must be ordered inside the map flow axis")
        return self


class IntercoolerBoundary(Contract):
    mode: Literal["none", "outlet_temperature", "heat_removal"] | None
    pressure_drop_pa: Nonnegative | None
    outlet_total_temperature_K: Positive | None = None
    heat_removal_W: Nonnegative | None = None
    sink_temperature_K: Positive | None = None
    minimum_approach_K: Nonnegative | None = None
    available_heat_rejection_W: Nonnegative | None = None
    evidence_id: Name

    @model_validator(mode="after")
    def independent_boundary(self):
        if self.mode == "none" and (
            self.pressure_drop_pa not in (0, None)
            or any(
                v is not None
                for v in (
                    self.outlet_total_temperature_K,
                    self.heat_removal_W,
                    self.sink_temperature_K,
                    self.minimum_approach_K,
                    self.available_heat_rejection_W,
                )
            )
        ):
            raise ValueError("explicit no-cooler mode has zero pressure loss and no thermal boundary")
        if self.mode == "outlet_temperature" and self.heat_removal_W is not None:
            raise ValueError("a temperature target and imposed heat removal cannot both fix the outlet")
        if self.mode == "heat_removal" and self.outlet_total_temperature_K is not None:
            raise ValueError("an imposed duty cannot also fix the outlet temperature")
        return self


class CompressorStage(Contract):
    id: Name
    shaft_id: Name
    inlet_port_id: Name | None
    compressor_outlet_port_id: Name | None
    outlet_port_id: Name | None
    speed_rad_s: Positive | None
    performance_map: CompressorMap | None
    mechanical_efficiency: Positive | None = Field(le=1)
    drive_efficiency: Positive | None = Field(le=1)
    minimum_surge_flow_margin_fraction: Nonnegative
    minimum_choke_flow_margin_fraction: Nonnegative
    intercooler: IntercoolerBoundary | None
    evidence_id: Name


class CompressorTrainRequest(Contract):
    train_id: Name
    asset_id: Name
    clock_id: Name
    event_id: Name
    time_s: Nonnegative | None
    clock_offset_s: float
    source_port_id: Name | None
    target_port_id: Name | None
    fluid: Literal["Hydrogen", "ParaHydrogen"] | None
    energy_reference: Name | None
    inlet_total_pressure_pa_abs: Positive | None
    inlet_total_temperature_K: Positive | None
    mass_flow_kg_s: Positive | None
    stages: tuple[CompressorStage, ...] = Field(min_length=1, max_length=16)
    evidence_ids: tuple[Name, ...]
    reviews: dict[str, bool] = Field(default_factory=dict)
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    owner_pins: dict = Field(default_factory=dict)
    maximum_map_points: int = Field(default=50000, ge=4, le=100000)
    units: dict[str, str] = Field(default_factory=lambda: dict(UNITS))

    @model_validator(mode="after")
    def identity(self):
        if len({v.id for v in self.stages}) != len(self.stages) or self.units != UNITS:
            raise ValueError("stage identifiers must be unique and map/thermodynamic SI units explicit")
        if self.time_s is not None and (
            not math.isfinite(self.time_s + self.clock_offset_s) or self.time_s + self.clock_offset_s < 0
        ):
            raise ValueError("the actual context time cannot be negative")
        return self


def boundary_metadata(request):
    return {
        "train_id": request.train_id,
        "event_id": request.event_id,
        "source_port_id": request.source_port_id,
        "target_port_id": request.target_port_id,
        "station_basis": "stagnation_total_to_total",
        "fluid": request.fluid,
        "energy_reference": request.energy_reference,
        "clock_offset_s": request.clock_offset_s,
        "stages": [
            {
                "id": s.id,
                "shaft_id": s.shaft_id,
                "ports": [s.inlet_port_id, s.compressor_outlet_port_id, s.outlet_port_id],
                "map_digest": digest(s.performance_map),
                "cooler_digest": digest(s.intercooler),
            }
            for s in request.stages
        ],
    }


def _policy(q, c):
    if (q.asset_id, q.clock_id) != (c.asset_id, c.clock_id):
        raise ValueError("train and RunContext asset/clock differ")
    ids = [e.id for e in c.evidence]
    selected = set(q.evidence_ids)
    if len(ids) != len(set(ids)) or len(selected) != len(q.evidence_ids) or selected - set(ids):
        raise ValueError("train evidence must select unique actual RunContext members")
    used = set(q.review_evidence_ids.values())
    for stage in q.stages:
        used.add(stage.evidence_id)
        if stage.performance_map:
            used.add(stage.performance_map.evidence_id)
        if stage.intercooler:
            used.add(stage.intercooler.evidence_id)
    if used - selected:
        raise ValueError("all stage/map/cooling/review evidence must be selected")
    if c.metadata.get("compressor_train_boundary") != boundary_metadata(q):
        raise ValueError("context does not bind the train route, map, cooling and thermodynamic basis")
    missing = [
        "review and evidence required: " + key
        for key in REVIEWS
        if q.reviews.get(key) is not True or key not in q.review_evidence_ids
    ]
    for key in (
        "time_s",
        "source_port_id",
        "target_port_id",
        "fluid",
        "energy_reference",
        "inlet_total_pressure_pa_abs",
        "inlet_total_temperature_K",
        "mass_flow_kg_s",
    ):
        if getattr(q, key) is None:
            missing.append("required input unknown: " + key)
    if not q.owner_pins:
        missing.append("reviewed owner/runtime source pins required")
    points = 0
    for stage in q.stages:
        for key in (
            "inlet_port_id",
            "compressor_outlet_port_id",
            "outlet_port_id",
            "speed_rad_s",
            "mechanical_efficiency",
            "drive_efficiency",
            "performance_map",
            "intercooler",
        ):
            if getattr(stage, key) is None:
                missing.append(f"{stage.id}: unknown {key}")
        mp = stage.performance_map
        if mp:
            points += len(mp.speed_rad_s) * len(mp.mass_flow_kg_s)
            if (
                mp.fluid is None
                or mp.inlet_pressure_range_pa_abs is None
                or mp.inlet_temperature_range_K is None
                or any(
                    value is None
                    for values in (
                        *mp.pressure_ratio,
                        *mp.isentropic_efficiency,
                        mp.surge_flow_kg_s,
                        mp.choke_flow_kg_s,
                    )
                    for value in values
                )
            ):
                missing.append(stage.id + ": complete map values, fluid and applicability support required")
            if mp.coordinate_convention == "reference_corrected" and (
                mp.reference_pressure_pa_abs is None or mp.reference_temperature_K is None
            ):
                missing.append(
                    stage.id + ": corrected coordinates require an explicit absolute reference P/T"
                )
        cool = stage.intercooler
        if cool:
            keys = ["mode", "pressure_drop_pa"]
            if cool.mode in {"outlet_temperature", "heat_removal"}:
                keys += [
                    "sink_temperature_K",
                    "minimum_approach_K",
                    "available_heat_rejection_W",
                    "outlet_total_temperature_K" if cool.mode == "outlet_temperature" else "heat_removal_W",
                ]
            if any(getattr(cool, k) is None for k in keys):
                missing.append(stage.id + ": complete declared cooler boundary and capacity required")
    if points > q.maximum_map_points:
        missing.append("map work budget exceeded")
    return missing


def _early_scope(q):
    if q.energy_reference != f"CoolProp-HEOS:{q.fluid}:native":
        return "the exact inspected HEOS fluid/energy reference is required"
    if q.stages[0].inlet_port_id != q.source_port_id or q.stages[-1].outlet_port_id != q.target_port_id:
        return "train source/target ports do not bind the serial stage route"
    route = [q.source_port_id]
    shafts = {}
    for i, s in enumerate(q.stages):
        if s.performance_map.fluid != q.fluid:
            return "a map for another fluid cannot be transferred or relabelled"
        if s.inlet_port_id == s.compressor_outlet_port_id:
            return "a compressor must have distinct physical inlet and outlet ports"
        if i and q.stages[i - 1].outlet_port_id != s.inlet_port_id:
            return "interstage ports are not serially connected"
        route.append(s.compressor_outlet_port_id)
        if s.intercooler.mode == "none":
            if s.outlet_port_id != s.compressor_outlet_port_id:
                return "no-cooler outlet must preserve the compressor outlet node"
        else:
            if s.outlet_port_id in (s.inlet_port_id, s.compressor_outlet_port_id):
                return "a cooler needs its own downstream node"
            route.append(s.outlet_port_id)
        shaft = (s.speed_rad_s, s.drive_efficiency)
        if s.shaft_id in shafts and shafts[s.shaft_id] != shaft:
            return "one shaft must retain the same speed and drive-efficiency declaration"
        shafts[s.shaft_id] = shaft
    if len(set(route)) != len(route):
        return "the forward train route contains repeated nodes or a recycle loop"
    return None


def native_backend_metadata():
    """Explicit reviewed discovery; ordinary imports and missing-data gates avoid it."""
    import CoolProp.CoolProp as cp
    import lh2dt
    from scipy.interpolate import RegularGridInterpolator

    root = Path(lh2dt.__file__).parent
    tree = {p.relative_to(root).as_posix(): file_digest(p) for p in sorted(root.rglob("*.py"))}
    license_path = next((p / "LICENSE" for p in root.parents[:4] if (p / "LICENSE").is_file()), None)
    if license_path is None or "MIT License" not in license_path.read_text(encoding="utf-8")[:100]:
        raise ValueError("the inspected owner source requires its actual MIT license notice")
    dist = distribution("scipy")
    entries = {
        str(p).replace("\\", "/"): file_digest(Path(dist.locate_file(p)))
        for p in dist.files
        if str(p).replace("\\", "/").startswith("scipy/interpolate/")
        and str(p).endswith((".py", ".pyd", ".so"))
    }
    project_file = root.parent.parent / "pyproject.toml"
    owner_version = getattr(lh2dt, "__version__", None)
    if owner_version is None:
        owner_version = (
            tomllib.loads(project_file.read_text(encoding="utf-8"))["project"]["version"]
            if project_file.is_file()
            else version("lh2dt_models")
        )
    return {
        "owner": "lh2dt_models",
        "owner_version": owner_version,
        "owner_python_tree_sha256": digest(tree),
        "owner_python_files": len(tree),
        "owner_license_sha256": file_digest(license_path),
        "CoolProp_version": version("CoolProp"),
        "CoolProp_revision": cp.get_global_param_string("gitrevision"),
        "CoolProp_binary_sha256": file_digest(Path(cp.__file__)),
        "scipy_version": version("scipy"),
        "scipy_interpolate_tree_sha256": digest(entries),
        "scipy_interpolate_files": len(entries),
        "numpy_version": version("numpy"),
        "python_version": platform.python_version(),
        "shared_equipment_adapter_sha256": file_digest(Path(equipment_exchange.__file__)),
        "shared_contracts_sha256": file_digest(Path(contracts.__file__)),
        "shared_thermo_sha256": file_digest(Path(thermo.__file__)),
        "reference_anchors": thermo.coolprop_reference_anchors(),
        "interpolator": RegularGridInterpolator.__qualname__,
        "license": "owner MIT; CoolProp MIT; SciPy BSD",
    }


def _map_point(mp, stage, pressure, temperature, mass_flow, body):
    from scipy.interpolate import RegularGridInterpolator

    if (
        not mp.inlet_pressure_range_pa_abs[0] <= pressure <= mp.inlet_pressure_range_pa_abs[1]
        or not mp.inlet_temperature_range_K[0] <= temperature <= mp.inlet_temperature_range_K[1]
    ):
        raise ValueError("stage inlet total P/T lies outside the reviewed map applicability envelope")
    theta = 1.0 if mp.coordinate_convention == "actual" else temperature / mp.reference_temperature_K
    delta = 1.0 if mp.coordinate_convention == "actual" else pressure / mp.reference_pressure_pa_abs
    speed = stage.speed_rad_s / math.sqrt(theta)
    flow = mass_flow * math.sqrt(theta) / delta
    if (
        not mp.speed_rad_s[0] <= speed <= mp.speed_rad_s[-1]
        or not mp.mass_flow_kg_s[0] <= flow <= mp.mass_flow_kg_s[-1]
    ):
        raise ValueError("map speed/flow is outside supplied knots; extrapolation is refused")
    table = np.stack((np.array(mp.pressure_ratio), np.array(mp.isentropic_efficiency)), axis=-1)
    interpolation = RegularGridInterpolator(
        (mp.speed_rad_s, mp.mass_flow_kg_s), table, method="linear", bounds_error=True
    )
    boundaries = RegularGridInterpolator(
        (mp.speed_rad_s,),
        np.column_stack((mp.surge_flow_kg_s, mp.choke_flow_kg_s)),
        method="linear",
        bounds_error=True,
    )
    ratio, efficiency = map(float, interpolation([[speed, flow]])[0])
    surge, choke = map(float, boundaries([[speed]])[0])
    surge_margin = (flow - surge) / surge
    choke_margin = (choke - flow) / choke
    result = {
        "map_id": mp.id,
        "map_digest": digest(mp),
        "coordinate_convention": mp.coordinate_convention,
        "speed_coordinate_rad_s": speed,
        "flow_coordinate_kg_s": flow,
        "theta": theta,
        "delta": delta,
        "pressure_ratio": ratio,
        "isentropic_efficiency": efficiency,
        "surge_flow_coordinate_kg_s": surge,
        "choke_flow_coordinate_kg_s": choke,
        "surge_flow_margin_fraction": surge_margin,
        "choke_flow_margin_fraction": choke_margin,
        "surge_dynamics_executed": False,
    }
    body["native_calls"].append(
        {
            "stage_id": stage.id,
            "call": "scipy.interpolate.RegularGridInterpolator",
            "method": "linear",
            "bounds_error": True,
            "inputs": {"speed": speed, "flow": flow, "map_digest": digest(mp)},
            "result": result,
        }
    )
    if (
        not surge < flow < choke
        or surge_margin < stage.minimum_surge_flow_margin_fraction
        or choke_margin < stage.minimum_choke_flow_margin_fraction
    ):
        raise ValueError("static surge/choke boundary or caller-declared flow margin is not met")
    return result


def _gas(state):
    if state.quality is not None or state.phase not in {"gas", "supercritical_gas", "supercritical"}:
        raise ValueError(
            "all compressor and cooler boundaries must remain single-phase gas or reviewed supercritical gas"
        )


def _near(actual, expected, *, absolute=1e-6, relative=1e-10):
    return (
        math.isfinite(actual)
        and math.isfinite(expected)
        and math.isclose(actual, expected, abs_tol=absolute, rel_tol=relative)
    )


def _native(q, c, body):
    before = native_backend_metadata()
    body["backend"] = before
    if before != q.owner_pins:
        raise ValueError("reviewed owner/property/interpolation/runtime pins changed")
    client = LH2EquipmentClient(fluid=q.fluid, property_basis=q.energy_reference)

    def property_call(name, inputs, callback):
        receipt = {
            "call": name,
            "inputs": inputs,
            "fluid": q.fluid,
            "energy_reference": q.energy_reference,
            "result": None,
        }
        body["native_calls"].append(receipt)
        try:
            state = callback()
            receipt["result"] = asdict(state)
            return state
        except (ValueError, RuntimeError, ArithmeticError, TypeError) as exc:
            receipt["error"] = {"type": type(exc).__name__, "message": str(exc)}
            raise

    body["native_called"] = True
    body["property_called"] = True
    inlet = property_call(
        "LH2EquipmentClient.state",
        {"P_total_pa_abs": q.inlet_total_pressure_pa_abs, "T_total_K": q.inlet_total_temperature_K},
        lambda: client.state(
            StateRequest(
                fluid=q.fluid, inputs={"P": q.inlet_total_pressure_pa_abs, "T": q.inlet_total_temperature_K}
            )
        ),
    )
    _gas(inlet)
    first = inlet
    rows = []
    for stage in q.stages:
        point = _map_point(
            stage.performance_map, stage, inlet.pressure_Pa, inlet.temperature_K, q.mass_flow_kg_s, body
        )
        request = EquipmentRequest(
            model_id="compressor",
            parameters={
                "pressure_ratio": point["pressure_ratio"],
                "isentropic_efficiency": point["isentropic_efficiency"],
            },
            parameter_source=stage.performance_map.evidence_id,
        )
        handle = client.build(request, c)
        call = {
            "stage_id": stage.id,
            "call": "LH2EquipmentClient.build/compressor.evaluate",
            "build": handle.report.model_dump(mode="json"),
            "inputs": {"inlet_total_state": asdict(inlet), "mass_flow_kg_s": q.mass_flow_kg_s},
            "result": None,
        }
        body["native_calls"].append(call)
        if handle.report.status != "ready":
            raise ValueError("the owner compressor factory did not return a ready component")
        body["owner_called"] = True
        compressed = handle.native.evaluate(inlet, q.mass_flow_kg_s)
        call["result"] = asdict(compressed)
        hot = compressed.outlet
        _gas(hot)
        ideal = property_call(
            "HydrogenProperties.from_ps",
            {"P_total_pa_abs": hot.pressure_Pa, "Smass_J_kg_K": inlet.specific_entropy_J_kgK},
            lambda p=hot.pressure_Pa, s=inlet.specific_entropy_J_kgK: client.properties.from_ps(p, s),
        )
        _gas(ideal)
        gas_power = compressed.shaft_power_W
        expected_gas = q.mass_flow_kg_s * (hot.specific_enthalpy_J_kg - inlet.specific_enthalpy_J_kg)
        if (
            not gas_power > 0
            or not _near(gas_power, expected_gas)
            or not _near(hot.pressure_Pa, inlet.pressure_Pa * point["pressure_ratio"])
            or not _near(
                compressed.isentropic_power_W,
                q.mass_flow_kg_s * (ideal.specific_enthalpy_J_kg - inlet.specific_enthalpy_J_kg),
            )
            or not _near(compressed.isentropic_power_W, gas_power * point["isentropic_efficiency"])
        ):
            raise ValueError("owner stage pressure/enthalpy/isentropic-efficiency closure failed")
        cooler = stage.intercooler
        if cooler.mode == "none":
            outlet = hot
            rejected = 0.0
        else:
            pressure = hot.pressure_Pa - cooler.pressure_drop_pa
            if pressure <= 0:
                raise ValueError(
                    "declared cooler pressure loss removes the positive absolute outlet pressure"
                )
            if cooler.mode == "outlet_temperature":
                outlet = property_call(
                    "HydrogenProperties.from_pT",
                    {"P_total_pa_abs": pressure, "T_total_K": cooler.outlet_total_temperature_K},
                    lambda p=pressure, t=cooler.outlet_total_temperature_K: client.properties.from_pT(p, t),
                )
            else:
                enthalpy = hot.specific_enthalpy_J_kg - cooler.heat_removal_W / q.mass_flow_kg_s
                outlet = property_call(
                    "HydrogenProperties.from_ph",
                    {"P_total_pa_abs": pressure, "Hmass_J_kg": enthalpy},
                    lambda p=pressure, h=enthalpy: client.properties.from_ph(p, h),
                )
            _gas(outlet)
            rejected = q.mass_flow_kg_s * (hot.specific_enthalpy_J_kg - outlet.specific_enthalpy_J_kg)
            if rejected < 0 and not _near(rejected, 0.0):
                raise ValueError("a declared cooler would require net heating")
            if rejected < 0:
                raise ValueError("negative roundoff duty is unresolved; no clamping is applied")
            if cooler.mode == "heat_removal" and not _near(rejected, cooler.heat_removal_W):
                raise ValueError("cooler duty and enthalpy change do not close")
            if rejected > cooler.available_heat_rejection_W and not _near(
                rejected, cooler.available_heat_rejection_W
            ):
                raise ValueError("declared cooling capacity is insufficient")
            if rejected > 0 and (
                cooler.sink_temperature_K >= hot.temperature_K
                or outlet.temperature_K < cooler.sink_temperature_K + cooler.minimum_approach_K
            ):
                raise ValueError("cooler sink temperature or minimum approach is not feasible")
        shaft = gas_power / stage.mechanical_efficiency
        electrical = shaft / stage.drive_efficiency
        if not all(math.isfinite(v) for v in (shaft, electrical, rejected)):
            raise ValueError("stage power/cooling budget is not numerically representable")
        row = {
            "stage_id": stage.id,
            "shaft_id": stage.shaft_id,
            "ports": [stage.inlet_port_id, stage.compressor_outlet_port_id, stage.outlet_port_id],
            "inlet_total_state": asdict(inlet),
            "compressor_outlet_total_state": asdict(hot),
            "outlet_total_state": asdict(outlet),
            "ideal_outlet_total_state": asdict(ideal),
            "map_point": point,
            "owner_result": asdict(compressed),
            "mass_in_kg_s": q.mass_flow_kg_s,
            "mass_out_kg_s": q.mass_flow_kg_s,
            "gas_power_W": gas_power,
            "shaft_input_power_W": shaft,
            "electrical_input_power_W": electrical,
            "mechanical_loss_to_surroundings_W": shaft - gas_power,
            "drive_loss_to_surroundings_W": electrical - shaft,
            "shaft_torque_N_m": shaft / stage.speed_rad_s,
            "heat_rejected_W": rejected,
            "cooler_pressure_drop_pa": hot.pressure_Pa - outlet.pressure_Pa,
            "stage_energy_residual_W": gas_power
            - rejected
            - q.mass_flow_kg_s * (outlet.specific_enthalpy_J_kg - inlet.specific_enthalpy_J_kg),
            "cooler_mode": cooler.mode,
        }
        body["native_calls"].append(
            {
                "stage_id": stage.id,
                "call": "owner.properties cooler boundary and isentropic audit",
                "cooler_input": cooler.model_dump(mode="json"),
                "ideal_outlet": asdict(ideal),
                "cooler_outlet": asdict(outlet),
            }
        )
        rows.append(row)
        body["stage_prefix"] = rows
        inlet = outlet
    values = {
        key: math.fsum(r[key] for r in rows)
        for key in (
            "gas_power_W",
            "shaft_input_power_W",
            "electrical_input_power_W",
            "mechanical_loss_to_surroundings_W",
            "drive_loss_to_surroundings_W",
            "heat_rejected_W",
        )
    }
    dh = q.mass_flow_kg_s * (inlet.specific_enthalpy_J_kg - first.specific_enthalpy_J_kg)
    gas_residual = values["gas_power_W"] - values["heat_rejected_W"] - dh
    overall = (
        values["electrical_input_power_W"]
        - values["mechanical_loss_to_surroundings_W"]
        - values["drive_loss_to_surroundings_W"]
        - values["heat_rejected_W"]
        - dh
    )
    tolerance = 1e-5 + 1e-10 * max(abs(values["electrical_input_power_W"]), abs(dh))
    within = (
        abs(gas_residual) <= tolerance
        and abs(overall) <= tolerance
        and all(abs(r["stage_energy_residual_W"]) <= tolerance for r in rows)
    )
    body["audits"] = {
        "gas_energy_residual_W": gas_residual,
        "overall_energy_residual_W": overall,
        "energy_tolerance_W": tolerance,
        "within_energy_budget": within,
        "mass_residual_kg_s": 0.0,
        "storage_assumption": "steady, no side streams or accumulation",
        "pressure_ledger": [
            {
                "stage_id": r["stage_id"],
                "inlet_total_Pa": r["inlet_total_state"]["pressure_Pa"],
                "compressed_total_Pa": r["compressor_outlet_total_state"]["pressure_Pa"],
                "outlet_total_Pa": r["outlet_total_state"]["pressure_Pa"],
            }
            for r in rows
        ],
    }
    if not within:
        raise ValueError("the assembled gas/electrical/cooling energy budget does not close")
    if native_backend_metadata() != before:
        raise ValueError("native source/runtime changed during train evaluation")
    body["values"] = {
        **values,
        "mass_flow_kg_s": q.mass_flow_kg_s,
        "inlet_total_state": asdict(first),
        "outlet_total_state": asdict(inlet),
        "total_pressure_ratio": inlet.pressure_Pa / first.pressure_Pa,
        "minimum_surge_flow_margin_fraction": min(r["map_point"]["surge_flow_margin_fraction"] for r in rows),
        "minimum_choke_flow_margin_fraction": min(r["map_point"]["choke_flow_margin_fraction"] for r in rows),
        "stages": rows,
    }
    body.update(status="computed_diagnostic", notes=[])


def evaluate_compressor_train(request: CompressorTrainRequest, context: RunContext):
    q = CompressorTrainRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    body = {
        "schema": SCHEMA,
        "request": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "input_digest": digest({"request": q, "context": c}),
        "status": "needs_data",
        "backend": None,
        "native_called": False,
        "owner_called": False,
        "property_called": False,
        "values": None,
        "audits": None,
        "stage_prefix": [],
        "native_calls": [],
        "implementation_sha256": file_digest(Path(__file__)),
        "physical_validation_claim": False,
        "surge_dynamics_executed": False,
        "qualification": "steady total-state map/energy diagnostic; no dynamic surge, anti-surge control, rotor or field-map qualification",
    }
    pending = _policy(q, c)
    if pending:
        body["notes"] = pending
    elif outside := _early_scope(q):
        body.update(status="out_of_scope", notes=[outside])
    else:
        try:
            _native(q, c, body)
        except (ValueError, RuntimeError, ArithmeticError, TypeError, ImportError, KeyError) as exc:
            body.update(status="out_of_scope", notes=[f"{type(exc).__name__}: {exc}"])
    body = json_value(body)
    body["result_digest"] = digest(body)
    return body


def verify_compressor_train_record(record, *, request=None, context=None):
    """Current-source replay checks physics and boundaries; hashes are not signatures."""
    try:
        json_value(record)
        if record["schema"] != SCHEMA or record["result_digest"] != digest(
            {k: v for k, v in record.items() if k != "result_digest"}
        ):
            raise ValueError("train receipt changed")
        q = CompressorTrainRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
        c = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
        if (request is not None and q != request) or (context is not None and c != context):
            raise ValueError("expected train request/context differs")
        if evaluate_compressor_train(q, c) != record:
            raise ValueError("native map/state/power/cooling/qualification differs on replay")
        return {
            "valid": True,
            "meaning": "unsigned current-source numerical replay, not historical execution or approval",
        }
    except (ValueError, TypeError, KeyError, AttributeError, ImportError, OSError) as exc:
        return {"valid": False, "reason": str(exc)}


def to_signal_frame(record, context: RunContext):
    if (
        record.get("status") != "computed_diagnostic"
        or not verify_compressor_train_record(record, context=context)["valid"]
        or record["audits"]["within_energy_budget"] is not True
    ):
        raise ValueError("a complete verified, conserved train snapshot with matching context is required")
    q = CompressorTrainRequest.model_validate_json(json.dumps(record["request"]))
    result = record["values"]
    values = {
        k: result[k]
        for k in (
            "mass_flow_kg_s",
            "gas_power_W",
            "shaft_input_power_W",
            "electrical_input_power_W",
            "heat_rejected_W",
        )
    }
    values.update(
        outlet_total_pressure_pa_abs=result["outlet_total_state"]["pressure_Pa"],
        outlet_total_temperature_K=result["outlet_total_state"]["temperature_K"],
        minimum_surge_flow_margin_fraction=result["minimum_surge_flow_margin_fraction"],
    )
    units = {
        k: "kg/s"
        if k == "mass_flow_kg_s"
        else "Pa"
        if k == "outlet_total_pressure_pa_abs"
        else "K"
        if k == "outlet_total_temperature_K"
        else "1"
        if k == "minimum_surge_flow_margin_fraction"
        else "W"
        for k in values
    }
    return SignalFrame(
        stream_id=f"{q.asset_id}/{q.train_id}/{q.event_id}",
        clock_id=q.clock_id,
        time_s=q.time_s + q.clock_offset_s,
        values=values,
        units=units,
        valid=dict.fromkeys(values, True),
        origin="synthetic" if context.origin == "synthetic" else "simulation",
        operating_mode="steady-total-state-compressor-train",
    )
