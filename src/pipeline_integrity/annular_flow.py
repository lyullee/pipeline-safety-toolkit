"""Steady concentric-annulus loss with explicitly distinct constitutive choices.

The supplied-Darcy branch is a bulk pressure-loss observation using the real
annular mean velocity and hydraulic diameter. It predicts neither a friction
factor nor its partition between walls. The Newtonian branch instead solves
the fully developed no-slip radial Poisson problem at both circular walls.
Neither branch models eccentricity, entrance flow, slip, moving walls, thermal
property evolution, multiphase flow or an experimental turbulence calibration.
"""
from __future__ import annotations

import math
from importlib.metadata import version
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Nonnegative, Positive


class ConcentricAnnulusLossRequest(Contract):
    outer_flow_radius_m: Positive
    inner_body_radius_m: Positive
    length_m: Nonnegative
    volume_flow_m3_s: float | None = None
    density_kg_m3: Positive | None = None
    dynamic_viscosity_Pa_s: Positive | None = None
    closure: Literal["supplied_darcy", "newtonian_laminar"] = "supplied_darcy"
    supplied_darcy_friction_factor: Positive | None = None
    factor_basis_id: str | None = Field(default=None, min_length=1)
    maximum_reviewed_laminar_reynolds: Positive | None = None
    radial_positions_m: tuple[Positive, ...] | None = Field(default=None, max_length=100000)
    fixed_concentric_steady_single_liquid_state_reviewed: bool = False
    supplied_factor_applicability_reviewed: bool = False
    fully_developed_no_slip_laminar_domain_reviewed: bool = False

    @model_validator(mode="after")
    def geometry_and_choice(self):
        if self.inner_body_radius_m >= self.outer_flow_radius_m:
            raise ValueError("inner body radius must be strictly below outer flow radius")
        if self.radial_positions_m is not None and any(
            r < self.inner_body_radius_m or r > self.outer_flow_radius_m for r in self.radial_positions_m
        ):
            raise ValueError("sample radii must lie between the actual annular walls")
        if self.closure == "newtonian_laminar" and self.supplied_darcy_friction_factor is not None:
            raise ValueError("supplied friction and the Newtonian prediction cannot be combined")
        return self


class AnnularProfilePoint(Contract):
    radius_m: Positive
    axial_velocity_m_s: float
    radial_velocity_gradient_s_inv: float
    fluid_shear_rz_Pa: float


class ConcentricAnnulusLossResult(Contract):
    status: Literal["computed", "needs_data", "out_of_scope"]
    flow_area_m2: Positive
    wetted_perimeter_m: Positive
    hydraulic_radius_m: Positive
    hydraulic_diameter_m: Positive
    closure: Literal["supplied_darcy", "newtonian_laminar"]
    mean_velocity_m_s: float | None = None
    reynolds_hydraulic_diameter: Nonnegative | None = None
    darcy_friction_factor: Positive | None = None
    signed_friction_pressure_drop_Pa: float | None = None
    signed_pressure_gradient_Pa_m: float | None = None
    pressure_volume_dissipation_W: Nonnegative | None = None
    pressure_force_on_segment_N: float | None = None
    total_wall_force_on_fluid_N: float | None = None
    inner_wall_axial_traction_on_fluid_Pa: float | None = None
    outer_wall_axial_traction_on_fluid_Pa: float | None = None
    profile_status: Literal["not_requested", "computed_laminar", "out_of_scope"] = "not_requested"
    radial_profile: tuple[AnnularProfilePoint, ...] | None = None
    factor_predicted_from_newtonian_law: bool = False
    turbulence_or_field_calibration_qualified: bool = False
    backend: str = ""
    issues: tuple[str, ...] = ()


def _finite(*values):
    if not all(math.isfinite(x) for x in values):
        raise ValueError("annular response exceeds finite numerical support")


def _log_ratio(hi, lo):
    gap = hi - lo
    return math.log1p(gap / lo) if gap < lo else math.log(hi) - math.log(lo)


def _sinhc_minus_one(z):
    """Positive even series for sinh(z)/z-1 on the selected small interval."""
    term = z * z / 6
    total = term
    for k in range(1, 32):
        term *= z * z / ((2 * k + 2) * (2 * k + 3))
        total += term
        if term <= 1e-17 * total:
            break
    return total


