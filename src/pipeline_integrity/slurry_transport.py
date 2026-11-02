"""Conditional homogeneous slurry transport using existing numerical/hydraulic owners.

Only a minimum fully-developed laminar constitutive closure is supplied for HB.
Deposition, heterogeneous transport and wear correlations are not inferred.
"""

from __future__ import annotations

import inspect
import json
import math
import warnings
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Fraction, Name, Nonnegative, Positive, RunContext, digest, json_value
from .datasets import file_digest
from .models import hydraulics

REVIEWS = (
    "rheology_and_temperature_shear_domain",
    "particle_concentration_and_homogeneous_support",
    "laminar_no_slip_or_newtonian_owner_domain",
    "numerical_and_pressure_work_qualification",
)


class SlurryPropertySnapshot(Contract):
    provider_id: Name
    provider_version: Name
    source_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reference_basis: Name
    rheology: Literal["newtonian", "bingham", "power_law", "herschel_bulkley"]
    consistency_Pa_s_n: Positive
    flow_index: Positive
    yield_stress_Pa: Nonnegative
    mixture_density_kg_m3: Positive
    temperature_K: Positive
    temperature_support_K: tuple[Positive, Positive]
    maximum_shear_rate_s_inv: Positive
    maximum_shear_stress_Pa: Positive
    solid_volume_fraction: Fraction | None
    concentration_support: tuple[Fraction, Fraction]
    particle_diameter_m: Positive | None
    maximum_particle_to_pipe_diameter_ratio: Positive | None
    carrier_density_kg_m3: Positive | None
    particle_density_kg_m3: Positive | None
    maximum_density_mixing_error_kg_m3: Nonnegative
    homogeneous_condition: Literal["supported", "unsupported", "unknown"]
    settling_condition: Literal[
        "supported_homogeneous_operating_window", "deposition_or_segregation", "unknown"
    ]
    supported_absolute_velocity_m_s: tuple[Nonnegative, Positive] | None
    flow_regime: Literal["laminar_supplied", "newtonian_owner", "unknown"]
    evidence_id: Name

    @model_validator(mode="after")
    def constitutive(self):
        if (
            self.temperature_support_K[0] >= self.temperature_support_K[1]
            or self.concentration_support[0] > self.concentration_support[1]
        ):
            raise ValueError("property support ranges must be ordered")
        if (
            self.supported_absolute_velocity_m_s
            and self.supported_absolute_velocity_m_s[0] >= self.supported_absolute_velocity_m_s[1]
        ):
            raise ValueError("velocity support range must be ordered")
        if self.rheology == "newtonian" and (self.flow_index != 1 or self.yield_stress_Pa != 0):
            raise ValueError("Newtonian limit requires n=1 and zero yield stress")
        if self.rheology == "bingham" and self.flow_index != 1:
            raise ValueError("Bingham limit requires n=1")
        if self.rheology == "power_law" and self.yield_stress_Pa != 0:
            raise ValueError("power-law limit requires zero yield stress")
        if self.maximum_shear_stress_Pa < self.yield_stress_Pa:
            raise ValueError("declared stress support cannot end below the supplied yield stress")
        return self


class SlurryTransportRequest(Contract):
    id: Name
    asset_id: Name
    event_id: Name
    clock_id: Name
    time_s: Nonnegative
    inlet_port_id: Name
    outlet_port_id: Name
    inlet_pressure_pa_abs: Positive
    volume_flow_m3_s: float
    length_m: Positive
    inner_diameter_m: Positive
    roughness_m: Nonnegative
    minor_loss_coefficient: Nonnegative
    outlet_minus_inlet_elevation_m: float
    gravity_m_s2: Positive
    properties: SlurryPropertySnapshot
    geometry_evidence_id: Name
    maximum_wall_stress_bracket_Pa: Positive
    quadrature_absolute_error_m3_s: Positive = 1e-12
    quadrature_relative_error: Positive = Field(default=1e-8, ge=1e-12, le=1e-3)
    quadrature_subinterval_limit: int = Field(default=100, ge=1, le=1000)
    root_absolute_stress_tolerance_Pa: Positive = 1e-8
    root_relative_stress_tolerance: Positive = Field(default=1e-10, ge=1e-14, le=1e-3)
    root_maximum_iterations: int = Field(default=100, ge=1, le=1000)
    flow_balance_absolute_m3_s: Nonnegative = 1e-10
    balance_relative_tolerance: Nonnegative = Field(default=1e-7, le=1e-3)
    energy_rate_balance_absolute_W: Nonnegative = 1e-7
    transition_policy: Literal["withhold", "use_declared_correlation"] = "withhold"
    friction_method: Literal["Clamond", "Colebrook", "Haaland", "Swamee_Jain_1976"] = "Clamond"
    evidence_ids: tuple[Name, ...] = Field(min_length=1)
    reviews: dict[str, bool] = Field(default_factory=lambda: dict.fromkeys(REVIEWS, False))
    review_evidence_ids: dict[str, Name] = Field(default_factory=dict)
    owner_pins: dict = Field(default_factory=dict)
    radial_positions_fraction: tuple[Fraction, ...] | None = Field(default=None, max_length=1_000_000)

    @model_validator(mode="after")
    def contract(self):
        if self.inlet_port_id == self.outlet_port_id:
            raise ValueError("pressure ports must differ")
        if len(set(self.evidence_ids)) != len(self.evidence_ids) or set(self.reviews) != set(REVIEWS):
            raise ValueError("unique evidence IDs and all named reviews are required")
        return self


