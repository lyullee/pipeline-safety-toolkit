"""Independent actual/reference mass and boundary-volume transfer references."""
import math
import pytest
from scipy.integrate import quad,solve_ivp
from pipeline_integrity.contracts import EvidenceRef,RunContext
from pipeline_integrity.tanks import AxisymmetricTank,CalibratedTankCurve,TankInventoryRequest,TankObservation,DeclaredTankDensity,PropertyTankDensity
from pipeline_integrity.tank_usable_inventory import TankUsableInventoryRequest as R,evaluate_tank_usable_inventory as evaluate
from pipeline_integrity.thermo import StateRequest

CONTEXT=RunContext(asset_id='tank',scenario_id='authored observer',clock_id='elapsed',origin='synthetic',evidence=(EvidenceRef(id='e',citation='Independently authored tank geometry/state',origin='synthetic'),))


def obs(height,time=100.):
 return TankObservation(tank_id='tank',clock_id='elapsed',time_s=time,quantity='level_from_internal_bottom',value=height,unit='m',evidence_id='e')


def density(rho):return DeclaredTankDensity(phase='liquid',fluid='AuthoredFuel',density_kg_m3=rho,property_basis='actual or reference state declared separately',evidence_id='e')


def request(level=2.1,**kw):
 geometry=AxisymmetricTank(inner_diameter_m=1.7,straight_length_m=4.6,horizontal=False,evidence_id='e')
 inventory=TankInventoryRequest(geometry=geometry,observation=obs(level),liquid_density=density(820.))
 data=dict(inventory_request=inventory,pump_intake_cutoff_observation=obs(.5),reference_liquid_density=density(847.),actual_and_reference_common_mass_basis_id='same liquid actual/ref density',geometry_cutoff_same_internal_datum_and_uniform_states_reviewed=True,actual_and_reference_same_liquid_and_mass_basis_reviewed=True)
 data.update(kw);return R(**data)


@pytest.mark.parametrize('level',[0.,.2,.5,2.1,4.6])
def test_actual_cylinder_cross_section_quadrature_pump_cutoff_and_common_mass(level):
 q=request(level);out=evaluate(q,CONTEXT);assert out.state=='computed'
 A=quad(lambda r:2*math.pi*r,0,1.7/2,epsabs=1e-15)[0]
 gross=quad(lambda z:A,0,level)[0];cutoff=quad(lambda z:A,0,.5)[0];capacity=quad(lambda z:A,0,4.6)[0]
 pumpable=quad(lambda z:A,.5,level)[0] if level>.5 else 0.
 assert out.gross_actual_volume_m3==pytest.approx(gross,rel=2e-14,abs=1e-15)
 assert out.geometric_nonpumpable_capacity_m3==pytest.approx(cutoff)
 assert out.current_pumpable_actual_volume_m3==pytest.approx(pumpable,rel=2e-14,abs=1e-15)
 assert out.current_nonpumpable_actual_volume_m3+out.current_pumpable_actual_volume_m3==pytest.approx(gross)
 assert out.physical_ullage_m3+gross==pytest.approx(capacity)
 assert out.below_pump_intake_cutoff==(level<.5)
 assert out.gross_mass_kg==pytest.approx(820*gross)
 assert out.gross_reference_volume_m3*847==pytest.approx(820*gross)
 assert out.nonpumpable_mass_kg+out.pumpable_mass_kg==pytest.approx(out.gross_mass_kg)
 assert out.physical_operating_safety_limits_selected is False


def calibrated_request(previous_height):
 curve=CalibratedTankCurve(heights_m=(0.,1.,2.,3.),volumes_m3=(0.,1.,3.,6.),evidence_id='e')
 q=request(1.7);native=q.inventory_request.model_copy(update={'geometry':curve})
 return q.model_copy(update={'inventory_request':native,'pump_intake_cutoff_observation':obs(.7),'previous_observation':obs(previous_height,0.),'level_history_common_geometry_state_reviewed':True,'constant_mean_flow_for_boundary_time_reviewed':True})