def _dimensionless_conductance(lam):
    # F = 1-k^4-(1-k^2)^2/log(1/k), k=Ri/Ro. The positive
    # series avoids subtracting first-order quantities to obtain a cubic gap.
    if lam < .5:
        term = (2 / 3) * lam**3
        total = term
        for k in range(1, 32):
            term *= 4 * lam * lam * (k + 1)**2 / (k * (k + 2) * (2 * k + 2) * (2 * k + 3))
            total += term
            if term <= 1e-17 * total:
                break
        return 2 * math.exp(-2 * lam) * total
    delta = -math.expm1(-2 * lam)
    return -math.expm1(-4 * lam) - delta * delta / lam


def _profile_shape(radius, inner, outer, lam):
    if radius == inner or radius == outer:
        return 0.
    z = _log_ratio(radius, inner)
    y = _log_ratio(outer, radius)
    if lam < .5:
        # x-x^k is evaluated without cancellation at the outer no-slip wall.
        x = z / lam
        logx = math.log1p(-y / lam) if y < z else math.log(x)
        term = (2 * lam)**2 / 2
        total = 0.
        for k in range(2, 64):
            contribution = term * x * (-math.expm1((k - 1) * logx))
            total += contribution
            if contribution <= 1e-17 * total:
                break
            term *= 2 * lam / (k + 1)
        return math.exp(-2 * lam) * total
    delta = -math.expm1(-2 * lam)
    if z < y:
        return delta * (z / lam) - (radius / outer)**2 * (-math.expm1(-2 * z))
    return -math.expm1(-2 * y) - delta * (y / lam)


def _laminar_shear(radius, inner, outer, lam, gradient):
    if lam < .5:
        a, b = radius - inner, outer - radius
        # Ri*Ro-r^2 is factored to preserve the small curvature term even
        # at an exactly representable mid-gap station.
        w = math.log1p((inner * (b - a) - a * a) / radius**2)
        shape = math.expm1(w) + math.exp(w) * _sinhc_minus_one(lam)
        return gradient * radius / 2 * shape
    delta = -math.expm1(-2 * lam)
    return gradient / 4 * (outer**2 * delta / (lam * radius) - 2 * radius)