def boundary_metadata(q):
    return {
        "asset_id": q.asset_id,
        "event_id": q.event_id,
        "clock_id": q.clock_id,
        "time_s": q.time_s,
        "inlet_port_id": q.inlet_port_id,
        "outlet_port_id": q.outlet_port_id,
        "pressure_basis": "absolute Pa; signed Q positive inlet to outlet",
        "request_digest": digest(q),
    }


def native_backend_metadata():
    from fluids import friction
    from scipy.integrate import _quadpack, quad
    from scipy.optimize import _zeros, brentq

    return {
        "scipy_version": version("scipy"),
        "fluids_version": version("fluids"),
        "scipy_license": "BSD-3-Clause",
        "fluids_license": "MIT",
        "quad_source_sha256": file_digest(Path(inspect.getfile(quad))),
        "quadpack_binary_sha256": file_digest(Path(_quadpack.__file__)),
        "brentq_source_sha256": file_digest(Path(inspect.getfile(brentq))),
        "root_binary_sha256": file_digest(Path(_zeros.__file__)),
        "friction_source_sha256": file_digest(Path(friction.__file__)),
        "hydraulic_owner_sha256": file_digest(Path(hydraulics.__file__)),
        "implementation_sha256": file_digest(Path(__file__)),
        "closure": "steady no-slip fully-developed laminar HB radial stress/flow integral; no transition correlation",
    }


def _policy(q, c):
    if (q.asset_id, q.event_id, q.clock_id) != (c.asset_id, c.scenario_id, c.clock_id) or c.metadata.get(
        "slurry_transport_boundary"
    ) != boundary_metadata(q):
        raise ValueError("actual asset/event/clock/ports/provider request context differs")
    available = [e.id for e in c.evidence]
    selected = set(q.evidence_ids)
    used = {q.geometry_evidence_id, q.properties.evidence_id, *q.review_evidence_ids.values()}
    if len(set(available)) != len(available) or selected - set(available) or used - selected:
        raise ValueError("property/geometry/review evidence must select actual unique context evidence")
    property_evidence = next(e for e in c.evidence if e.id == q.properties.evidence_id)
    if property_evidence.sha256 != q.properties.source_sha256:
        raise ValueError("provider source checksum differs from actual selected property evidence")
    notes = ["review:" + k for k in REVIEWS if not q.reviews[k] or k not in q.review_evidence_ids]
    if not q.owner_pins:
        notes.append("reviewed native owner/runtime/source pins")
    p = q.properties
    for field in ("solid_volume_fraction", "supported_absolute_velocity_m_s"):
        if getattr(p, field) is None:
            notes.append("missing:" + field)
    if p.solid_volume_fraction and any(
        getattr(p, k) is None
        for k in (
            "particle_diameter_m",
            "maximum_particle_to_pipe_diameter_ratio",
            "carrier_density_kg_m3",
            "particle_density_kg_m3",
        )
    ):
        notes.append("particle/density/domain support for nonzero solids")
    return notes


