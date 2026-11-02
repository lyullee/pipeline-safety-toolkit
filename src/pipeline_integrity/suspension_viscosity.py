"""Effective viscosity of a reviewed Newtonian hard-sphere suspension.

The effective-volume differential closure follows Mendoza and Santamaria-Holek,
arXiv:0810.5111, equation 15. Model equations are implemented independently;
no upstream prose, tables, figures or implementation are included.
"""
from __future__ import annotations
import math
from pydantic import Field
from .contracts import Contract, Name, Positive, Nonnegative


class SuspensionViscosityRequest(Contract):
    id: Name
    temperature_K: Positive
    carrier_dynamic_viscosity_Pa_s: Positive | None
    dry_solid_volume_fraction: float | None = Field(ge=0.,le=1.)
    maximum_packing_volume_fraction: float | None = Field(gt=0.,le=1.)
    slurry_density_kg_m3: Positive | None = None
    hard_sphere_aqueous_newtonian_low_inertia_regime_reviewed: bool = False
    carrier_property_and_packing_evidence_id: Name


class SuspensionViscosityResult(Contract):
    status: str
    model: str = 'effective_volume_differential_hard_spheres'
    issues: tuple[str,...] = ()
    temperature_K: Positive
    effective_solid_fraction: Nonnegative | None = None
    relative_dynamic_viscosity: Positive | None = None
    effective_dynamic_viscosity_Pa_s: Positive | None = None
    effective_kinematic_viscosity_m2_s: Positive | None = None
    logarithmic_viscosity_slope_per_volume_fraction: Positive | None = None
    field_or_material_calibration_qualified: bool = False


def evaluate_suspension_viscosity(q:SuspensionViscosityRequest)->SuspensionViscosityResult:
    """Predict viscosity from dry-solid volume fraction and reviewed packing.

    The carrier viscosity belongs to the given common temperature. Density, if
    supplied, belongs to the complete suspension; carrier density is not silently
    substituted. The selected closure concerns identical rigid spheres in a
    Newtonian aqueous carrier. It does not infer packing from concentration or
    solve settling, slip, yielding, shear thickening or an emulsion.
    """
    q=SuspensionViscosityRequest.model_validate(q.model_dump())
    common=dict(temperature_K=q.temperature_K)
    if not q.hard_sphere_aqueous_newtonian_low_inertia_regime_reviewed:
        return SuspensionViscosityResult(status='needs_data',issues=('particle/carrier/regime applicability requires review',),**common)
    if any(v is None for v in (q.carrier_dynamic_viscosity_Pa_s,q.dry_solid_volume_fraction,q.maximum_packing_volume_fraction)):
        return SuspensionViscosityResult(status='needs_data',issues=('carrier viscosity, actual dry-solid volume fraction and packing are required',),**common)
    phi=q.dry_solid_volume_fraction;packing=q.maximum_packing_volume_fraction
    if packing>math.pi/(3*math.sqrt(2)):
        return SuspensionViscosityResult(status='out_of_scope',issues=('selected identical-sphere geometric packing exceeds close packing',),**common)
    if phi>=packing:
        return SuspensionViscosityResult(status='out_of_scope',issues=('fluid closure ends strictly before the declared packing boundary',),**common)
    occupied_fraction=phi/packing
    if occupied_fraction>=1:
        return SuspensionViscosityResult(status='out_of_scope',issues=('packing gap cannot be resolved in the supplied floating-point coordinate',),**common)
    excluded_fraction=(1-packing)*occupied_fraction
    # Separate free-volume denominators avoid cancellation in 1-phi_eff.
    carrier_space=1-excluded_fraction
    effective_fraction=phi/carrier_space
    log_ratio=2.5*(math.log1p(-excluded_fraction)-math.log1p(-occupied_fraction))
    try:
        relative=math.exp(log_ratio)
        viscosity=q.carrier_dynamic_viscosity_Pa_s*relative
        slope=2.5/(carrier_space*(1-occupied_fraction))
        kinematic=viscosity/q.slurry_density_kg_m3 if q.slurry_density_kg_m3 is not None else None
    except (OverflowError,ZeroDivisionError):
        return SuspensionViscosityResult(status='out_of_scope',issues=('packing-limit viscosity exceeds finite numerical support',),**common)
    if not all(math.isfinite(x) and x>0 for x in (relative,viscosity,slope)) or (kinematic is not None and (not math.isfinite(kinematic) or kinematic<=0)):
        return SuspensionViscosityResult(status='out_of_scope',issues=('requested viscosity coordinate exceeds finite numerical support',),**common)
    return SuspensionViscosityResult(status='computed',effective_solid_fraction=effective_fraction,
        relative_dynamic_viscosity=relative,effective_dynamic_viscosity_Pa_s=viscosity,
        effective_kinematic_viscosity_m2_s=kinematic,
        logarithmic_viscosity_slope_per_volume_fraction=slope,**common)
