"""Single withdrawal/refill inventory under an explicitly additive-volume basis."""
from typing import Literal
from .contracts import Contract,Positive,Nonnegative,Fraction
from .volume_weighting import normalized_weighted_sum

class ComponentReplacementRequest(Contract):
    system_volume_m3: Positive
    initial_component_volume_fraction: Fraction | None
    feed_component_volume_fraction: Fraction | None
    target_component_volume_fraction: Fraction | None
    additive_volumes_common_state_and_uniform_withdrawal_reviewed: bool = False

class ComponentReplacementResult(Contract):
    status: Literal['computed','needs_data','out_of_scope','non_identifiable']
    replacement_volume_m3: Nonnegative | None = None
    replacement_fraction: Fraction | None = None
    final_component_volume_fraction: Fraction | None = None
    withdrawn_component_volume_m3: Nonnegative | None = None
    added_component_volume_m3: Nonnegative | None = None
    final_component_volume_m3: Nonnegative | None = None
    component_budget_residual_m3: float | None = None
    issues: tuple[str,...] = ()

def solve_component_replacement(q: ComponentReplacementRequest) -> ComponentReplacementResult:
    """Withdraw well-mixed original inventory once, then add equal feed volume.

    All volume fractions refer to the same component, temperature and pressure.
    Mixing contraction, chemistry, freezing-point selection and repeated/continuous
    dilution are outside this ledger. A no-change target selects zero replacement.
    """
    q=ComponentReplacementRequest.model_validate(q.model_dump(mode='python'))
    a,b,c=q.initial_component_volume_fraction,q.feed_component_volume_fraction,q.target_component_volume_fraction
    if None in (a,b,c) or not q.additive_volumes_common_state_and_uniform_withdrawal_reviewed:
        return ComponentReplacementResult(status='needs_data',issues=('complete common-state fractions and additive uniform-withdrawal review required',))
    if c==a:f=0.
    elif a==b:return ComponentReplacementResult(status='non_identifiable',issues=('feed has no component contrast with initial inventory',))
    elif not min(a,b)<=c<=max(a,b):return ComponentReplacementResult(status='out_of_scope',issues=('target lies outside the reachable initial/feed interval',))
    else:f=(c-a)/(b-a)
    final=normalized_weighted_sum({'retained':a,'feed':b},{'retained':1-f,'feed':f})
    removed=q.system_volume_m3*f*a;added=q.system_volume_m3*f*b;end=q.system_volume_m3*final
    import math
    error=math.fsum((q.system_volume_m3*a,-removed,added,-end))
    if not all(math.isfinite(x) for x in (removed,added,end,error)):
        raise ValueError('component inventory exceeds finite numerical support')
    return ComponentReplacementResult(status='computed',replacement_volume_m3=q.system_volume_m3*f,replacement_fraction=f,
        final_component_volume_fraction=final,withdrawn_component_volume_m3=removed,added_component_volume_m3=added,
        final_component_volume_m3=end,component_budget_residual_m3=error)