def _domain(q):
    p = q.properties
    velocity = abs(q.volume_flow_m3_s) / (math.pi * q.inner_diameter_m**2 / 4)
    if (
        p.homogeneous_condition != "supported"
        or p.settling_condition != "supported_homogeneous_operating_window"
    ):
        raise ValueError(
            "homogeneous/nondepositing support is unknown or failed; no deposition model is supplied"
        )
    if (
        not p.temperature_support_K[0] <= p.temperature_K <= p.temperature_support_K[1]
        or not p.concentration_support[0] <= p.solid_volume_fraction <= p.concentration_support[1]
    ):
        raise ValueError("temperature or concentration exceeds supplied rheology support")
    if not p.supported_absolute_velocity_m_s[0] <= velocity <= p.supported_absolute_velocity_m_s[1]:
        raise ValueError("velocity exceeds supplied homogeneous/flow-regime window")
    if p.solid_volume_fraction:
        if p.particle_diameter_m / q.inner_diameter_m > p.maximum_particle_to_pipe_diameter_ratio:
            raise ValueError("particle-to-pipe scale exceeds supplied continuum support")
        mixed = (
            1 - p.solid_volume_fraction
        ) * p.carrier_density_kg_m3 + p.solid_volume_fraction * p.particle_density_kg_m3
        if abs(mixed - p.mixture_density_kg_m3) > p.maximum_density_mixing_error_kg_m3:
            raise ValueError("supplied bulk density and volume-fraction density ledger disagree")
    if p.rheology == "newtonian":
        if p.flow_regime != "newtonian_owner":
            raise ValueError(
                "equivalent Newtonian mode requires an explicitly reviewed existing-owner flow domain"
            )
    elif p.flow_regime != "laminar_supplied" or q.roughness_m != 0 or q.minor_loss_coefficient != 0:
        raise ValueError(
            "HB closure requires supplied laminar, ideal smooth/no-slip and no unqualified local-loss model"
        )


def _radial_velocity_profile(q,wall_tau,native):
    """Integrate the existing solved constitutive wall shear, without a new Q solver.

    Radial coordinates are fractions of radius measured from the axis. Caller
    ordering and repeats are retained. None omits the profile; an explicit empty
    tuple requests only axis/plug values. This is a fully developed no-slip
    laminar profile and does not describe entrance/fitting or turbulent fields.
    """
    positions=q.radial_positions_fraction
    profile=dict(status="not_requested",radial_positions_fraction=None,velocity_m_s=None,
        axis_velocity_m_s=None,plug_velocity_m_s=None,plug_radius_fraction=None,notes=[])
    if positions is None:return profile
    profile["radial_positions_fraction"]=positions
    p=q.properties
    if q.volume_flow_m3_s==0:
        profile.update(status="computed_laminar",velocity_m_s=tuple(0. for _ in positions),
            axis_velocity_m_s=0.,plug_velocity_m_s=0. if p.yield_stress_Pa>0 else None,
            plug_radius_fraction=1. if p.yield_stress_Pa>0 else 0.)
        return profile
    if native is not None:
        reynolds=native.values.get("reynolds")
        if reynolds is None or not math.isfinite(reynolds) or reynolds>=2300:
            profile.update(status="out_of_scope",notes=[
                "Newtonian owner is not in the declared fully developed laminar subdomain; bulk result is retained"])
            return profile
    try:
        excess=wall_tau-p.yield_stress_Pa
        if wall_tau<=0 or excess<=0:
            raise ValueError("wall stress does not resolve a yielded laminar profile")
        sign=1. if q.volume_flow_m3_s>0 else -1.
        n=p.flow_index
        wall_rate=(excess/p.consistency_Pa_s_n)**(1/n)
        axis=sign*(q.inner_diameter_m/2)*(excess/wall_tau)*wall_rate*(n/(n+1))
        if not math.isfinite(axis) or axis==0:
            raise ValueError("laminar velocity exceeds finite nonzero numerical support")
        velocities=[]
        for position in positions:
            distance_ratio=wall_tau*(1-position)/excess
            if distance_ratio>=1:factor=1.
            else:factor=-math.expm1((1+1/n)*math.log1p(-distance_ratio))
            velocity=axis*factor
            if not math.isfinite(velocity):raise ValueError("nonfinite radial velocity")
            velocities.append(velocity)
        profile.update(status="computed_laminar",velocity_m_s=tuple(velocities),axis_velocity_m_s=axis,
            plug_velocity_m_s=axis if p.yield_stress_Pa>0 else None,
            plug_radius_fraction=p.yield_stress_Pa/wall_tau)
    except (ValueError,OverflowError,ZeroDivisionError) as exc:
        profile.update(status="out_of_scope",notes=[str(exc)])
    return profile