def evaluate_concentric_annulus_loss(request: ConcentricAnnulusLossRequest) -> ConcentricAnnulusLossResult:
    """Calculate inlet-minus-outlet friction drop along the declared positive axis.

    Signed Q determines the friction-drop sign; pressure-volume dissipation is
    nonnegative. No absolute operating pressure or phase margin is inferred.
    The laminar Reynolds limit is supplied by the caller: a circular-pipe
    transition threshold is not silently imposed on an annular geometry.
    """
    if not isinstance(request, ConcentricAnnulusLossRequest):
        raise TypeError("ConcentricAnnulusLossRequest required")
    q = ConcentricAnnulusLossRequest.model_validate(request.model_dump(mode="python"))
    inner, outer = q.inner_body_radius_m, q.outer_flow_radius_m
    area = math.pi * (outer - inner) * (outer + inner)
    perimeter = 2 * math.pi * (outer + inner)
    diameter = 2 * (outer - inner)
    _finite(area, perimeter, diameter)
    if min(area, perimeter, diameter) <= 0:
        raise ValueError("annular geometry is below finite numerical support")
    base = dict(flow_area_m2=area, wetted_perimeter_m=perimeter,
                hydraulic_radius_m=diameter / 4, hydraulic_diameter_m=diameter, closure=q.closure)
    missing = []
    if not q.fixed_concentric_steady_single_liquid_state_reviewed:
        missing.append("fixed concentric steady single-liquid state requires review")
    if q.volume_flow_m3_s is None:missing.append("missing signed volume flow")
    if q.density_kg_m3 is None:missing.append("missing actual liquid density")
    if q.closure == "supplied_darcy":
        if q.supplied_darcy_friction_factor is None:missing.append("missing supplied Darcy factor")
        if not q.supplied_factor_applicability_reviewed or q.factor_basis_id is None:
            missing.append("supplied factor basis and annular applicability require review")
    else:
        if q.dynamic_viscosity_Pa_s is None:missing.append("missing Newtonian dynamic viscosity")
        if not q.fully_developed_no_slip_laminar_domain_reviewed or q.maximum_reviewed_laminar_reynolds is None:
            missing.append("fully developed no-slip laminar domain and Reynolds support require review")
    if missing:
        return ConcentricAnnulusLossResult(status="needs_data", **base, issues=tuple(missing))
    velocity = q.volume_flow_m3_s / area
    _finite(velocity)
    if q.volume_flow_m3_s and velocity == 0:
        raise ValueError("nonzero annular flow is below finite velocity support")
    reynolds = (q.density_kg_m3 * abs(velocity) * diameter / q.dynamic_viscosity_Pa_s
                if q.dynamic_viscosity_Pa_s is not None else None)
    if reynolds is not None:_finite(reynolds)
    base.update(mean_velocity_m_s=velocity, reynolds_hydraulic_diameter=reynolds)
    if q.closure == "newtonian_laminar" and reynolds > q.maximum_reviewed_laminar_reynolds:
        return ConcentricAnnulusLossResult(status="out_of_scope", **base, issues=("reviewed annular laminar Reynolds support exceeded",))
    from fluids.core import dP_from_K
    lam = _log_ratio(outer, inner)
    if q.closure == "supplied_darcy":
        factor = q.supplied_darcy_friction_factor
        gradient = math.copysign(dP_from_K(factor / diameter, q.density_kg_m3, velocity), velocity)
    else:
        fshape = _dimensionless_conductance(lam)
        if fshape <= 0:raise ValueError("annular conductance is below finite numerical support")
        delta = -math.expm1(-2 * lam)
        gradient = 8 * q.dynamic_viscosity_Pa_s * velocity / outer**2 * delta / fshape
        factor = 2 * abs(gradient) * diameter / (q.density_kg_m3 * velocity**2) if velocity else None
    _finite(gradient)
    if factor is not None:_finite(factor)
    pressure = math.copysign(dP_from_K(factor * q.length_m / diameter, q.density_kg_m3, velocity), velocity) if factor is not None else 0.
    if velocity and q.length_m and (gradient == 0 or pressure == 0):
        raise ValueError("nonzero annular loss is below native finite arithmetic support")
    power = pressure * q.volume_flow_m3_s
    force = pressure * area
    _finite(pressure, power, force)
    result = dict(status="computed", **base, darcy_friction_factor=factor,
                  signed_friction_pressure_drop_Pa=pressure, signed_pressure_gradient_Pa_m=gradient,
                  pressure_volume_dissipation_W=power, pressure_force_on_segment_N=force,
                  total_wall_force_on_fluid_N=-force, backend="fluids.core.dP_from_K:" + version("fluids"),
                  factor_predicted_from_newtonian_law=q.closure == "newtonian_laminar")
    if q.closure == "supplied_darcy":
        if q.radial_positions_m is not None:
            result.update(profile_status="out_of_scope", issues=("a bulk supplied factor does not determine a radial velocity or wall-shear partition",))
        return ConcentricAnnulusLossResult(**result)
    tau_inner = _laminar_shear(inner, inner, outer, lam, gradient)
    tau_outer = _laminar_shear(outer, inner, outer, lam, gradient)
    _finite(tau_inner, tau_outer)
    result.update(inner_wall_axial_traction_on_fluid_Pa=-tau_inner,
                  outer_wall_axial_traction_on_fluid_Pa=tau_outer)
    if q.radial_positions_m is not None:
        rows = []
        for radius in q.radial_positions_m:
            shape = _profile_shape(radius, inner, outer, lam)
            if shape < 0:raise ValueError("radial profile left nonnegative no-slip shape support")
            speed = 2 * velocity * delta / fshape * shape
            shear = _laminar_shear(radius, inner, outer, lam, gradient)
            derivative = shear / q.dynamic_viscosity_Pa_s
            _finite(speed, shear, derivative)
            rows.append(AnnularProfilePoint(radius_m=radius, axial_velocity_m_s=speed,
                         radial_velocity_gradient_s_inv=derivative, fluid_shear_rz_Pa=shear))
        result.update(profile_status="computed_laminar", radial_profile=tuple(rows))
    return ConcentricAnnulusLossResult(**result)
