"""Independent manufactured mass balances with thermal pressure compensation."""
import math
import pytest
from scipy.integrate import solve_ivp,quad
from pipeline_integrity.thermo import ThermodynamicState
from pipeline_integrity.isolated_gas_inventory_loss import IsolatedGasInventoryLossRequest as R,observe_isolated_gas_inventory_loss as evaluate


class IdealProvider:
    def resolve(self,request):
        p,t=request.inputs['P'],request.inputs['T'];Rs=300.
        return ThermodynamicState(fluid=request.fluid,backend=request.backend,pressure_pa_abs=p,temperature_k=t,density_kg_m3=p/(Rs*t),enthalpy_j_kg=1100*t,entropy_j_kg_k=1100*math.log(t/300)-Rs*math.log(p/1e5),phase='gas',viscosity_pa_s=None,cp_j_kg_k=1100.,cv_j_kg_k=800.,conductivity_w_m_k=None,speed_of_sound_m_s=math.sqrt(1100/800*Rs*t),provider_version='authored ideal law',provider_revision='reference1')


def request(**kw):
    data=dict(fluid='AuthoredGas',actual_internal_volume_m3=2.,initial_pressure_pa_abs=450000.,initial_temperature_K=300.,final_pressure_pa_abs=420000.,final_temperature_K=300.,elapsed_time_s=3600.,reference_pressure_pa_abs=100000.,reference_temperature_K=300.,property_and_reference_basis_id='same fluid explicit actual/reference states',isolated_same_composition_no_other_mass_exchange_reviewed=True,rigid_volume_and_uniform_actual_endpoint_states_reviewed=True)
    data.update(kw);return R(**data)


@pytest.mark.parametrize('a,b,Tfinal',[(0.,0.,240.),(1e-4,0.,280.),(1e-4,2e-8,340.),(-1e-4,0.,300.),(2e-4,-1e-8,310.),(1e-4,0.,360.)])
def test_manufactured_mass_flow_ode_with_actual_independent_temperature_history(a,b,Tfinal):
    volume=2.;m0=10.;duration=600.;T0=300.
    solution=solve_ivp(lambda t,m:[-(a+b*t)],(0,duration),[m0],rtol=1e-12,atol=1e-13,max_step=duration/50)
    mfinal=solution.y[0,-1];p0=m0*300*T0/volume;pf=mfinal*300*Tfinal/volume
    r=request(initial_pressure_pa_abs=p0,initial_temperature_K=T0,final_pressure_pa_abs=pf,final_temperature_K=Tfinal,elapsed_time_s=duration)
    out=evaluate(r,property_provider=IdealProvider());assert out.state=='computed'
    transferred=quad(lambda t:a+b*t,0,duration,epsabs=1e-14)[0]
    assert out.signed_observed_mass_decrease_kg==pytest.approx(transferred,abs=8e-14)
    assert out.signed_mean_unaccounted_outflow_kg_s==pytest.approx(transferred/duration,abs=2e-16)
    refdensity=r.reference_pressure_pa_abs/(300*r.reference_temperature_K)
    assert out.signed_reference_volume_decrease_m3*refdensity==pytest.approx(transferred,abs=8e-14)
    assert abs(out.mass_budget_roundoff_residual_kg)<2e-14
    assert out.leak_cause_or_opening_identified is False


def test_cooling_pressure_drop_alone_is_not_observed_loss():
    out=evaluate(request(final_pressure_pa_abs=360000.,final_temperature_K=240.),property_provider=IdealProvider())
    assert out.signed_observed_mass_decrease_kg==0
    assert out.density_subtraction_condition_number is None
    assert out.half_ulp_endpoint_density_precision_kg_m3>0


@pytest.mark.parametrize('referenceP,referenceT',[(85000.,280.),(101325.,293.15),(140000.,320.)])
def test_reference_basis_changes_volume_without_changing_actual_mass(referenceP,referenceT):
    out=evaluate(request(reference_pressure_pa_abs=referenceP,reference_temperature_K=referenceT),property_provider=IdealProvider())
    expected_mass=quad(lambda V:(450000-420000)/(300*300),0,2.)[0]
    assert out.signed_observed_mass_decrease_kg==pytest.approx(expected_mass,rel=2e-15)
    assert out.signed_reference_volume_decrease_m3==pytest.approx(expected_mass*300*referenceT/referenceP,rel=2e-15)