def _constitutive_flow_at_stress(tau, radius, properties, absolute_error, relative_error, limit):
    """Shared no-slip radial integral for the declared yield/power-law material."""
    from scipy.integrate import IntegrationWarning, quad
    p = properties
    if tau <= p.yield_stress_Pa:
        return 0.0, 0.0
    excess = tau - p.yield_stress_Pa
    lower = p.yield_stress_Pa / tau
    width = excess / tau
    # Map the yielded annulus to [0,1]. All dimensional magnitudes stay in a
    # logarithmic scale until the complete flow is reconstructed, so R³ cannot
    # overflow before a small constitutive shear rate compensates it.
    power_log = (math.log(excess) - math.log(p.consistency_Pa_s_n)) / p.flow_index
    log_scale = math.log(math.pi) + 3*math.log(radius) + power_log + math.log(width)
    if not math.isfinite(log_scale):
        raise ValueError('constitutive flow scale exceeds finite numerical support')
    exponent = 1 / p.flow_index
    def integrand(u):
        return u**exponent * (lower + width*u)**2
    log_tolerance = math.log(absolute_error) - log_scale
    scaled_absolute = .25 if log_tolerance >= math.log(.25) else (
        0. if log_tolerance < math.log(math.ulp(0.)) else math.exp(log_tolerance))
    with warnings.catch_warnings():
        warnings.simplefilter('error', IntegrationWarning)
        shape, shape_error = quad(integrand, 0., 1., epsabs=scaled_absolute,
                            epsrel=relative_error, limit=limit)
    if not math.isfinite(shape) or shape <= 0:
        raise ValueError('nonzero constitutive integral is below resolved numerical support')
    log_flow = log_scale + math.log(shape)
    if not math.log(math.ulp(0.)) <= log_flow <= math.log(float.fromhex('0x1.fffffffffffffp1023')):
        raise ValueError('nonzero constitutive flow exceeds finite floating-point support')
    value = math.exp(log_flow)
    error = value * (shape_error / shape)
    if not math.isfinite(value) or not math.isfinite(error):
        raise ValueError('radial flow integral is not finite')
    return value, error


class LaminarPressureFlowRequest(Contract):
    properties: SlurryPropertySnapshot
    inner_diameter_m: Positive
    length_m: Positive
    driving_pressure_difference_Pa: float
    radial_positions_fraction: tuple[Fraction, ...] = ()
    laminar_homogeneous_no_slip_domain_reviewed: bool = False
    absolute_flow_tolerance_m3_s: Positive = 1e-12
    relative_flow_tolerance: float = Field(default=1e-8, ge=1e-13, lt=1)
    quadrature_subinterval_limit: int = Field(default=100, ge=2, le=10000)


class LaminarPressureFlowResult(Contract):
    signed_volume_flow_m3_s: float
    signed_mean_velocity_m_s: float
    wall_shear_stress_magnitude_Pa: Nonnegative
    plug_radius_fraction: Fraction
    radial_positions_fraction: tuple[Fraction, ...]
    axial_velocity_m_s: tuple[float, ...]
    dissipated_power_W: Nonnegative
    absolute_quadrature_error_m3_s: Nonnegative


