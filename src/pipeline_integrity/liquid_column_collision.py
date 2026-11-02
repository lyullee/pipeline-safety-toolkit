"""Two incident liquid acoustic states and a separate constant-head return model.

Shared characteristic transport supplies the incoming invariants. A local
two-column state admits motion on both sides and is different from a rigid-wall
reflection. Prescribed incoming states are not a prediction of vapor collapse.
"""
from __future__ import annotations
import math
import numpy as np
from pydantic import Field,model_validator
from typing import Literal
from .contracts import Contract,Positive
from .moc_characteristics import characteristic_invariants


class LiquidAcousticColumnState(Contract):
    density_kg_m3: Positive
    wave_speed_m_s: Positive
    pressure_pa_abs: Positive
    signed_axial_velocity_m_s: float


class LiquidColumnCollisionRequest(Contract):
    left: LiquidAcousticColumnState
    right: LiquidAcousticColumnState
    reference_pressure_pa_abs: Positive
    vapor_pressure_pa_abs: Positive
    maximum_acoustic_mach: Positive = Field(default=.05,le=.1)
    maximum_linear_storage_strain: Positive = Field(default=.01,le=.05)
    ordinary_liquid_impedance_and_local_linear_states_reviewed: bool = False


class LiquidColumnCollisionResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    interface_pressure_pa_abs: Positive | None = None
    interface_signed_velocity_m_s: float | None = None
    left_pressure_increment_pa: float | None = None
    right_pressure_increment_pa: float | None = None
    left_impedance_pa_s_m: Positive | None = None
    right_impedance_pa_s_m: Positive | None = None
    relative_approach_speed_m_s: float | None = None
    left_outgoing_wave_speed_m_s: float | None = None
    right_outgoing_wave_speed_m_s: float | None = None
    energy_change_rate_per_area_W_m2: float | None = None
    incident_energy_flux_difference_W_m2: float | None = None
    quadratic_acoustic_energy_residual_W_m2: float | None = None
    issues: tuple[str,...] = ()
    vapor_collapse_kinetics_predicted: Literal[False] = False
    nonlinear_total_energy_qualification_claimed: Literal[False] = False


def _incoming_pressure_invariants(state,reference_pressure):
    # Evaluate the unchanged head/discharge primitive per unit cross-sectional
    # area. Gravity is only a head-to-pressure coordinate normalization here.
    gravity=9.80665
    head=(state.pressure_pa_abs-reference_pressure)/(state.density_kg_m3*gravity)
    h=np.full(3,head);flow=np.full(3,state.signed_axial_velocity_m_s)
    _,_,minus,plus=characteristic_invariants(h,flow,flow,state.wave_speed_m_s/gravity,0.)
    return state.density_kg_m3*gravity*plus,state.density_kg_m3*gravity*minus


