"""Horizontal ballistic liquid-jet observation without empirical discharge factors."""
import math
from .contracts import Contract,Positive,Nonnegative,Calculation

class HorizontalJetObservation(Contract):
    horizontal_distance_m: Nonnegative | None
    vertical_drop_m: Nonnegative | None
    gravity_m_s2: Positive
    actual_normal_jet_area_m2: Positive | None = None
    tracked_to_section_mean_velocity_factor: Positive | None = None
    horizontal_launch_constant_gravity_and_ballistic_path_reviewed: bool = False
    actual_area_and_velocity_profile_conversion_reviewed: bool = False

def invert_horizontal_jet(q: HorizontalJetObservation) -> Calculation:
    """Infer tracked horizontal parcel speed from position below the exit plane.

    Vertical initial speed is zero. Aerodynamic drag, jet breakup, capillary
    effects and the path-to-exit alignment are outside this observation model.
    Volume flow additionally needs actual normal jet area and a reviewed mapping
    from the tracked streamline/parcel to section-mean exit speed; neither is
    silently substituted by nominal pipe area or a discharge coefficient.
    """
    q=HorizontalJetObservation.model_validate(q.model_dump(mode='python'))
    if not q.horizontal_launch_constant_gravity_and_ballistic_path_reviewed or None in (q.horizontal_distance_m,q.vertical_drop_m):
        return Calculation(values={},status='needs_data',notes=('positive trajectory offsets and reviewed horizontal ballistic launch required',))
    if min(q.horizontal_distance_m,q.vertical_drop_m)<=0:
        return Calculation(values={},status='out_of_scope',notes=('a finite horizontal jet speed needs positive horizontal distance and positive gravity drop',))
    time=math.sqrt(2*(q.vertical_drop_m/q.gravity_m_s2))
    if not math.isfinite(time) or time<=0:raise ValueError('positive flight time exceeds finite numerical support')
    velocity=q.horizontal_distance_m/time
    if not math.isfinite(velocity) or velocity<=0:raise ValueError('positive horizontal speed exceeds finite numerical support')
    vals=dict(parcel_flight_time_s=time,tracked_horizontal_velocity_m_s=velocity,downward_velocity_at_observation_m_s=q.gravity_m_s2*time)
    notes=[]
    if q.actual_normal_jet_area_m2 is not None and q.tracked_to_section_mean_velocity_factor is not None and q.actual_area_and_velocity_profile_conversion_reviewed:
        mean=velocity*q.tracked_to_section_mean_velocity_factor;vals.update(section_mean_exit_velocity_m_s=mean,volume_flow_m3_s=q.actual_normal_jet_area_m2*mean)
    else:notes.append('actual jet area or tracked-to-mean conversion remains unavailable/unreviewed; volume flow is not inferred')
    if not all(math.isfinite(x) for x in vals.values()) or time<=0:raise ValueError('ballistic observation exceeds positive finite numerical support')
    return Calculation(values=vals,notes=tuple(notes))
