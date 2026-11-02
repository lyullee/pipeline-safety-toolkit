"""A supplied fixed-life Goodman envelope intersected with an elastic peak limit.

The existing material owner evaluates every Goodman coordinate. This observer
solves for an admissible amplitude and nonnegative mean at one supplied life
anchor; it does not predict that anchor, plastic mean relaxation or fatigue life.
"""
import math
from .contracts import Contract,Name,Positive,RunContext,digest
from .material_exchange import prepare_material_job,evaluate_material_job
from .adapters import MaterialClient


class YieldLimitedMeanStressRequest(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    supplied_fully_reversed_failure_amplitude_pa: Positive | None = None
    supplied_whole_cycle_anchor: Positive | None = None
    supplied_yield_stress_pa: Positive | None = None
    supplied_ultimate_strength_pa: Positive | None = None
    supplied_tensile_peak_limit_pa: Positive | None = None
    calibration_evidence_id: Name
    calibration_basis_id: Name | None = None
    actual_elastic_amplitude_fixed_life_goodman_and_peak_limit_reviewed: bool = False


def evaluate_yield_limited_mean_stress(q: YieldLimitedMeanStressRequest,context: RunContext,*,client=None):
    """Return the intersection, including the limiting first-yield boundary.

    The peak limit must be supplied and no larger than supplied yield. At exact
    equality the result is an elastic-envelope limit, not a sustained plastic
    material state. A reversed amplitude at or above yield is withheld: an
    empirical mean-insensitivity assumption is not an elastoplastic closure.
    """
    from scipy.optimize import brentq
    q=YieldLimitedMeanStressRequest.model_validate(q.model_dump());c=RunContext.model_validate(context.model_dump())
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):raise ValueError('mean-stress/context identity differs')
    if q.calibration_evidence_id not in {e.id for e in c.evidence}:raise ValueError('fixed-life calibration evidence absent')
    base=dict(request_digest=digest(q),context_digest=digest(c),calibration_basis_id=q.calibration_basis_id,
        physical_stress_evolution_or_fatigue_life_predicted=False,field_or_standard_qualified=False)
    required=['supplied_fully_reversed_failure_amplitude_pa','supplied_whole_cycle_anchor','supplied_yield_stress_pa',
        'supplied_ultimate_strength_pa','supplied_tensile_peak_limit_pa','calibration_basis_id']
    if any(getattr(q,k) is None for k in required) or not q.actual_elastic_amplitude_fixed_life_goodman_and_peak_limit_reviewed:
        return dict(status='needs_data',**base)
    amplitude=q.supplied_fully_reversed_failure_amplitude_pa;yield_strength=q.supplied_yield_stress_pa
    ultimate=q.supplied_ultimate_strength_pa;peak=q.supplied_tensile_peak_limit_pa
    if ultimate<yield_strength:raise ValueError('ultimate strength below yield strength')
    if amplitude>=yield_strength:
        return dict(status='out_of_scope',reason='above-yield baseline needs a separately reviewed material/rule closure; no mean-insensitive response inferred',**base)
    if peak>yield_strength or peak>=ultimate:
        return dict(status='out_of_scope',reason='peak exceeds elastic support or reaches the Goodman normalization pole',**base)
    if peak<amplitude:
        return dict(status='no_failure_envelope_intersection_at_nonnegative_mean',reason='entire supplied peak segment is below the fixed-life envelope',**base)
    client=MaterialClient() if client is None else client;evaluations=[];cache={}
    def coordinate(a):
        if a in cache:return cache[a]
        mean=peak-a
        job=prepare_material_job('fatigue.goodman',dict(alternating_stress_Pa=a,mean_stress_Pa=mean,
            ultimate_tensile_strength_Pa=ultimate),c,location_id=q.location_id,evidence_ids=(q.calibration_evidence_id,),client=client)
        receipt=evaluate_material_job(job,c,client=client)
        if receipt.native.status!='computed':raise _Withheld(receipt.model_dump(mode='json'))
        value=receipt.native.payload['values']['equivalent_fully_reversed_amplitude_Pa']
        if not math.isfinite(value):raise ValueError('native coordinate exceeds finite support')
        cache[a]=(value,receipt);evaluations.append(dict(alternating_amplitude_pa=a,mean_stress_pa=mean,native_equivalent_amplitude_pa=value))
        return cache[a]
    try:
        root=brentq(lambda a:(coordinate(a)[0]-amplitude)/amplitude,0.,amplitude,xtol=max(1e-12,1e-12*amplitude),rtol=1e-12)
        equivalent,receipt=coordinate(root)
    except _Withheld as failure:
        return dict(status='solver_withheld',native_material_receipt=failure.receipt,**base)
    return dict(status='computed',whole_cycle_anchor=q.supplied_whole_cycle_anchor,
        corrected_fixed_life_failure_amplitude_pa=root,tensile_mean_at_peak_limit_pa=peak-root,
        actual_peak_limit_pa=peak,elastic_limit_boundary=peak==yield_strength,
        native_equivalent_amplitude_pa=equivalent,constitutive_coordinate_residual_pa=equivalent-amplitude,
        native_material_receipt=receipt.model_dump(mode='json'),evaluations=evaluations,**base)


class _Withheld(Exception):
    def __init__(self,receipt):self.receipt=receipt
