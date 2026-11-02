"""Machine-precision endpoint reconciliation around an unchanged SWT owner.

The native schema-bound job is always executed and preserved. Only a unique
declared calibration endpoint whose forward parameter agrees to a small number
of floating-point ULPs may reconcile a numerical bracket rejection. No numerical
search or physical calibration interval is extended.
"""
import math
from decimal import Decimal,localcontext
from typing import Literal
from pydantic import Field
from .contracts import Contract,Positive,Nonnegative,digest
from .material_exchange import MaterialJob,MaterialReceipt,evaluate_material_job


class SWTEndpointResult(Contract):
    status: Literal['computed','needs_data','out_of_scope','solver_withheld']
    job_digest: str
    native_material_receipt: MaterialReceipt
    cycles_to_failure: Positive | None = None
    reversals_to_failure: Positive | None = None
    swt_parameter_pa: Positive | None = None
    endpoint_reconciled: bool = False
    reconciled_endpoint: Literal['minimum','maximum'] | None = None
    forward_endpoint_parameter_pa: Positive | None = None
    endpoint_parameter_residual_pa: float | None = None
    allowed_parameter_roundoff_pa: Nonnegative | None = None
    physical_cycle_support_unchanged: Literal[True] = True
    stress_evolution_or_field_acceptance_predicted: Literal[False] = False


def _forward_at_endpoint(parameters,cycles):
    # A forward residual at two endpoints only, not a duplicate life solver.
    with localcontext() as context:
        context.prec=75
        d=lambda v:Decimal.from_float(float(v))
        sf=d(parameters['fatigue_strength_coefficient_Pa'])
        young=d(parameters['elastic_modulus_Pa'])
        ef=d(parameters['fatigue_ductility_coefficient'])
        b=d(parameters['fatigue_strength_exponent']);c=d(parameters['fatigue_ductility_exponent'])
        reversals=2*d(cycles)
        value=sf*sf/young*reversals**(2*b)+sf*ef*reversals**(b+c)
        return float(value)


def evaluate_swt_cycle_job(job: MaterialJob,context,*,client=None,
                           maximum_endpoint_roundoff_ulps: int = 16) -> SWTEndpointResult:
    """Execute an existing SWT job; preserve a uniquely matching endpoint.

    A native successful interior result is returned without numerical changes.
    Missing reviews, nonpositive maximum stress and genuinely outside-interval
    targets remain withheld. The original receipt is never rewritten to pretend
    that the native solver computed a rejected endpoint.
    """
    class Roundoff(Contract):
        ulps: int = Field(ge=1,le=64)
    budget=Roundoff(ulps=maximum_endpoint_roundoff_ulps).ulps
    q=MaterialJob.model_validate(job.model_dump())
    if q.model_id!='fatigue.swt_strain_life':raise ValueError('only the declared uniaxial SWT job is supported')
    receipt=evaluate_material_job(q,context,client=client);receipt.check_integrity()
    native=receipt.native;p=q.parameters
    base=dict(job_digest=digest(q),native_material_receipt=receipt)
    low=p['minimum_valid_cycles'];high=p['maximum_valid_cycles']
    if native.status=='computed':
        v=native.payload['values'];cycles=v['cycles_to_failure']
        if low<=cycles<=high:
            return SWTEndpointResult(status='computed',cycles_to_failure=cycles,
                reversals_to_failure=v['reversals_to_failure'],swt_parameter_pa=v['swt_parameter_Pa'],**base)
    elif native.status!='out_of_scope':
        return SWTEndpointResult(status=native.status if native.status=='needs_data' else 'solver_withheld',**base)
    if not p['stress_and_strain_measurement_basis_reviewed'] or not p['material_applicability_reviewed'] or p['maximum_stress_Pa']<=0:
        return SWTEndpointResult(status='out_of_scope',**base)
    target=p['maximum_stress_Pa']*p['strain_amplitude']
    if target<=0 or not math.isfinite(target):return SWTEndpointResult(status='out_of_scope',**base)
    matches=[]
    for name,cycles in [('minimum',low),('maximum',high)]:
        parameter=_forward_at_endpoint(p,cycles)
        if not math.isfinite(parameter) or parameter<=0:continue
        allowance=budget*math.ulp(max(parameter,target))
        residual=target-parameter
        if abs(residual)<=allowance:matches.append((name,cycles,parameter,residual,allowance))
    if len(matches)!=1:return SWTEndpointResult(status='out_of_scope',**base)
    name,cycles,parameter,residual,allowance=matches[0]
    return SWTEndpointResult(status='computed',cycles_to_failure=cycles,reversals_to_failure=2*cycles,
        swt_parameter_pa=target,endpoint_reconciled=True,reconciled_endpoint=name,
        forward_endpoint_parameter_pa=parameter,endpoint_parameter_residual_pa=residual,
        allowed_parameter_roundoff_pa=allowance,**base)
