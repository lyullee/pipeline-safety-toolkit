"""Independent compressible-liquid piston and mass-transfer references."""
import math
import pytest
from scipy.integrate import solve_ivp, quad
from pipeline_integrity.liquid_chamber_cycle import (
 ChamberLiquidEndpoint as E,LiquidChamberCycleRequest as R,evaluate_liquid_chamber_cycle as evaluate,
)


def request(rhos=810.,rhod=835.,vc=2.1e-4,vs=1.7e-3,slip=.012):
 return R(suction=E(pressure_pa_abs=2e5,temperature_K=302.,density_kg_m3=rhos,phase='liquid',property_basis_id='reviewed suction state'),discharge=E(pressure_pa_abs=12e6,temperature_K=308.,density_kg_m3=rhod,phase='liquid',property_basis_id='reviewed discharge state'),end_discharge_dead_volume_m3=vc,swept_volume_m3=vs,slip_fraction_of_swept_suction_volume=slip,single_liquid_closed_mass_phases_and_ideal_valve_endpoints_reviewed=True,common_fluid_and_slip_reference_reviewed=True)


@pytest.mark.parametrize('kappa,vc,slip',[(2e-10,1e-4,0),(8e-10,2e-4,.011),(2e-9,7e-4,.02),(4e-9,1.2e-3,.03)])
def test_closed_mass_pressure_volume_ode_and_full_piston_cycle(kappa,vc,slip):
 ps,pd=2e5,12e6;rhos=810.;rhod=rhos*math.exp(kappa*(pd-ps));vs=.0017
 def reaching_suction(v,p):return p[0]-ps
 reaching_suction.terminal=True;reaching_suction.direction=-1
 # d(rho V)=0 and d rho/dp=kappa*rho give this ODE independently.
 closed=solve_ivp(lambda v,p:[-1/(kappa*v)],(vc,vc+vs),[pd],events=reaching_suction,rtol=2e-12,atol=1e-7,max_step=vs/500)
 assert len(closed.t_events[0])==1
 opening=closed.t_events[0][0]
 end_suction=vc+vs
 incoming_mass=quad(lambda v:rhos,opening,end_suction,epsabs=1e-15)[0]
 start_discharge=incoming_mass/rhod+vc
 discharged_mass=quad(lambda v:rhod,vc,start_discharge,epsabs=1e-15)[0]
 leak_mass=quad(lambda s:rhos*vs*slip*(1+.7*math.sin(2*math.pi*s)),0,1,epsabs=1e-15)[0]
 out=evaluate(request(rhos,rhod,vc,vs,slip));assert out.state=='computed'
 assert out.suction_valve_opening_chamber_volume_m3==pytest.approx(opening,rel=2e-11)
 assert out.gross_transferred_mass_kg_per_cycle==pytest.approx(incoming_mass,rel=2e-11)
 assert out.gross_transferred_mass_kg_per_cycle==pytest.approx(discharged_mass,rel=2e-11)
 assert out.net_transferred_mass_kg_per_cycle==pytest.approx(incoming_mass-leak_mass,rel=2e-11)
 assert out.suction_referenced_volumetric_efficiency==pytest.approx((incoming_mass-leak_mass)/(rhos*vs),rel=2e-11)
 assert out.net_discharge_reference_volume_m3_per_cycle*rhod==pytest.approx(out.net_suction_reference_volume_m3_per_cycle*rhos,rel=2e-15)


@pytest.mark.parametrize('dead_volume',[0.,1e-4,2e-3])
def test_incompressible_limit_and_slip_basis(dead_volume):
 out=evaluate(request(998.,998.,dead_volume,.001,.021))
 assert out.state=='computed'
 assert out.closed_valve_reexpansion_volume_m3==0
 assert out.suction_referenced_volumetric_efficiency==pytest.approx(.979)
 assert out.gross_transferred_mass_kg_per_cycle==pytest.approx(.998)


def test_zero_clearance_removes_liquid_compression_delivery_penalty():
 out=evaluate(request(810.,900.,0,.001,.02))
 assert out.suction_referenced_volumetric_efficiency==pytest.approx(.98)
 assert out.residual_liquid_mass_kg==0
 assert out.net_discharge_reference_volume_m3_per_cycle==pytest.approx(.00098*810/900)


def test_extensive_scaling_and_common_pressure_datum_do_not_change_efficiency():
 a=request();b=a.model_copy(update={'end_discharge_dead_volume_m3':a.end_discharge_dead_volume_m3*7,'swept_volume_m3':a.swept_volume_m3*7})
 x,y=evaluate(a),evaluate(b)
 assert y.suction_referenced_volumetric_efficiency==x.suction_referenced_volumetric_efficiency
 assert y.net_transferred_mass_kg_per_cycle==pytest.approx(7*x.net_transferred_mass_kg_per_cycle)
 shifted=a.model_copy(update={'suction':a.suction.model_copy(update={'pressure_pa_abs':4e5}),'discharge':a.discharge.model_copy(update={'pressure_pa_abs':12.2e6})})
 # This is an observer of supplied densities; pressure alone cannot silently
 # recompute them or substitute a gas pressure ratio.
 assert evaluate(shifted).suction_referenced_volumetric_efficiency==x.suction_referenced_volumetric_efficiency


