"""Normalized monotonic elastic-plus-power coordinates through the material owner."""
from __future__ import annotations
import math
from typing import Literal
from .contracts import Contract,Name,Positive,Nonnegative,RunContext,digest
from .adapters import MaterialClient
from .material_exchange import prepare_material_job,evaluate_material_job


class NormalizedConstitutiveRequest(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    time_s: Nonnegative
    strain_measure: Literal['engineering','true']
    tensile_stress_pa: Nonnegative | None = None
    elastic_modulus_pa: Positive | None = None
    reference_stress_pa: Positive | None = None
    normalized_power_coefficient: Positive | None = None
    normalized_power_exponent: Positive | None = None
    maximum_supported_tensile_stress_pa: Positive | None = None
    calibration_basis_id: Name | None = None
    calibration_evidence_id: Name
    monotonic_stress_strain_coordinate_and_calibration_reviewed: bool = False


class NormalizedConstitutiveResult(Contract):
    status: Literal['computed','needs_data','out_of_scope','solver_withheld']
    request_digest: str
    context_digest: str
    strain_measure: Literal['engineering','true']
    calibration_basis_id: Name | None
    elastic_strain: Nonnegative | None = None
    power_strain: Nonnegative | None = None
    total_strain: Nonnegative | None = None
    native_material_receipt: dict | None = None
    issues: tuple[str,...] = ()
    native_amplitudes_are_virtual_monotonic_coordinates: Literal[True] = True
    cyclic_calibration_unloading_or_material_qualification_inferred: Literal[False] = False


def evaluate_normalized_constitutive_coordinate(request: NormalizedConstitutiveRequest,context: RunContext,*,client=None) -> NormalizedConstitutiveResult:
    """Map a reviewed normalized tensile curve without copying its scalar engine.

    The source-coordinate power term is alpha*(sigma0/E)*(sigma/sigma0)^n.
    The existing owner's strain-amplitude inputs are explicitly virtual scalar
    coordinates here: the conversion does not turn a monotonic calibration into
    a cyclic material model, nor convert true and engineering stress/strain.
    Positive alpha/exponent and caller stress support define the selected branch.
    """
    if not isinstance(request,NormalizedConstitutiveRequest):raise TypeError('NormalizedConstitutiveRequest required')
    q=NormalizedConstitutiveRequest.model_validate(request.model_dump(mode='python'))
    c=RunContext.model_validate(context.model_dump(mode='python'))
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):raise ValueError('constitutive coordinate and context asset/clock differ')
    if q.calibration_evidence_id not in {e.id for e in c.evidence}:raise ValueError('constitutive calibration evidence is absent')
    base=dict(request_digest=digest(q),context_digest=digest(c),strain_measure=q.strain_measure,calibration_basis_id=q.calibration_basis_id)
    missing=['missing '+k for k in ('tensile_stress_pa','elastic_modulus_pa','reference_stress_pa','normalized_power_coefficient',
        'normalized_power_exponent','maximum_supported_tensile_stress_pa','calibration_basis_id') if getattr(q,k) is None]
    if not q.monotonic_stress_strain_coordinate_and_calibration_reviewed:missing.append('monotonic coordinate measure and calibration require review')
    if missing:return NormalizedConstitutiveResult(status='needs_data',issues=tuple(missing),**base)
    if q.tensile_stress_pa>q.maximum_supported_tensile_stress_pa:
        return NormalizedConstitutiveResult(status='out_of_scope',issues=('query exceeds supplied tensile calibration support',),**base)
    log_normalized=math.log(q.normalized_power_coefficient)+math.log(q.reference_stress_pa)-math.log(q.elastic_modulus_pa)
    strength=math.exp(math.log(q.reference_stress_pa)-log_normalized/q.normalized_power_exponent)
    if not math.isfinite(strength) or strength<=0:raise ValueError('native power-coordinate strength exceeds positive finite support')
    parameters=dict(stress_amplitude_Pa=q.tensile_stress_pa,elastic_modulus_Pa=q.elastic_modulus_pa,
        cyclic_strength_coefficient_Pa=strength,cyclic_strain_hardening_exponent=1/q.normalized_power_exponent)
    client=MaterialClient() if client is None else client
    job=prepare_material_job('materials.ramberg_osgood',parameters,c,location_id=q.location_id,evidence_ids=(q.calibration_evidence_id,),client=client)
    receipt=evaluate_material_job(job,c,client=client)
    if receipt.native.status!='computed':return NormalizedConstitutiveResult(status='solver_withheld',issues=('existing scalar constitutive owner withheld calculation',),**base)
    values=receipt.native.payload['values']
    return NormalizedConstitutiveResult(status='computed',**base,elastic_strain=values['elastic_strain_amplitude'],
        power_strain=values['plastic_strain_amplitude'],total_strain=values['total_strain_amplitude'],native_material_receipt=receipt.model_dump(mode='json'))
