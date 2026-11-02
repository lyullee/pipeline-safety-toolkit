"""Explicit volume-average liquid viscosity estimate with applicability retained.

This averaging rule does not identify emulsion topology or dispersed fraction.
Its exact parallel-shear reference is not a universal oil/water rheology law.
"""
from __future__ import annotations
from typing import Literal
from .contracts import Contract, Name, Nonnegative, Positive
from .volume_weighting import normalize_component_volumes, normalized_weighted_sum


class OilWaterViscosityRequest(Contract):
    oil_volume_m3: Nonnegative | None
    water_volume_m3: Nonnegative | None
    oil_dynamic_viscosity_Pa_s: Positive | None
    water_dynamic_viscosity_Pa_s: Positive | None
    temperature_K: Positive | None
    pressure_Pa_abs: Positive | None
    component_properties_at_same_state_reviewed: bool = False
    volume_average_approximation_selected: bool = False
    rheology_evidence_id: Name | None
    requested_shear_rate_s_inv: float | None = None


class OilWaterViscosityResult(Contract):
    status: Literal['computed', 'needs_data', 'out_of_scope']
    rheology_evidence_id: Name | None
    estimated_dynamic_viscosity_Pa_s: Positive | None = None
    oil_volume_fraction: Nonnegative | None = None
    water_volume_fraction: Nonnegative | None = None
    estimated_shear_stress_Pa: float | None = None
    estimated_dissipation_W_m3: Nonnegative | None = None
    issues: tuple[str, ...] = ()
    emulsion_morphology_or_three_phase_pressure_loss_qualified: Literal[False] = False


def estimate_oil_water_viscosity(q: OilWaterViscosityRequest) -> OilWaterViscosityResult:
    """Apply the selected approximate rule at one common absolute PT state.

Volumes refer to components present at that state, not standard volumes or
mass fractions. Missing active-phase properties remain unknown. Shear outputs
are optional observations of this constant-viscosity estimate, not evidence of
Newtonian emulsion behavior. No pressure-loss, slip or inversion model is run.
"""
    q = OilWaterViscosityRequest.model_validate(q.model_dump(mode='python'))
    base = {'rheology_evidence_id': q.rheology_evidence_id}
    if any(v is None for v in (q.oil_volume_m3,q.water_volume_m3,q.temperature_K,q.pressure_Pa_abs,q.rheology_evidence_id)) or not q.component_properties_at_same_state_reviewed or not q.volume_average_approximation_selected:
        return OilWaterViscosityResult(status='needs_data', **base, issues=('Common-state volumes, properties and an explicit reviewed approximation selection required.',))
    if q.oil_volume_m3 + q.water_volume_m3 == 0:
        return OilWaterViscosityResult(status='out_of_scope', **base, issues=('Zero liquid inventory has no identifiable mixture viscosity.',))
    volumes = {'oil': q.oil_volume_m3, 'water': q.water_volume_m3}
    properties = {'oil': q.oil_dynamic_viscosity_Pa_s, 'water': q.water_dynamic_viscosity_Pa_s}
    if any(volumes[k] > 0 and properties[k] is None for k in volumes):
        return OilWaterViscosityResult(status='needs_data', **base, issues=('Every present liquid needs a dynamic viscosity at the declared common state.',))
    _, weights = normalize_component_volumes(volumes)
    # A missing zero-inventory component contributes exactly zero. The filler
    # value is consumed solely by a zero weight; it is never a material model.
    viscosity = normalized_weighted_sum({k:(v if v is not None else 0.) for k,v in properties.items()}, weights)
    shear = q.requested_shear_rate_s_inv
    return OilWaterViscosityResult(status='computed', **base,
        estimated_dynamic_viscosity_Pa_s=viscosity,oil_volume_fraction=weights['oil'],water_volume_fraction=weights['water'],
        estimated_shear_stress_Pa=None if shear is None else viscosity*shear,
        estimated_dissipation_W_m3=None if shear is None else viscosity*shear*shear,
        issues=('Approximate volume-average coefficient only; actual emulsion morphology, non-Newtonian response and pipeline multiphase accuracy require separate data.',))