def test_actual_volume_and_duration_extensivity_and_conditional_reporting():
    a=evaluate(request(),property_provider=IdealProvider())
    b=evaluate(request(actual_internal_volume_m3=6.,elapsed_time_s=7200.),property_provider=IdealProvider())
    assert b.signed_observed_mass_decrease_kg==pytest.approx(3*a.signed_observed_mass_decrease_kg)
    assert b.signed_mean_unaccounted_outflow_kg_s==pytest.approx(1.5*a.signed_mean_unaccounted_outflow_kg_s)
    unreviewed=evaluate(request(reporting_interval_s=365.25*86400),property_provider=IdealProvider())
    assert unreviewed.conditional_constant_mean_reporting_volume_m3 is None
    reported=evaluate(request(reporting_interval_s=365.25*86400,constant_mean_reporting_extrapolation_reviewed=True),property_provider=IdealProvider())
    assert reported.conditional_constant_mean_reporting_volume_m3==pytest.approx(a.signed_mean_reference_volume_outflow_m3_s*365.25*86400)


@pytest.mark.parametrize('update',[{'actual_internal_volume_m3':None},{'property_and_reference_basis_id':None},{'isolated_same_composition_no_other_mass_exchange_reviewed':False},{'rigid_volume_and_uniform_actual_endpoint_states_reviewed':False}])
def test_unknown_isolation_volume_state_or_basis_is_not_defaulted(update):
    assert evaluate(request(**update),property_provider=IdealProvider()).state=='needs_data'


@pytest.mark.parametrize('bad',['liquid','wrongT','wrongFluid','referenceDrift'])
def test_inconsistent_provider_phase_state_composition_or_reference_is_diagnostic(bad):
    class Bad(IdealProvider):
        count=0
        def resolve(self,q):
            s=super().resolve(q)
            if bad=='liquid':return s.model_copy(update={'phase':'liquid'})
            if bad=='wrongT':return s.model_copy(update={'temperature_k':s.temperature_k+1})
            if bad=='wrongFluid':return s.model_copy(update={'fluid':'DifferentGas'})
            if q.inputs['P']==100000:
                self.count+=1
                return s.model_copy(update={'density_kg_m3':s.density_kg_m3*(1+.01*self.count)})
            return s
    with pytest.raises(ValueError):evaluate(request(),property_provider=Bad())


@pytest.mark.parametrize('fluid,T,Pi,Pf',[('Methane',290.,6e5,5.8e5),('Nitrogen',315.,2e6,1.9e6),('Hydrogen',300.,8e6,7.8e6)])
def test_existing_coolprop_inventory_against_independent_low_level_mass_difference(fluid,T,Pi,Pf):
    pytest.importorskip('CoolProp')
    from CoolProp import AbstractState,PT_INPUTS
    low=AbstractState('HEOS',fluid);low.update(PT_INPUTS,Pi,T);rhoi=low.rhomass();low.update(PT_INPUTS,Pf,T);rhof=low.rhomass();low.update(PT_INPUTS,100000.,300.);rhoref=low.rhomass()
    r=request(fluid=fluid,initial_pressure_pa_abs=Pi,final_pressure_pa_abs=Pf,initial_temperature_K=T,final_temperature_K=T)
    out=evaluate(r);assert out.state=='computed'
    loss=quad(lambda V:rhoi-rhof,0,2.)[0]
    assert out.signed_observed_mass_decrease_kg==pytest.approx(loss,rel=2e-13)
    assert out.signed_reference_volume_decrease_m3==pytest.approx(loss/rhoref,rel=2e-13)


@pytest.mark.parametrize('update',[{'elapsed_time_s':0},{'actual_internal_volume_m3':-1},{'initial_pressure_pa_abs':0},{'final_temperature_K':0}])
def test_invalid_absolute_state_time_or_volume_not_clipped(update):
    with pytest.raises(ValueError):request(**update)


def test_contract_replay_preserves_explicit_reference_basis():
    q=request();replay=R.model_validate_json(q.model_dump_json())
    assert evaluate(q,property_provider=IdealProvider())==evaluate(replay,property_provider=IdealProvider())
