"""Independent continuity/Euler references on actual annular cone geometry."""
import math
import pytest
from scipy.integrate import quad,solve_ivp
from pipeline_integrity.liquid_cone_meter import LiquidConeMeterRequest as R,evaluate_liquid_cone_meter as evaluate


def request(D,Dc,p1,p2,rho=823.,mu=.0012,C=1.):
 return R(actual_pipe_inner_diameter_m=D,actual_cone_end_diameter_m=Dc,upstream_pressure_pa_abs=p1,downstream_pressure_pa_abs=p2,density_kg_m3=rho,dynamic_viscosity_pa_s=mu,discharge_coefficient=C,coefficient_basis_id='actual supplied geometry/tap Cd',actual_tap_geometry_basis_id='upstream pipe and cone downstream face',beta_support=(.05,.999),pipe_reynolds_support=(0,1e12),actual_single_liquid_incompressible_nonflashing_and_taps_reviewed=True,coefficient_calibration_support_reviewed=True)


@pytest.mark.parametrize('D,Dc',[(.08,.035),(.11,.08),(.2,.11),(.06,.05)])
@pytest.mark.parametrize('Q',[.0003,.0011,.003])
def test_actual_cone_annulus_euler_pressure_coordinate_not_circular_hole(D,Dc,Q):
 rho=823.;A1=quad(lambda r:2*math.pi*r,0,D/2,epsabs=1e-15)[0]
 A2=quad(lambda r:2*math.pi*r,Dc/2,D/2,epsabs=1e-15)[0]
 L=.31;p1=8e6
 # Resolve a smooth actual passage-area contraction. Euler pressure gradient
 # is -rho*u*du/dx, with Q constant by continuity, not a meter formula call.
 def area(x):return A1+(A2-A1)*(.5-.5*math.cos(math.pi*x/L))
 def area_prime(x):return (A2-A1)*.5*math.pi/L*math.sin(math.pi*x/L)
 sol=solve_ivp(lambda x,p:[rho*Q**2*area_prime(x)/area(x)**3],(0,L),[p1],rtol=1e-12,atol=1e-7,max_step=L/400)
 p2=sol.y[0,-1]
 out=evaluate(request(D,Dc,p1,p2));assert out.state=='computed'
 assert out.volume_flow_m3_s==pytest.approx(Q,rel=2e-7)
 assert out.mass_flow_kg_s==pytest.approx(rho*Q,rel=2e-7)
 assert out.actual_open_annulus_area_m2==pytest.approx(A2,rel=2e-15)
 assert out.area_equivalent_beta**2==pytest.approx(A2/A1,rel=2e-15)
 kinetic_pressure=.5*rho*((out.volume_flow_m3_s/A2)**2-(out.volume_flow_m3_s/A1)**2)
 assert kinetic_pressure==pytest.approx(p1-p2,rel=5e-14)


@pytest.mark.parametrize('coefficient',[.61,.78,.94])
def test_supplied_coefficient_scales_actual_flow_not_permanent_pressure_loss(coefficient):
 args=(.1,.07,4e5,3.9e5)
 ideal=evaluate(request(*args));calibrated=evaluate(request(*args,C=coefficient))
 assert calibrated.volume_flow_m3_s==pytest.approx(coefficient*ideal.volume_flow_m3_s,rel=2e-15)
 assert calibrated.measured_pressure_difference_pa==10000.
 assert 'permanent_pressure_loss' not in calibrated.model_dump()


def test_zero_flow_and_forward_calibration_reverse_guard():
 assert evaluate(request(.1,.07,4e5,4e5)).volume_flow_m3_s==0
 assert evaluate(request(.1,.07,3.9e5,4e5)).state=='out_of_scope'


@pytest.mark.parametrize('field',['density_kg_m3','dynamic_viscosity_pa_s','discharge_coefficient','coefficient_basis_id','actual_tap_geometry_basis_id','beta_support','pipe_reynolds_support'])
def test_unknown_actual_state_or_calibration_withheld(field):
 r=request(.1,.07,4e5,3.9e5).model_copy(update={field:None})
 assert evaluate(r).state=='needs_data'


def test_actual_geometry_reynolds_and_review_support_guards():
 r=request(.1,.07,4e5,3.9e5)
 for update in [{'beta_support':(.9,.95)},{'pipe_reynolds_support':(0,1)},{'actual_single_liquid_incompressible_nonflashing_and_taps_reviewed':False},{'coefficient_calibration_support_reviewed':False}]:
  out=evaluate(r.model_copy(update=update));assert out.state in ('needs_data','out_of_scope')


def test_geometry_meaning_and_replay():
 r=request(.1,.07,4e5,3.9e5)
 for diameter in [.1,.11]:
  with pytest.raises(ValueError):R.model_validate({**r.model_dump(),'actual_cone_end_diameter_m':diameter})
 assert evaluate(R.model_validate_json(r.model_dump_json()))==evaluate(r)
