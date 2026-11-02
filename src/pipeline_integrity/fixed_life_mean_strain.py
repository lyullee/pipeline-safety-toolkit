"""Fixed-life strain capacity by inversion of existing mean-sensitive owners."""
import math
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Contract,Name,Positive,RunContext,digest
from .adapters import MaterialClient
from .material_exchange import prepare_material_job,evaluate_material_job
from .swt_endpoint_compatibility import evaluate_swt_cycle_job


class FixedLifeMeanStrainRequest(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    method: Literal['morrow','swt']
    target_whole_cycles: Positive
    native_parameters_without_strain: dict
    reviewed_strain_amplitude_bracket: tuple[Positive,Positive]
    absolute_strain_root_tolerance: Positive = 1e-13
    evidence_ids: tuple[Name,...] = Field(min_length=1)
    fixed_life_inverse_and_strain_bracket_reviewed: bool = False

    @model_validator(mode='after')
    def ordered(self):
        lo,hi=self.reviewed_strain_amplitude_bracket
        if hi<=lo or self.absolute_strain_root_tolerance>=hi-lo:raise ValueError('nonempty resolved strain bracket required')
        if 'strain_amplitude' in self.native_parameters_without_strain:raise ValueError('strain is the unknown, not a supplied native parameter')
        return self


def evaluate_fixed_life_mean_strain(q: FixedLifeMeanStrainRequest,context: RunContext,*,client=None):
    """Return strain and its explicitly elastic-calculated stress coordinate.

    The supplied maximum/mean stress is the reviewed actual/stabilized stress
    used by the selected owner. This adapter neither evolves that stress nor
    turns E*strain into an actual plastic stress. Trial strains must remain in
    the native calibration domain; no rejected trial is replaced by extrapolated
    life and no physical cycle interval is widened to make a bracket work.
    """
    from scipy.optimize import brentq
    q=FixedLifeMeanStrainRequest.model_validate(q.model_dump());c=RunContext.model_validate(context.model_dump())
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):raise ValueError('fixed-life/context identity differs')
    if set(q.evidence_ids)-{e.id for e in c.evidence}:raise ValueError('fixed-life evidence absent')
    base=dict(request_digest=digest(q),method=q.method,target_whole_cycles=q.target_whole_cycles,
        actual_stress_evolution_predicted=False,elastic_calculated_stress_is_actual_stress=False,
        field_or_standard_qualified=False)
    if not q.fixed_life_inverse_and_strain_bracket_reviewed:return dict(status='needs_data',**base)
    p=q.native_parameters_without_strain
    if any(k not in p for k in ['minimum_valid_cycles','maximum_valid_cycles','elastic_modulus_Pa']):
        return dict(status='needs_data',reason='native cycle support and modulus required',**base)
    if not p['minimum_valid_cycles']<=q.target_whole_cycles<=p['maximum_valid_cycles']:
        return dict(status='out_of_scope',reason='target life outside native calibration support',**base)
    client=MaterialClient() if client is None else client;cache={};evaluations=[]
    def observe(strain):
        if strain in cache:return cache[strain]
        parameters=dict(p,strain_amplitude=strain)
        job=prepare_material_job('fatigue.'+q.method+'_strain_life',parameters,c,location_id=q.location_id,evidence_ids=q.evidence_ids,client=client)
        if q.method=='swt':
            output=evaluate_swt_cycle_job(job,c,client=client)
            status=output.status;cycles=output.cycles_to_failure;receipt=output.model_dump(mode='json')
        else:
            output=evaluate_material_job(job,c,client=client);status=output.native.status
            cycles=output.native.payload['values'].get('cycles_to_failure');receipt=output.model_dump(mode='json')
        evaluations.append(dict(strain_amplitude=strain,status=status,predicted_whole_cycles=cycles))
        if status!='computed':raise _OwnerWithheld(status,receipt)
        if cycles is None or cycles<=0 or not math.isfinite(cycles):raise ValueError('native cycle prediction is not positive finite')
        cache[strain]=(cycles,receipt);return cache[strain]
    try:
        low,high=q.reviewed_strain_amplitude_bracket;nlow,_=observe(low);nhigh,_=observe(high)
        if nlow<q.target_whole_cycles or nhigh>q.target_whole_cycles:
            return dict(status='bracket_does_not_bound_fixed_life',evaluations=evaluations,**base)
        strain=brentq(lambda value:math.log(observe(value)[0])-math.log(q.target_whole_cycles),
            low,high,xtol=q.absolute_strain_root_tolerance,rtol=1e-12)
        cycles,receipt=observe(strain)
    except _OwnerWithheld as failure:
        return dict(status='needs_data' if failure.status=='needs_data' else 'solver_withheld',
            reason='native trial withheld; calibration support was not extended',native_result=failure.receipt,evaluations=evaluations,**base)
    pseudo=p['elastic_modulus_Pa']*strain
    if not math.isfinite(pseudo):raise ValueError('elastic-calculated coordinate exceeds finite support')
    return dict(status='computed',calculated_strain_amplitude=strain,
        elastic_calculated_pseudo_stress_amplitude_pa=pseudo,native_predicted_whole_cycles=cycles,
        cycle_residual=cycles-q.target_whole_cycles,native_result=receipt,evaluations=evaluations,**base)


class _OwnerWithheld(Exception):
    def __init__(self,status,receipt):self.status=status;self.receipt=receipt