def evaluate_laminar_pressure_flow(request: LaminarPressureFlowRequest) -> LaminarPressureFlowResult:
    """Expose the same radial constitutive integral in pressure-driven form.

    The pressure difference drives fully developed straight-pipe friction only;
    subtract gravity and fitting losses before supplying this coordinate.
    """
    from types import SimpleNamespace
    if not isinstance(request, LaminarPressureFlowRequest):
        raise TypeError('LaminarPressureFlowRequest required')
    q = LaminarPressureFlowRequest.model_validate(request.model_dump())
    p = q.properties
    if not q.laminar_homogeneous_no_slip_domain_reviewed:
        raise ValueError('review the laminar homogeneous no-slip constitutive domain')
    if p.homogeneous_condition != 'supported' or p.settling_condition != 'supported_homogeneous_operating_window':
        raise ValueError('homogeneous operating support is required')
    if not p.temperature_support_K[0] <= p.temperature_K <= p.temperature_support_K[1]:
        raise ValueError('material temperature exceeds its supplied support')
    if p.solid_volume_fraction is None or not p.concentration_support[0] <= p.solid_volume_fraction <= p.concentration_support[1]:
        raise ValueError('solid concentration and its supported range are required')
    required_regime = 'newtonian_owner' if p.rheology == 'newtonian' else 'laminar_supplied'
    if p.flow_regime != required_regime or p.supported_absolute_velocity_m_s is None:
        raise ValueError('explicit constitutive flow regime and velocity support are required')
    if p.solid_volume_fraction:
        if any(v is None for v in (p.particle_diameter_m, p.maximum_particle_to_pipe_diameter_ratio,
                                    p.carrier_density_kg_m3, p.particle_density_kg_m3)):
            raise ValueError('particle geometry and constituent densities are required for a suspension')
        mixed_density = (1 - p.solid_volume_fraction) * p.carrier_density_kg_m3 + p.solid_volume_fraction * p.particle_density_kg_m3
        if abs(mixed_density - p.mixture_density_kg_m3) > p.maximum_density_mixing_error_kg_m3:
            raise ValueError('constituent and bulk density ledger disagree')
    if p.particle_diameter_m is not None:
        if p.maximum_particle_to_pipe_diameter_ratio is None or p.particle_diameter_m / q.inner_diameter_m > p.maximum_particle_to_pipe_diameter_ratio:
            raise ValueError('particle size exceeds the reviewed homogeneous pipe support')
    tau = abs(q.driving_pressure_difference_Pa) * q.inner_diameter_m / (4 * q.length_m)
    if not math.isfinite(tau) or tau > p.maximum_shear_stress_Pa:
        raise ValueError('wall shear stress exceeds supplied material support')
    shear = (max(0., tau - p.yield_stress_Pa) / p.consistency_Pa_s_n)**(1 / p.flow_index)
    if not math.isfinite(shear) or shear > p.maximum_shear_rate_s_inv:
        raise ValueError('wall shear rate exceeds supplied material support')
    magnitude, error = _constitutive_flow_at_stress(tau, q.inner_diameter_m / 2, p,
        q.absolute_flow_tolerance_m3_s, q.relative_flow_tolerance, q.quadrature_subinterval_limit)
    if error > max(q.absolute_flow_tolerance_m3_s, q.relative_flow_tolerance * magnitude):
        raise ValueError('constitutive flow quadrature did not reach the requested tolerance')
    flow = math.copysign(magnitude, q.driving_pressure_difference_Pa)
    velocity = flow / (math.pi * q.inner_diameter_m**2 / 4)
    if p.supported_absolute_velocity_m_s and not p.supported_absolute_velocity_m_s[0] <= abs(velocity) <= p.supported_absolute_velocity_m_s[1]:
        raise ValueError('computed flow exceeds the supplied velocity window')
    if p.rheology == 'newtonian' and p.mixture_density_kg_m3 * abs(velocity) * q.inner_diameter_m / p.consistency_Pa_s_n >= 2300:
        raise ValueError('Newtonian result is outside the selected laminar regime')
    profile = _radial_velocity_profile(SimpleNamespace(properties=p, volume_flow_m3_s=flow,
        inner_diameter_m=q.inner_diameter_m, radial_positions_fraction=q.radial_positions_fraction), tau, None)
    if profile['status'] != 'computed_laminar':
        raise ValueError('radial velocity profile is outside finite numerical support')
    power = flow * q.driving_pressure_difference_Pa
    if not all(math.isfinite(v) for v in (flow, velocity, power)):
        raise ValueError('laminar flow exceeds finite numerical support')
    return LaminarPressureFlowResult(signed_volume_flow_m3_s=flow,
        signed_mean_velocity_m_s=velocity, wall_shear_stress_magnitude_Pa=tau,
        plug_radius_fraction=profile['plug_radius_fraction'],
        radial_positions_fraction=q.radial_positions_fraction,
        axial_velocity_m_s=profile['velocity_m_s'], dissipated_power_W=power,
        absolute_quadrature_error_m3_s=error)