@pytest.mark.parametrize('previous_height',[1.1,2.5])
def test_calibrated_nonlinear_area_history_rate_and_independent_transfer_ode(previous_height):
 q=calibrated_request(previous_height);out=evaluate(q,CONTEXT);assert out.state=='computed'
 def volume(h):return quad(lambda z:1. if z<1 else (2. if z<2 else 3.),0,h,points=[p for p in [1,2] if p<h],epsabs=1e-12)[0]
 current=volume(1.7);previous=volume(previous_height);rate=(current-previous)/100
 assert out.signed_mean_actual_volume_flow_m3_s==pytest.approx(rate,rel=2e-14)
 boundary=volume(3.) if rate>0 else volume(.7)
 def reached(t,v):return v[0]-boundary
 reached.terminal=True
 solved=solve_ivp(lambda t,v:[rate],(0,2000),[current],events=reached,rtol=1e-12,atol=1e-12,max_step=20)
 assert len(solved.t_events[0])==1
 result_time=out.conditional_time_to_physical_full_s if rate>0 else out.conditional_time_to_pump_intake_cutoff_s
 assert result_time==pytest.approx(solved.t_events[0][0],rel=2e-13)
 assert out.signed_corresponding_mass_flow_kg_s==pytest.approx(rate*820.)


def test_no_constant_future_rate_is_inferred_from_observed_history():
 q=calibrated_request(1.1).model_copy(update={'constant_mean_flow_for_boundary_time_reviewed':False})
 out=evaluate(q,CONTEXT);assert out.signed_mean_actual_volume_flow_m3_s>0
 assert out.conditional_time_to_physical_full_s is None
 assert out.conditional_time_to_pump_intake_cutoff_s is None


@pytest.mark.parametrize('flow',[0.,.01,-.02])
def test_supplied_actual_state_flow_and_zero_boundary_time(flow):
 q=request(signed_mean_actual_volume_flow_m3_s=flow,constant_mean_flow_for_boundary_time_reviewed=True)
 out=evaluate(q,CONTEXT)
 if flow>0:assert out.conditional_time_to_physical_full_s==pytest.approx(out.physical_ullage_m3/flow)
 elif flow<0:assert out.conditional_time_to_pump_intake_cutoff_s==pytest.approx(out.current_pumpable_actual_volume_m3/(-flow))
 else:assert out.conditional_time_to_physical_full_s is None and out.conditional_time_to_pump_intake_cutoff_s is None


def test_explicit_below_intake_volume_does_not_subtract_nonexistent_liquid():
 out=evaluate(request(.2,signed_mean_actual_volume_flow_m3_s=-.01,constant_mean_flow_for_boundary_time_reviewed=True),CONTEXT)
 assert out.state=='computed';assert out.current_pumpable_actual_volume_m3==0
 assert out.current_nonpumpable_actual_volume_m3==out.gross_actual_volume_m3
 assert out.conditional_time_to_pump_intake_cutoff_s==0


def test_actual_reference_density_agrees_with_existing_volumetric_curve_owner():
 from pipeline_integrity.liquid_reference_volume import LiquidReferenceVolumeRequest,evaluate_liquid_reference_volume
 from pipeline_integrity.thermal_expansion import ExpansionCurve,ExpansionKnot
 beta=.0009;T0=300.;T=330.;rhoref=847.
 # A prescribed engineering volumetric tangent integrates dV/dT=beta*Vref.
 reference_volume=3.2
 thermal=solve_ivp(lambda temp,v:[beta*reference_volume],(T0,T),[reference_volume],rtol=1e-12,atol=1e-13)
 actual_volume=thermal.y[0,-1];rho_actual=rhoref*reference_volume/actual_volume
 q=request();native=q.inventory_request.model_copy(update={'observation':TankObservation(tank_id='tank',clock_id='elapsed',time_s=100.,quantity='liquid_volume',value=actual_volume,unit='m^3',evidence_id='e'),'liquid_density':density(rho_actual)})
 out=evaluate(q.model_copy(update={'inventory_request':native}),CONTEXT)
 curve=ExpansionCurve(id='authored volume curve',material_id='authored fuel',knots=(ExpansionKnot(temperature_K=290.,tangent_expansion_per_K=beta),ExpansionKnot(temperature_K=340.,tangent_expansion_per_K=beta)),evidence_id='e',tangent_small_engineering_strain_basis_reviewed=True)
 corrected=evaluate_liquid_reference_volume(LiquidReferenceVolumeRequest(observed_volume_m3=actual_volume,observed_temperature_K=T,reference_temperature_K=T0,volumetric_expansion_curve=curve,common_pressure_and_volumetric_engineering_basis_reviewed=True))
 assert out.gross_reference_volume_m3==pytest.approx(reference_volume,rel=2e-14)
 assert corrected.reference_volume_m3==pytest.approx(out.gross_reference_volume_m3,rel=2e-14)
 assert out.gross_mass_kg==pytest.approx(reference_volume*rhoref,rel=2e-14)


