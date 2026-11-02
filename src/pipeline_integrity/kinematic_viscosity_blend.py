"""Mass-weighted double-log viscosity coordinate for reviewed liquid recipes.

This empirical coordinate is not a constitutive law. Applicability to a fluid
family requires measured blend evidence at the specified temperature/state.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive
from .volume_weighting import normalized_weighted_sum


class ViscosityBlendComponent(Contract):
    id: Name
    fraction: Nonnegative
    temperature_K: Positive
    pressure_Pa_abs: Positive
    kinematic_viscosity_m2_s: Positive | None = None
    dynamic_viscosity_Pa_s: Positive | None = None
    density_kg_m3: Positive | None = None
    property_origin: Name

    @model_validator(mode="after")
    def one_viscosity(self):
        if self.kinematic_viscosity_m2_s is not None and self.dynamic_viscosity_Pa_s is not None:
            raise ValueError("supply one viscosity coordinate per component")
        return self


class KinematicViscosityBlendRequest(Contract):
    components: tuple[ViscosityBlendComponent, ...] = Field(min_length=1)
    fraction_basis: Literal["mass", "charged_component_volume"]
    temperature_K: Positive
    pressure_Pa_abs: Positive
    state_id: Name
    homogeneous_newtonian_liquid_reviewed: bool = False
    measured_blend_applicability_evidence: Name | None = None
    actual_mixture_density_kg_m3: Positive | None = None

    @model_validator(mode="after")
    def composition(self):
        if len({c.id for c in self.components}) != len(self.components):
            raise ValueError("unique component identities required")
        if not math.isclose(math.fsum(c.fraction for c in self.components), 1., rel_tol=0, abs_tol=1e-12):
            raise ValueError("recipe fractions must sum to one")
        return self


class KinematicViscosityBlendResult(Contract):
    status: Literal["computed", "needs_data"]
    state_id: Name
    mass_fractions: dict[Name, float] | None = None
    component_kinematic_viscosities_m2_s: dict[Name, float] | None = None
    blended_kinematic_viscosity_m2_s: Positive | None = None
    blended_dynamic_viscosity_Pa_s: Positive | None = None
    reduced_double_log_coordinate: float | None = None
    empirical_applicability_evidence: Name | None = None
    field_or_standard_qualified: bool = False
    issues: tuple[str, ...] = ()


def evaluate_kinematic_viscosity_blend(request: KinematicViscosityBlendRequest) -> KinematicViscosityBlendResult:
    """Evaluate the Refutas mass-weighted coordinate in SI units.

    The reduced index is ln(ln(nu/cSt + 0.8)). Affine index scale and offset
    cancel in a normalized mixture, so neither is carried in this API. Positive
    log arguments require nu > 0.2 cSt; no extrapolation through that boundary.
    Charged component volumes determine masses using their individual supplied
    densities, without assuming that the final mixture volume is additive.
    A final dynamic viscosity uses separately supplied actual mixture density.
    All component properties must describe the same absolute P and T. Zero
    fraction components retain provenance but need no viscosity/density value.
    """
    if not isinstance(request, KinematicViscosityBlendRequest):
        raise TypeError("KinematicViscosityBlendRequest required")
    q = KinematicViscosityBlendRequest.model_validate(request.model_dump(mode="python"))
    issues = []
    if not q.homogeneous_newtonian_liquid_reviewed:
        issues.append("single homogeneous Newtonian liquid applicability requires review")
    active = [c for c in q.components if c.fraction > 0]
    values = {}
    for c in active:
        if c.temperature_K != q.temperature_K or c.pressure_Pa_abs != q.pressure_Pa_abs:
            raise ValueError("component viscosity and density must share the requested T and absolute P")
        nu = c.kinematic_viscosity_m2_s
        if c.dynamic_viscosity_Pa_s is not None:
            if c.density_kg_m3 is None:
                issues.append("density required for dynamic viscosity conversion: " + c.id)
            else:
                nu = c.dynamic_viscosity_Pa_s / c.density_kg_m3
        if nu is None:
            issues.append("missing kinematic viscosity: " + c.id)
        elif not math.isfinite(nu) or nu <= 0.2e-6:
            raise ValueError("double-log viscosity requires finite nu strictly above 0.2 cSt")
        else:
            values[c.id] = nu
        if q.fraction_basis == "charged_component_volume" and c.density_kg_m3 is None:
            issues.append("component density required to convert volume recipe to mass: " + c.id)
    if issues:
        return KinematicViscosityBlendResult(status="needs_data", state_id=q.state_id, issues=tuple(issues))
    if q.fraction_basis == "mass":
        weights = {c.id: c.fraction for c in active}
    else:
        # Scaling first avoids overflow for large, finite supplied densities.
        scale = max(c.density_kg_m3 for c in active)
        amounts = {c.id: c.fraction * (c.density_kg_m3 / scale) for c in active}
        total = math.fsum(amounts.values())
        if total <= 0:
            raise ValueError("mass conversion exceeds finite support")
        weights = {k: v / total for k, v in amounts.items()}
    coords = {k: math.log(math.log(v / 1e-6 + 0.8)) for k, v in values.items()}
    coordinate = normalized_weighted_sum(coords, weights)
    try:
        nu = (math.exp(math.exp(coordinate)) - 0.8) * 1e-6
        mu = None if q.actual_mixture_density_kg_m3 is None else nu * q.actual_mixture_density_kg_m3
    except OverflowError as exc:
        raise ValueError("viscosity blend exceeds finite support") from exc
    if not math.isfinite(nu) or nu <= 0 or (mu is not None and not math.isfinite(mu)):
        raise ValueError("viscosity blend exceeds finite positive support")
    notes = () if q.measured_blend_applicability_evidence else ("empirical blend applicability is unverified; numerical coordinate only",)
    return KinematicViscosityBlendResult(status="computed", state_id=q.state_id,
        mass_fractions={c.id: weights.get(c.id, 0.) for c in q.components},
        component_kinematic_viscosities_m2_s=values, blended_kinematic_viscosity_m2_s=nu,
        blended_dynamic_viscosity_Pa_s=mu, reduced_double_log_coordinate=coordinate,
        empirical_applicability_evidence=q.measured_blend_applicability_evidence, issues=notes)