def _solve(q, body):
    from scipy.optimize import brentq

    _domain(q)
    p = q.properties
    radius = q.inner_diameter_m / 2
    target = abs(q.volume_flow_m3_s)
    sign = 1 if q.volume_flow_m3_s > 0 else -1 if q.volume_flow_m3_s < 0 else 0
    area = math.pi * radius**2
    native = None
    root_iterations = 0
    quadrature_calls = 0

    def flow_at_stress(tau):
        nonlocal quadrature_calls
        quadrature_calls += 1
        return _constitutive_flow_at_stress(tau, radius, p,
            q.quadrature_absolute_error_m3_s, q.quadrature_relative_error,
            q.quadrature_subinterval_limit)

    if p.rheology == "newtonian":
        native = hydraulics.liquid_pressure_loss(
            hydraulics.LiquidPressureLoss(
                diameter_m=q.inner_diameter_m,
                length_m=q.length_m,
                roughness_m=q.roughness_m,
                volume_flow_m3_s=q.volume_flow_m3_s,
                density_kg_m3=p.mixture_density_kg_m3,
                viscosity_pa_s=p.consistency_Pa_s_n,
                minor_loss_coefficient=q.minor_loss_coefficient,
                outlet_minus_inlet_elevation_m=q.outlet_minus_inlet_elevation_m,
                gravity_m_s2=q.gravity_m_s2,
                transition_policy=q.transition_policy,
                friction_method=q.friction_method,
            )
        )
        if native.status != "computed":
            raise ValueError("Newtonian owner withheld result: " + "; ".join(native.notes))
        friction_drop = abs(native.values["dissipative_pressure_loss_pa"])
        # Fully-developed wall shear excludes supplied fitting losses.
        wall_tau = native.values["darcy_friction_factor"] * p.mixture_density_kg_m3 * (target / area) ** 2 / 8
        solved_flow = target
        error = 0.0
    elif target == 0:
        wall_tau = friction_drop = solved_flow = error = 0.0
    else:
        upper = min(
            q.maximum_wall_stress_bracket_Pa,
            p.maximum_shear_stress_Pa,
            p.yield_stress_Pa + p.consistency_Pa_s_n * p.maximum_shear_rate_s_inv**p.flow_index,
        )
        if upper <= p.yield_stress_Pa:
            raise ValueError("reviewed stress bracket cannot yield the material")
        maxflow, _ = flow_at_stress(upper)
        if maxflow < target:
            raise ValueError("specified flow exceeds reviewed wall-stress bracket")
        if maxflow == target:
            wall_tau = upper
        else:
            wall_tau, root = brentq(
                lambda tau: flow_at_stress(tau)[0] - target,
                p.yield_stress_Pa,
                upper,
                xtol=q.root_absolute_stress_tolerance_Pa,
                rtol=q.root_relative_stress_tolerance,
                maxiter=q.root_maximum_iterations,
                full_output=True,
                disp=True,
            )
            if not root.converged:
                raise ValueError("native wall-stress root did not converge")
            root_iterations = int(root.iterations)
        if wall_tau <= p.yield_stress_Pa:
            raise ValueError("nonzero flow cannot be resolved above the yield threshold")
        solved_flow, error = flow_at_stress(wall_tau)
        friction_drop = 4 * q.length_m * wall_tau / q.inner_diameter_m
    shear_rate = (
        0.0 if target == 0 else ((wall_tau - p.yield_stress_Pa) / p.consistency_Pa_s_n) ** (1 / p.flow_index)
    )
    if wall_tau > p.maximum_shear_stress_Pa or shear_rate > p.maximum_shear_rate_s_inv:
        raise ValueError("wall rheology exceeds supplied stress/shear-rate support")
    static = p.mixture_density_kg_m3 * q.gravity_m_s2 * q.outlet_minus_inlet_elevation_m
    signed_drop = sign * friction_drop + static
    pout = q.inlet_pressure_pa_abs - signed_drop
    zero_yield_ambiguity = target == 0 and p.yield_stress_Pa > 0
    yield_pressure_span = 4 * q.length_m * p.yield_stress_Pa / q.inner_diameter_m
    zero_flow_pressure_interval = (
        (pout - yield_pressure_span, pout + yield_pressure_span) if zero_yield_ambiguity else None
    )
    if (zero_yield_ambiguity and zero_flow_pressure_interval[1] <= 0) or (
        not zero_yield_ambiguity and pout <= 0
    ):
        raise ValueError("predicted outlet absolute pressure is nonpositive")
    pressure_power = q.volume_flow_m3_s * signed_drop
    potential_power = q.volume_flow_m3_s * static
    dissipated = target * friction_drop
    balance = pressure_power - potential_power - dissipated
    flow_residual = solved_flow - target
    flow_limit = q.flow_balance_absolute_m3_s + q.balance_relative_tolerance * target
    energy_limit = q.energy_rate_balance_absolute_W + q.balance_relative_tolerance * max(
        abs(pressure_power), abs(potential_power), abs(dissipated)
    )
    valid = abs(flow_residual) <= flow_limit and error <= flow_limit and abs(balance) <= energy_limit
    body.update(
        status="computed_diagnostic" if valid else "diagnostic_rejected",
        notes=[] if valid else ["native flow quadrature/root or pressure-work budget exceeded"],
        native_newtonian=None if native is None else native.model_dump(mode="json"),
        values={
            "outlet_pressure_pa_abs": None if zero_yield_ambiguity else pout,
            "required_inlet_minus_outlet_Pa": None if zero_yield_ambiguity else signed_drop,
            "pressure_prediction_unique": not zero_yield_ambiguity,
            "minimal_branch_reference_outlet_pressure_pa_abs": pout
            if zero_yield_ambiguity and pout > 0
            else None,
            "minimal_branch_formal_outlet_pressure_Pa": pout if zero_yield_ambiguity else None,
            "zero_flow_formal_outlet_pressure_interval_Pa": zero_flow_pressure_interval,
            "zero_flow_positive_absolute_pressure_branch_exists": True if zero_yield_ambiguity else None,
            "minimal_branch_reference_inlet_minus_outlet_Pa": signed_drop if zero_yield_ambiguity else None,
            "signed_friction_pressure_drop_Pa": sign * friction_drop,
            "static_pressure_change_Pa": static,
            "wall_shear_stress_Pa": wall_tau,
            "wall_shear_rate_s_inv": shear_rate,
            "plug_radius_fraction": None
            if p.rheology == "newtonian"
            else (
                1.0
                if wall_tau == 0 and p.yield_stress_Pa > 0
                else p.yield_stress_Pa / wall_tau
                if wall_tau
                else 0.0
            ),
            "mass_flow_kg_s": p.mixture_density_kg_m3 * q.volume_flow_m3_s,
            "solid_volume_flow_m3_s": p.solid_volume_fraction * q.volume_flow_m3_s,
            "carrier_volume_flow_m3_s": (1 - p.solid_volume_fraction) * q.volume_flow_m3_s,
            "velocity_m_s": q.volume_flow_m3_s / area,
            "pressure_work_rate_W": pressure_power,
            "potential_energy_rate_W": potential_power,
            "dissipated_power_W": dissipated,
            "pressure_work_balance_residual_W": balance,
            "pressure_work_balance_limit_W": energy_limit,
            "flow_residual_m3_s": flow_residual,
            "flow_balance_limit_m3_s": flow_limit,
            "radial_flow_quadrature_error_m3_s": error,
            "root_iterations": root_iterations,
            "quadrature_calls": quadrature_calls,
            "minimal_zero_flow_pressure_branch": target == 0,
            "zero_flow_friction_pressure_support_Pa": (
                -4 * q.length_m * p.yield_stress_Pa / q.inner_diameter_m,
                4 * q.length_m * p.yield_stress_Pa / q.inner_diameter_m,
            )
            if target == 0
            else None,
            "deposition_velocity_m_s": None,
            "erosion_rate": None,
            "temperature_change_K": None,
            "radial_velocity_profile": _radial_velocity_profile(q,wall_tau,native),
        },
    )