@pytest.mark.parametrize('field',['pump_intake_cutoff_observation','reference_liquid_density','actual_and_reference_common_mass_basis_id'])
def test_missing_cutoff_reference_data_not_replaced_with_zero_or_actual_density(field):
 assert evaluate(request().model_copy(update={field:None}),CONTEXT).state=='needs_data'


def test_unknown_review_state_density_or_history_withheld():
 q=request()
 for update in [{'geometry_cutoff_same_internal_datum_and_uniform_states_reviewed':False},{'actual_and_reference_same_liquid_and_mass_basis_reviewed':False},{'inventory_request':q.inventory_request.model_copy(update={'liquid_density':None})}]:
  assert evaluate(q.model_copy(update=update),CONTEXT).state=='needs_data'
 assert evaluate(calibrated_request(1.1).model_copy(update={'level_history_common_geometry_state_reviewed':False}),CONTEXT).state=='needs_data'


def test_unknown_bad_datum_phase_and_wrong_fluid_remain_diagnostic():
 q=request()
 assert evaluate(q.model_copy(update={'pump_intake_cutoff_observation':obs(7.)}),CONTEXT).state=='out_of_scope'
 with pytest.raises(ValueError):evaluate(q.model_copy(update={'pump_intake_cutoff_observation':obs(.5).model_copy(update={'clock_id':'different'})}),CONTEXT)
 assert evaluate(q.model_copy(update={'reference_liquid_density':density(847.).model_copy(update={'phase':'gas'})}),CONTEXT).state=='out_of_scope'
 assert evaluate(q.model_copy(update={'reference_liquid_density':density(847.).model_copy(update={'fluid':'another'})}),CONTEXT).state=='out_of_scope'


def test_history_order_duplicate_rate_reference_evidence_and_replay():
 q=request()
 with pytest.raises(ValueError):R.model_validate({**q.model_dump(),'previous_observation':obs(1.,100.).model_dump()})
 with pytest.raises(ValueError):R.model_validate({**q.model_dump(),'previous_observation':obs(1.,0.).model_dump(),'signed_mean_actual_volume_flow_m3_s':.1})
 with pytest.raises(ValueError):evaluate(q.model_copy(update={'reference_liquid_density':density(847.).model_copy(update={'evidence_id':'absent'})}),CONTEXT)
 assert evaluate(R.model_validate_json(q.model_dump_json()),CONTEXT)==evaluate(q,CONTEXT)


def test_existing_actual_heos_liquid_states_and_low_level_common_mass():
 pytest.importorskip('CoolProp')
 from CoolProp import AbstractState,PT_INPUTS
 actual=PropertyTankDensity(state=StateRequest(fluid='Water',inputs={'P':101325.,'T':330.}),evidence_id='e')
 reference=PropertyTankDensity(state=StateRequest(fluid='Water',inputs={'P':101325.,'T':300.}),evidence_id='e')
 q=request();q=q.model_copy(update={'inventory_request':q.inventory_request.model_copy(update={'liquid_density':actual}),'reference_liquid_density':reference})
 out=evaluate(q,CONTEXT);assert out.state=='computed'
 low=AbstractState('HEOS','Water');low.update(PT_INPUTS,101325.,330.);rho_actual=low.rhomass();low.update(PT_INPUTS,101325.,300.);rho_ref=low.rhomass()
 mass=quad(lambda z:math.pi*(1.7/2)**2*rho_actual,0,2.1)[0]
 assert out.gross_mass_kg==pytest.approx(mass,rel=2e-14)
 assert out.gross_reference_volume_m3==pytest.approx(mass/rho_ref,rel=2e-14)