def evaluate_liquid_column_collision(request:LiquidColumnCollisionRequest):
    """Match pressure and axial velocity between two ordinary acoustic liquids.

    Positive velocity points from left to right for both input states. A right
    column approaching from the right therefore has negative velocity. Pressure
    is absolute; outputs separately report its increments from each incident
    state. A common conduit cross section is assumed. Unequal impedances are a
    declared linear-interface extension, not a different-area junction model.
    """
    q=LiquidColumnCollisionRequest.model_validate(request.model_dump(mode='python'))
    if not q.ordinary_liquid_impedance_and_local_linear_states_reviewed:
        return LiquidColumnCollisionResult(status='needs_data',issues=('ordinary Z=rho*c impedance and local linear-acoustic liquid states require review',))
    l,r=q.left,q.right
    if min(l.pressure_pa_abs,r.pressure_pa_abs)<q.vapor_pressure_pa_abs:
        return LiquidColumnCollisionResult(status='out_of_scope',issues=('incident liquid state below supplied vapor pressure',))
    zl=l.density_kg_m3*l.wave_speed_m_s;zr=r.density_kg_m3*r.wave_speed_m_s
    plus,_=_incoming_pressure_invariants(l,q.reference_pressure_pa_abs)
    _,minus=_incoming_pressure_invariants(r,q.reference_pressure_pa_abs)
    velocity=(plus-minus)/(zl+zr)
    pressure=q.reference_pressure_pa_abs+(zr*plus+zl*minus)/(zl+zr)
    numbers=(zl,zr,plus,minus,velocity,pressure)
    if not all(math.isfinite(v) for v in numbers):raise ValueError('two-column acoustic state exceeds finite support')
    if pressure<q.vapor_pressure_pa_abs:
        return LiquidColumnCollisionResult(status='out_of_scope',issues=('interface would cross vapor pressure; a separate cavity model is required',))
    for state in (l,r):
        if max(abs(state.signed_axial_velocity_m_s),abs(velocity))/state.wave_speed_m_s>q.maximum_acoustic_mach:
            return LiquidColumnCollisionResult(status='out_of_scope',issues=('reviewed small acoustic-Mach support exceeded',))
        if max(abs(state.pressure_pa_abs-q.reference_pressure_pa_abs),abs(pressure-q.reference_pressure_pa_abs))/(state.density_kg_m3*state.wave_speed_m_s**2)>q.maximum_linear_storage_strain:
            return LiquidColumnCollisionResult(status='out_of_scope',issues=('reviewed linear acoustic storage-strain support exceeded',))
    def energy(state,p,v):return (p-q.reference_pressure_pa_abs)**2/(2*state.density_kg_m3*state.wave_speed_m_s**2)+.5*state.density_kg_m3*v*v
    change=l.wave_speed_m_s*(energy(l,pressure,velocity)-energy(l,l.pressure_pa_abs,l.signed_axial_velocity_m_s))
    change+=r.wave_speed_m_s*(energy(r,pressure,velocity)-energy(r,r.pressure_pa_abs,r.signed_axial_velocity_m_s))
    flux=(l.pressure_pa_abs-q.reference_pressure_pa_abs)*l.signed_axial_velocity_m_s-(r.pressure_pa_abs-q.reference_pressure_pa_abs)*r.signed_axial_velocity_m_s
    return LiquidColumnCollisionResult(status='computed',interface_pressure_pa_abs=pressure,interface_signed_velocity_m_s=velocity,
        left_pressure_increment_pa=pressure-l.pressure_pa_abs,right_pressure_increment_pa=pressure-r.pressure_pa_abs,
        left_impedance_pa_s_m=zl,right_impedance_pa_s_m=zr,relative_approach_speed_m_s=l.signed_axial_velocity_m_s-r.signed_axial_velocity_m_s,
        left_outgoing_wave_speed_m_s=-l.wave_speed_m_s,right_outgoing_wave_speed_m_s=r.wave_speed_m_s,
        energy_change_rate_per_area_W_m2=change,incident_energy_flux_difference_W_m2=flux,quadratic_acoustic_energy_residual_W_m2=change-flux)


class ConstantHeadColumnReturnRequest(Contract):
    density_kg_m3: Positive
    wave_speed_m_s: Positive
    downstream_column_length_m: Positive
    initial_velocity_away_from_closed_valve_m_s: Positive
    reservoir_pressure_pa_abs: Positive
    vapor_pressure_pa_abs: Positive
    maximum_gap_fraction_of_length: Positive = Field(default=.05,le=.2)
    maximum_acoustic_crossing_to_turn_time: Positive = Field(default=.2,le=1.)
    maximum_acoustic_mach: Positive = Field(default=.05,le=.1)
    observation_times_s: tuple[float,...] = ()
    constant_pressure_small_gap_rigid_column_reviewed: bool = False
    @model_validator(mode='after')
    def source_order(self):
        if self.reservoir_pressure_pa_abs<=self.vapor_pressure_pa_abs:raise ValueError('positive reservoir-minus-vapor pressure drive required')
        if any(t<0 for t in self.observation_times_s) or any(v<=u for u,v in zip(self.observation_times_s,self.observation_times_s[1:])):raise ValueError('observation times must be nonnegative and strictly ordered')
        return self


class ColumnReturnObservation(Contract):
    time_s: float
    gap_displacement_m: float
    signed_velocity_away_from_valve_m_s: float
    kinetic_energy_per_area_J_m2: float
    pressure_work_per_area_J_m2: float
    kinetic_work_residual_J_m2: float


class ConstantHeadColumnReturnResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    signed_acceleration_m_s2: float | None = None
    time_to_reverse_s: Positive | None = None
    time_to_return_to_valve_s: Positive | None = None
    maximum_gap_displacement_m: Positive | None = None
    signed_impact_velocity_m_s: float | None = None
    instantaneous_rigid_wall_pressure_increment_pa: Positive | None = None
    instantaneous_wall_pressure_reference_pa_abs: Positive | None = None
    acoustic_crossing_to_turn_time: Positive | None = None
    observations: tuple[ColumnReturnObservation,...] = ()
    issues: tuple[str,...] = ()
    condensation_or_phase_change_solved: Literal[False] = False


def evaluate_constant_head_column_return(request:ConstantHeadColumnReturnRequest):
    """Constant-pressure motion before impact, plus a local rigid-wall pulse scale.

    The valve is on the left; positive displacement/velocity points away from
    it. Constant reservoir-minus-vapor pressure decelerates the prescribed rigid
    column. Return takes twice the reversal time. Supplied wave speed is used
    only for acoustic applicability and the instantaneous rigid-wall reference;
    no finite valve closure, reflected waveform or vapor kinetics is advanced.
    """
    q=ConstantHeadColumnReturnRequest.model_validate(request.model_dump(mode='python'))
    if not q.constant_pressure_small_gap_rigid_column_reviewed:return ConstantHeadColumnReturnResult(status='needs_data',issues=('constant pressure, fixed-column inertia, small gap and negligible losses require review',))
    drive=q.reservoir_pressure_pa_abs-q.vapor_pressure_pa_abs
    mass_per_area=q.density_kg_m3*q.downstream_column_length_m
    acceleration=drive/mass_per_area;initial=q.initial_velocity_away_from_closed_valve_m_s
    turn=initial/acceleration;returned=2*turn;gap=initial*turn/2
    ratio=q.downstream_column_length_m/q.wave_speed_m_s/turn
    if not all(math.isfinite(x) and x>0 for x in (acceleration,turn,returned,gap,ratio)):raise ValueError('constant-head column motion exceeds finite positive support')
    if gap/q.downstream_column_length_m>q.maximum_gap_fraction_of_length or ratio>q.maximum_acoustic_crossing_to_turn_time or initial/q.wave_speed_m_s>q.maximum_acoustic_mach:
        return ConstantHeadColumnReturnResult(status='out_of_scope',issues=('reviewed small-gap, rapid acoustic adjustment or small-Mach approximation exceeded',))
    if any(t>returned for t in q.observation_times_s):raise ValueError('post-impact observation requires a different transient model')
    incident=LiquidAcousticColumnState(density_kg_m3=q.density_kg_m3,wave_speed_m_s=q.wave_speed_m_s,pressure_pa_abs=q.vapor_pressure_pa_abs,signed_axial_velocity_m_s=-initial)
    _,wall_pulse=_incoming_pressure_invariants(incident,q.vapor_pressure_pa_abs)
    observations=[];initial_energy=.5*mass_per_area*initial*initial
    for time in q.observation_times_s:
        displacement=.5*acceleration*time*(returned-time);velocity=initial*(1-2*time/returned)
        energy=.5*mass_per_area*velocity*velocity;work=-drive*displacement
        observations.append(ColumnReturnObservation(time_s=time,gap_displacement_m=displacement,signed_velocity_away_from_valve_m_s=velocity,
            kinetic_energy_per_area_J_m2=energy,pressure_work_per_area_J_m2=work,kinetic_work_residual_J_m2=energy-initial_energy-work))
    return ConstantHeadColumnReturnResult(status='computed',signed_acceleration_m_s2=-acceleration,time_to_reverse_s=turn,time_to_return_to_valve_s=returned,
        maximum_gap_displacement_m=gap,signed_impact_velocity_m_s=-initial,instantaneous_rigid_wall_pressure_increment_pa=wall_pulse,
        instantaneous_wall_pressure_reference_pa_abs=q.vapor_pressure_pa_abs+wall_pulse,acoustic_crossing_to_turn_time=ratio,observations=tuple(observations))