def _evaluate_bound_slurry(q, c, backend, notes):
    """One unchanged calculation path, enclosed by a public or session source fence."""
    body = {
        "schema": "pipeline_integrity.slurry_transport.v1",
        "request": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "implementation_sha256": file_digest(Path(__file__)),
        "status": "needs_data",
        "notes": notes,
        "native_called": False,
        "provider": None,
        "native_newtonian": None,
        "values": None,
        "physical_validation_claim": False,
        "whole_slurry_complete": False,
        "qualification": "reviewed homogeneous snapshot; existing Newtonian owner or no-slip fully-developed laminar HB closure; no deposition/erosion/heterogeneous model",
    }
    if not body["notes"]:
        if backend != q.owner_pins:
            raise ValueError("reviewed numerical/hydraulic owner pins differ")
        body["provider"] = backend
        try:
            body["native_called"] = True
            _solve(q, body)
            json_value(body)
        except (ValueError, RuntimeError, ArithmeticError, TypeError, Warning) as exc:
            body.update(
                status="out_of_scope",
                notes=[f"{type(exc).__name__}: {exc}"],
                values=None,
                native_newtonian=None,
            )
    body = json_value(body)
    return {**body, "record_digest": digest(body)}


def evaluate_slurry_transport(request, context):
    q = SlurryTransportRequest.model_validate(request.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    notes = _policy(q, c)
    if notes:
        return _evaluate_bound_slurry(q, c, None, notes)
    before = native_backend_metadata()
    try:
        return _evaluate_bound_slurry(q, c, before, notes)
    finally:
        if native_backend_metadata() != before:
            raise ValueError("numerical/hydraulic owner changed during evaluation")


class SlurryTransportSession:
    """Batch nonlinear trials under one opening/closing native-source fence.

    ``evaluate`` returns provisional records. They cannot be used as completed
    owner receipts until successful context exit and ``finalize``. Finalization
    restores the exact public receipt shape; the enclosing calculation should
    also retain ``source_fence_receipt``. Only digest membership is stored, so a
    large root search does not retain every trial's full request/context.
    """

    def __init__(self, expected_owner_pins, *, maximum_evaluations=100000):
        if type(maximum_evaluations) is not int or not 1 <= maximum_evaluations <= 1000000:
            raise ValueError("slurry session evaluation budget must be an integer in [1,1000000]")
        self.expected = json.loads(json.dumps(json_value(expected_owner_pins), allow_nan=False))
        self.maximum_evaluations = maximum_evaluations
        self.evaluations = 0
        self.active = False
        self.closed = False
        self.verified = False
        self.before = None
        self.after = None
        self._issued = set()

    def __enter__(self):
        if self.active or self.closed:
            raise ValueError("slurry sessions cannot be entered twice")
        self.before = native_backend_metadata()
        if self.before != self.expected:
            raise ValueError("slurry session owner differs from reviewed source pins")
        self.active = True
        return self

    def evaluate(self, request, context):
        if not self.active:
            raise ValueError("slurry trial evaluation requires an active session")
        if self.evaluations >= self.maximum_evaluations:
            raise ValueError("slurry session evaluation budget exceeded")
        q = SlurryTransportRequest.model_validate(request.model_dump(mode="python"))
        c = RunContext.model_validate(context.model_dump(mode="python"))
        if q.owner_pins != self.expected:
            raise ValueError("slurry trial uses a different owner-source binding")
        self.evaluations += 1
        result = _evaluate_bound_slurry(q, c, self.before, _policy(q, c))
        provisional = {key: value for key, value in result.items() if key != "record_digest"}
        provisional["session_provisional"] = True
        provisional["session_owner_pins_digest"] = digest(self.before)
        provisional["record_digest"] = digest(provisional)
        self._issued.add(provisional["record_digest"])
        return provisional

    def __exit__(self, exception_type, exception, traceback):
        self.active = False
        self.closed = True
        self.after = native_backend_metadata()
        self.verified = self.after == self.before == self.expected
        if not self.verified:
            raise ValueError("slurry session source changed; all provisional trials are rejected")
        return False

    def finalize(self, record):
        if not self.closed or not self.verified:
            raise ValueError("slurry trials cannot be finalized before successful source-fence closure")
        if record.get("record_digest") not in self._issued or record.get("session_provisional") is not True:
            raise ValueError("slurry receipt was not issued by this session")
        body = {key: value for key, value in record.items() if key != "record_digest"}
        if digest(body) != record["record_digest"] or body.get("session_owner_pins_digest") != digest(self.before):
            raise ValueError("slurry provisional receipt changed before finalization")
        del body["session_provisional"]
        del body["session_owner_pins_digest"]
        body = json_value(body)
        return {**body, "record_digest": digest(body)}

    def source_fence_receipt(self):
        if not self.closed or not self.verified:
            raise ValueError("slurry source-fence receipt is unavailable before successful closure")
        body = dict(schema="pipeline_integrity.slurry_session_fence.v1", before=self.before, after=self.after,
                    unchanged=True, evaluations=self.evaluations,
                    maximum_evaluations=self.maximum_evaluations,
                    provisional_trials_require_finalization=True)
        body = json_value(body)
        return {**body, "record_digest": digest(body)}


def verify_slurry_transport_record(record, *, request=None, context=None):
    try:
        if record["record_digest"] != digest({k: v for k, v in record.items() if k != "record_digest"}):
            raise ValueError("slurry receipt changed")
        q = SlurryTransportRequest.model_validate_json(json.dumps(record["request"], allow_nan=False))
        c = RunContext.model_validate_json(json.dumps(record["context"], allow_nan=False))
        if (request is not None and request != q) or (context is not None and context != c):
            raise ValueError("caller request/context differs")
        if evaluate_slurry_transport(q, c) != record:
            raise ValueError("actual constitutive/native/pressure-work replay differs")
        return {"valid": True}
    except (ValueError, TypeError, KeyError, OSError, ImportError, AttributeError) as exc:
        return {"valid": False, "reason": str(exc)}
