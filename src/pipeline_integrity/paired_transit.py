"""Paired travel-time inversion for uniform axial advection and stationary endpoints."""
import math
from typing import Literal
from .contracts import Contract,Name,Positive,Nonnegative

class PairedTransitRequest(Contract):
    clock_id: Name
    path_length_m: Positive
    signed_axial_projection_m: float
    forward_observed_time_s: Positive | None
    reverse_observed_time_s: Positive | None
    forward_fixed_delay_s: Nonnegative | None
    reverse_fixed_delay_s: Nonnegative | None
    flow_area_m2: Positive | None = None
    chord_to_area_mean_factor: Positive | None = None
    uniform_axial_flow_isotropic_medium_and_stationary_paths_reviewed: bool = False
    area_mean_profile_conversion_reviewed: bool = False

class PairedTransitResult(Contract):
    status: Literal['computed','needs_data','non_identifiable','out_of_scope']
    clock_id: Name
    forward_propagation_time_s: Positive | None = None
    reverse_propagation_time_s: Positive | None = None
    axial_flow_velocity_m_s: float | None = None
    uniform_medium_sound_speed_m_s: Positive | None = None
    effective_beam_speed_m_s: Positive | None = None
    signed_volume_flow_m3_s: float | None = None
    issues: tuple[str,...] = ()
    fluid_density_inferred: Literal[False] = False

def invert_paired_transit(q: PairedTransitRequest) -> PairedTransitResult:
    """Solve |L*n/t_forward-u*ex|=|-L*n/t_reverse-u*ex|=c.

    The familiar half-sum of reciprocal transit times measures effective beam
    speed. For oblique rays it differs from medium sound speed by the transverse
    advection correction. Density is not determined by this kinematic observation.
    Fixed hardware delays are explicit; no timing bias or flow-profile is fitted.
    """
    q=PairedTransitRequest.model_validate(q.model_dump(mode='python'))
    if abs(q.signed_axial_projection_m)>q.path_length_m:raise ValueError('axial projection exceeds actual acoustic path length')
    if not q.uniform_axial_flow_isotropic_medium_and_stationary_paths_reviewed or None in (q.forward_observed_time_s,q.reverse_observed_time_s,q.forward_fixed_delay_s,q.reverse_fixed_delay_s):
        return PairedTransitResult(status='needs_data',clock_id=q.clock_id,issues=('complete common-clock travel times, fixed delays and uniform propagation review required',))
    td=q.forward_observed_time_s-q.forward_fixed_delay_s;tu=q.reverse_observed_time_s-q.reverse_fixed_delay_s
    if min(td,tu)<=0:return PairedTransitResult(status='out_of_scope',clock_id=q.clock_id,issues=('fixed delay leaves no positive propagation time',))
    ce=.5*q.path_length_m*(1/td+1/tu);beta=q.signed_axial_projection_m/q.path_length_m
    base=dict(clock_id=q.clock_id,forward_propagation_time_s=td,reverse_propagation_time_s=tu,effective_beam_speed_m_s=ce)
    if beta==0 and q.signed_axial_projection_m!=0:raise ValueError('nonzero path projection is below finite support')
    if beta==0:
        return PairedTransitResult(status='non_identifiable' if td==tu else 'out_of_scope',**base,issues=('perpendicular paths cannot identify axial flow and true sound speed independently',))
    velocity=(.5*q.path_length_m/beta)*((tu-td)/td)/tu
    sound=math.hypot(ce,velocity*math.sqrt(max(0.,(1-beta)*(1+beta))))
    if not all(math.isfinite(x) for x in (ce,velocity,sound)):raise ValueError('transit inversion exceeds finite numerical support')
    flow=None;issues=[]
    if q.flow_area_m2 is not None and q.chord_to_area_mean_factor is not None and q.area_mean_profile_conversion_reviewed:
        flow=q.flow_area_m2*q.chord_to_area_mean_factor*velocity
        if not math.isfinite(flow):raise ValueError('volume-flow observation exceeds finite support')
    else:issues.append('area-mean profile conversion/area remains unknown or unreviewed')
    return PairedTransitResult(status='computed',**base,axial_flow_velocity_m_s=velocity,uniform_medium_sound_speed_m_s=sound,signed_volume_flow_m3_s=flow,issues=tuple(issues))