def test_finite_water_contraction_coordinate_matches_density_mass_balance():
 x=.0037;rhos=997.;rhod=rhos/(1-x);r=request(rhos,rhod,vc=.0023,vs=.0015,slip=.019)
 out=evaluate(r)
 # Explicit finite volume contraction, not an unidentified differential
 # isothermal compressibility or imported historical coefficient table.
 initial_volume=r.end_discharge_dead_volume_m3/(1-x)
 independently_available=r.swept_volume_m3-(initial_volume-r.end_discharge_dead_volume_m3)
 assert out.net_suction_reference_volume_m3_per_cycle==pytest.approx(independently_available-.019*r.swept_volume_m3,rel=3e-15)


@pytest.mark.parametrize('delta',[1e-3,1e-6,1e-9])
def test_small_density_increment_continuous_liquid_limit(delta):
 out=evaluate(request(1000.,1000.+delta,vc=.001,vs=.002,slip=0))
 assert out.closed_valve_reexpansion_volume_m3==pytest.approx(.001*((1000.+delta)-1000.)/1000.,rel=1e-15)
 assert out.suction_referenced_volumetric_efficiency<=1.


@pytest.mark.parametrize('field',['density_kg_m3','property_basis_id'])
def test_unknown_endpoint_data_not_replaced_by_water_or_gas(field):
 r=request();r=r.model_copy(update={'discharge':r.discharge.model_copy(update={field:None})})
 assert evaluate(r).state=='needs_data'


def test_missing_slip_or_review_is_not_assumed_zero():
 r=request()
 for change in [{'slip_fraction_of_swept_suction_volume':None},{'common_fluid_and_slip_reference_reviewed':False},{'single_liquid_closed_mass_phases_and_ideal_valve_endpoints_reviewed':False}]:
  assert evaluate(r.model_copy(update=change)).state=='needs_data'


@pytest.mark.parametrize('phase',['gas','two_phase','supercritical','unknown'])
def test_unsupported_or_unknown_endpoint_phase_withheld(phase):
 r=request();out=evaluate(r.model_copy(update={'suction':r.suction.model_copy(update={'phase':phase})}))
 assert out.state==('needs_data' if phase=='unknown' else 'out_of_scope')


def test_impossible_stroke_density_reversal_and_excessive_slip_not_clipped():
 assert evaluate(request(800.,1200.,vc=.1,vs=.001,slip=0)).state=='out_of_scope'
 assert evaluate(request(900.,800.)).state=='out_of_scope'
 assert evaluate(request(800.,1000.,vc=.001,vs=.001,slip=.9)).state=='out_of_scope'


def test_zero_delivery_boundary_is_preserved_without_artificial_positive_flow():
 out=evaluate(request(800.,1000.,vc=.001,vs=.001,slip=.75))
 assert out.state=='computed';assert out.net_transferred_mass_kg_per_cycle==0;assert out.suction_referenced_volumetric_efficiency==0


def test_invalid_pressure_order_and_contract_replay():
 r=request()
 with pytest.raises(ValueError):R.model_validate({**r.model_dump(),'discharge':{**r.discharge.model_dump(),'pressure_pa_abs':1e5}})
 assert evaluate(R.model_validate_json(r.model_dump_json()))==evaluate(r)


def test_existing_coolprop_liquid_states_endpoint_mass_reference():
 pytest.importorskip('CoolProp')
 from pipeline_integrity.thermo import CoolPropProvider,StateRequest
 from CoolProp import AbstractState,PT_INPUTS
 provider=CoolPropProvider(use_cache=False);ps,pd,T=3e5,17e6,310.
 states=[provider.resolve(StateRequest(fluid='Water',backend='HEOS',inputs={'P':p,'T':T},single_phase_required=True)) for p in (ps,pd)]
 r=request(states[0].density_kg_m3,states[1].density_kg_m3,vc=.0002,vs=.001,slip=.01)
 r=r.model_copy(update={'suction':E(pressure_pa_abs=ps,temperature_K=T,density_kg_m3=states[0].density_kg_m3,phase='liquid',property_basis_id='native HEOS Water'),'discharge':E(pressure_pa_abs=pd,temperature_K=T,density_kg_m3=states[1].density_kg_m3,phase='liquid',property_basis_id='native HEOS Water')})
 out=evaluate(r);assert out.state=='computed'
 low=AbstractState('HEOS','Water');low.update(PT_INPUTS,ps,T);rho_s=low.rhomass();low.update(PT_INPUTS,pd,T);rho_d=low.rhomass()
 high_volume_mass=rho_s*(r.end_discharge_dead_volume_m3+r.swept_volume_m3)
 residual_mass=rho_d*r.end_discharge_dead_volume_m3
 transferred=high_volume_mass-residual_mass-rho_s*r.swept_volume_m3*.01
 assert out.net_transferred_mass_kg_per_cycle==pytest.approx(transferred,rel=2e-14)
