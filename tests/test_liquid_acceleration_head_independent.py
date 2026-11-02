"""Independent Euler pressure-force and reversible kinetic-work references."""
import math
import pytest
from scipy.integrate import quad
from pipeline_integrity.liquid_acceleration_head import (
 LiquidAccelerationSegment as S,LiquidAccelerationHeadRequest as R,observe_liquid_acceleration_head as evaluate,
)


def request(rate,q=.003,**kw):
 return R(segments=(S(length_m=7.2,actual_flow_area_m2=.008),S(length_m=13.1,actual_flow_area_m2=.013)),density_kg_m3=831.,actual_common_flow_derivative_m3_s2=rate,actual_common_flow_m3_s=q,forcing_basis_id='actual piston displacement derivative',actual_forcing_and_single_liquid_rigid_column_reviewed=True,**kw)


@pytest.mark.parametrize('angle',[0.,.4,1.2,2.,3.2,4.1,5.5])
def test_piston_motion_distributed_euler_pressure_force_and_work(angle):
 area_piston=.0017;stroke_radius=.024;omega=7.1
 # A supplied physical piston x=r*sin(wt), not a fitted pump-type/rpm factor.
 q=area_piston*stroke_radius*omega*math.cos(angle)
 rate=-area_piston*stroke_radius*omega**2*math.sin(angle)
 r=request(rate,q);out=evaluate(r);assert out.state=='computed'
 segment_pressures=[];energy=0;denergy=0
 for segment in r.segments:
  local_a=rate/segment.actual_flow_area_m2;local_v=q/segment.actual_flow_area_m2
  # Integrate Euler force on an actual fluid segment. Pressure times area
  # balances the integrated actual material acceleration, not a K loss.
  force=quad(lambda x:r.density_kg_m3*segment.actual_flow_area_m2*local_a,0,segment.length_m)[0]
  segment_pressures.append(force/segment.actual_flow_area_m2)
  energy+=quad(lambda x:.5*r.density_kg_m3*segment.actual_flow_area_m2*local_v**2,0,segment.length_m)[0]
  denergy+=quad(lambda x:r.density_kg_m3*segment.actual_flow_area_m2*local_v*local_a,0,segment.length_m)[0]
 assert out.segment_signed_pressure_pa==pytest.approx(segment_pressures,rel=3e-15,abs=1e-13)
 assert out.signed_inlet_minus_outlet_inertial_pressure_pa==pytest.approx(sum(segment_pressures),rel=3e-15,abs=1e-13)
 assert out.instantaneous_kinetic_energy_j==pytest.approx(energy,rel=3e-15)
 assert out.signed_pressure_work_rate_w==pytest.approx(denergy,rel=3e-15,abs=1e-13)
 assert out.dissipated_power_w==0


def test_integrated_pressure_work_equals_kinetic_change_and_period_zero_work():
 omega=6.2;amplitude=.003;mean=.004
 def state(t):return evaluate(request(amplitude*omega*math.cos(omega*t),mean+amplitude*math.sin(omega*t)))
 end=.37
 work=quad(lambda t:state(t).signed_pressure_work_rate_w,0,end,epsabs=1e-12)[0]
 assert work==pytest.approx(state(end).instantaneous_kinetic_energy_j-state(0).instantaneous_kinetic_energy_j,abs=2e-12)
 cyclic=quad(lambda t:state(t).signed_pressure_work_rate_w,0,2*math.pi/omega,epsabs=1e-12)[0]
 assert cyclic==pytest.approx(0,abs=3e-12)


def test_inertial_head_not_friction_and_deceleration_returns_energy():
 out=evaluate(request(-.02,.004))
 assert out.signed_inertial_head_m<0;assert out.signed_pressure_work_rate_w<0
 assert out.signed_inertial_head_m*out.hydraulic_inertance_kg_m4> -float('inf')
 assert out.signed_inertial_head_m==pytest.approx(out.signed_inlet_minus_outlet_inertial_pressure_pa/(831*9.80665))


def test_zero_rate_and_zero_length_do_not_invent_dissipation():
 out=evaluate(request(0.,.003));assert out.signed_inlet_minus_outlet_inertial_pressure_pa==0;assert out.instantaneous_kinetic_energy_j>0
 r=request(.2).model_copy(update={'segments':(S(length_m=0,actual_flow_area_m2=.01),)})
 out=evaluate(r);assert out.hydraulic_inertance_kg_m4==0;assert out.signed_inertial_head_m==0


def test_pressure_observer_does_not_require_flow_for_optional_kinetic_output():
 out=evaluate(request(.02,None));assert out.state=='computed';assert out.instantaneous_kinetic_energy_j is None;assert out.signed_pressure_work_rate_w is None


@pytest.mark.parametrize('field',['density_kg_m3','actual_common_flow_derivative_m3_s2','forcing_basis_id'])
def test_actual_input_missingness_is_not_a_pump_speed_default(field):
 assert evaluate(request(.02).model_copy(update={field:None})).state=='needs_data'


def test_forcing_review_and_replay():
 r=request(.02);assert evaluate(r.model_copy(update={'actual_forcing_and_single_liquid_rigid_column_reviewed':False})).state=='needs_data'
 assert evaluate(R.model_validate_json(r.model_dump_json()))==evaluate(r)


def test_nonrepresentable_forcing_and_geometry_are_withheld():
 r=request(.02).model_copy(update={'actual_common_flow_derivative_m3_s2':float('inf')});assert evaluate(r).state=='out_of_scope'
 r=request(1e300).model_copy(update={'segments':(S(length_m=1e300,actual_flow_area_m2=1e-300),)});assert evaluate(r).state=='out_of_scope'
