"""Actual water EOS with independent PT-path first-law integration and flux maximum."""
import math
import pytest
from scipy.integrate import solve_ivp
from scipy.optimize import brentq,minimize_scalar
from CoolProp.CoolProp import PropsSI

from pipeline_integrity.steam_nozzle import SteamNozzleRequest,evaluate_steam_nozzle
from pipeline_integrity.thermo import ThermodynamicState


class CaloricFixture:
    def __init__(self,cp=2100.,slope=0.,R=461.5,hshift=0.,sshift=0.,bad=None):
        self.cp,self.slope,self.R,self.hshift,self.sshift,self.bad=cp,slope,R,hshift,sshift,bad
    def resolve(self,q):
        p=q.inputs['P']
        def entropy(t):return self.sshift+self.cp*math.log(t/300)+self.slope*(t-300)-self.R*math.log(p/1e5)
        t=q.inputs.get('T')
        if t is None:t=brentq(lambda x:entropy(x)-q.inputs['Smass'],1,3000,xtol=1e-11)
        cp=self.cp+self.slope*t
        return ThermodynamicState(fluid='Other' if self.bad=='fluid' else q.fluid,backend=q.backend,
            pressure_pa_abs=p+(100 if self.bad=='pressure' else 0),temperature_k=t,density_kg_m3=p/(self.R*t),
            enthalpy_j_kg=self.hshift+self.cp*t+.5*self.slope*t*t,
            entropy_j_kg_k=entropy(t)+(1 if self.bad=='entropy' and 'Smass' in q.inputs else 0),
            phase='liquid' if self.bad=='phase' else 'gas',viscosity_pa_s=None,cp_j_kg_k=cp,cv_j_kg_k=cp-self.R,
            conductivity_w_m_k=None,speed_of_sound_m_s=None if self.bad=='sound' else math.sqrt(cp/(cp-self.R)*self.R*t),
            provider_version='fixture',provider_revision='changed' if self.bad=='revision' and 'Smass' in q.inputs else 'one')


def request(**changes):
    d=dict(stagnation_pressure_Pa_abs=1e6,stagnation_temperature_K=800,back_pressure_Pa_abs=2e5,
        pressure_support_lower_Pa_abs=1e5,property_basis_id='reviewed-pure-water-total-state',
        effective_flow_area_m2=1e-4,effective_area_basis_id='supplied-calibrated-effective-CdA',
        adiabatic_no_work_one_dimensional_total_inlet_and_single_phase_isentrope_reviewed=True,
        single_sonic_maximum_on_supported_path_reviewed=True,effective_area_is_reviewed_CdA_not_inferred_from_Cv=True)
    d.update(changes);return SteamNozzleRequest(**d)


@pytest.mark.parametrize('gamma',[1.1,1.3,1.67])
@pytest.mark.parametrize('ratio',[.9,.2])
def test_constant_cp_limit_against_analytic_nozzle_flux(gamma,ratio):
    R=461.5;cp=gamma*R/(gamma-1);p0=1e6;t0=800
    q=request(back_pressure_Pa_abs=p0*ratio)
    out=evaluate_steam_nozzle(q,property_provider=CaloricFixture(cp=cp,R=R))
    assert out.status=='computed'
    rc=(2/(gamma+1))**(gamma/(gamma-1));r=max(ratio,rc)
    expected=p0*math.sqrt(2*gamma/((gamma-1)*R*t0)*(r**(2/gamma)-r**((gamma+1)/gamma)))
    assert out.ideal_mass_flux_kg_m2_s==pytest.approx(expected,rel=2e-10)
    assert out.throat_state.pressure_pa_abs==pytest.approx(p0*r,rel=2e-10)
    assert out.regime==('choked' if ratio<rc else 'unchoked')
    assert abs(out.specific_energy_residual_J_kg)<1e-8
    assert abs(out.entropy_residual_J_kg_K)<1e-7


@pytest.mark.parametrize('p0,t0',[(5e5,600.),(2e6,800.),(5e6,1000.)])
def test_actual_water_against_independent_PT_thermodynamic_ODE_and_flux_maximum(p0,t0):
    """Reference uses no production PS flash and no EOS sonic-condition root."""
    fluid='HEOS::Water';h0=PropsSI('Hmass','P',p0,'T',t0,fluid)
    def rhs(p,y):
        t=y[0];rho=PropsSI('Dmass','P',p,'T',t,fluid);cp=PropsSI('Cpmass','P',p,'T',t,fluid)
        rho_t=PropsSI('d(Dmass)/d(T)|P','P',p,'T',t,fluid)
        return [-t*rho_t/(rho*rho*cp),1/rho]
    sol=solve_ivp(rhs,(p0,.25*p0),[t0,h0],method='DOP853',rtol=3e-11,atol=[1e-9,1e-5],dense_output=True,max_step=p0/40)
    assert sol.success
    def flux(x):
        p=x*p0;t,h=sol.sol(p)
        return PropsSI('Dmass','P',p,'T',t,fluid)*math.sqrt(2*(h0-h))
    optimum=minimize_scalar(lambda x:-flux(x),bounds=(.35,.8),method='bounded',options={'xatol':1e-12})
    out=evaluate_steam_nozzle(request(stagnation_pressure_Pa_abs=p0,stagnation_temperature_K=t0,
        back_pressure_Pa_abs=.25*p0,pressure_support_lower_Pa_abs=.1*p0))
    assert out.status=='computed' and out.regime=='choked'
    assert out.ideal_mass_flux_kg_m2_s==pytest.approx(-optimum.fun,rel=2e-8)
    assert out.throat_state.pressure_pa_abs==pytest.approx(optimum.x*p0,rel=2e-6)
    assert out.throat_mach==pytest.approx(1,rel=1e-10)
    assert out.throat_state.temperature_k==pytest.approx(sol.sol(out.throat_state.pressure_pa_abs)[0],rel=2e-9)
    assert abs(out.specific_energy_residual_J_kg)<1e-6
    assert abs(out.entropy_residual_J_kg_K)<1e-5
    assert out.ideal_mass_flux_kg_m2_s>flux(optimum.x*.999)
    assert out.ideal_mass_flux_kg_m2_s>flux(optimum.x*1.001)


def test_actual_water_unchoked_PT_path_energy_integral():
    p0,t0=2e6,800.;pb=.9*p0;fluid='HEOS::Water'
    def rhs(p,y):
        t=y[0];rho=PropsSI('Dmass','P',p,'T',t,fluid);cp=PropsSI('Cpmass','P',p,'T',t,fluid)
        return [-t*PropsSI('d(Dmass)/d(T)|P','P',p,'T',t,fluid)/(rho*rho*cp),1/rho]
    h0=PropsSI('Hmass','P',p0,'T',t0,fluid)
    sol=solve_ivp(rhs,(p0,pb),[t0,h0],rtol=1e-11,atol=[1e-9,1e-5],method='DOP853')
    out=evaluate_steam_nozzle(request(stagnation_pressure_Pa_abs=p0,stagnation_temperature_K=t0,back_pressure_Pa_abs=pb))
    t,h=sol.y[:,-1];expected=PropsSI('Dmass','P',pb,'T',t,fluid)*math.sqrt(2*(h0-h))
    assert out.status=='computed' and out.regime=='unchoked'
    assert out.ideal_mass_flux_kg_m2_s==pytest.approx(expected,rel=3e-8)


def test_actual_industrial_water_backend_selected_gas_case():
    q=request(stagnation_pressure_Pa_abs=2e6,stagnation_temperature_K=800)
    a=evaluate_steam_nozzle(q);b=evaluate_steam_nozzle(q.model_copy(update={'backend':'IF97'}))
    assert b.status=='computed' and b.regime=='choked'
    assert b.ideal_mass_flux_kg_m2_s==pytest.approx(a.ideal_mass_flux_kg_m2_s,rel=.002)
    # Agreement between property formulations is not empirical steam/Cd calibration.
    assert b.wet_steam_or_device_calibration_qualified is False


def test_variable_cp_ideal_fixture_against_energy_ODE():
    cp0,b,R=1600.,1.2,461.5;p0,t0,pb=1e6,800.,8e5
    def rhs(p,y):return [R*y[0]/(p*(cp0+b*y[0])),R*y[0]/p]
    h0=cp0*t0+.5*b*t0*t0
    sol=solve_ivp(rhs,(p0,pb),[t0,h0],method='DOP853',rtol=1e-12,atol=[1e-9,1e-5])
    t,h=sol.y[:,-1];expected=pb/(R*t)*math.sqrt(2*(h0-h))
    out=evaluate_steam_nozzle(request(back_pressure_Pa_abs=pb),property_provider=CaloricFixture(cp=cp0,slope=b,R=R))
    assert out.status=='computed' and out.regime=='unchoked'
    assert out.ideal_mass_flux_kg_m2_s==pytest.approx(expected,rel=2e-9)


@pytest.mark.parametrize('scale',[.001,20.])
def test_effective_area_extensivity_and_mass_flow_area_inverse(scale):
    q=request(target_mass_flow_kg_s=.5)
    a=evaluate_steam_nozzle(q,property_provider=CaloricFixture())
    b=evaluate_steam_nozzle(q.model_copy(update={'effective_flow_area_m2':1e-4*scale,'target_mass_flow_kg_s':.5*scale}),property_provider=CaloricFixture())
    assert a.status==b.status=='computed'
    assert b.mass_flow_kg_s==pytest.approx(a.mass_flow_kg_s*scale)
    assert b.required_effective_flow_area_m2==pytest.approx(a.required_effective_flow_area_m2*scale)
    recovered=evaluate_steam_nozzle(q.model_copy(update={'effective_flow_area_m2':a.required_effective_flow_area_m2}),property_provider=CaloricFixture())
    assert recovered.mass_flow_kg_s==pytest.approx(.5)
    assert recovered.conventional_Cv_inferred is False


def test_choked_capacity_does_not_need_an_unreviewed_downstream_expansion():
    a=evaluate_steam_nozzle(request(back_pressure_Pa_abs=2e5),property_provider=CaloricFixture())
    b=evaluate_steam_nozzle(request(back_pressure_Pa_abs=1000),property_provider=CaloricFixture())
    assert a.regime==b.regime=='choked'
    assert b.ideal_mass_flux_kg_m2_s==pytest.approx(a.ideal_mass_flux_kg_m2_s)


def test_enthalpy_entropy_reference_shift_invariance():
    a=evaluate_steam_nozzle(request(),property_provider=CaloricFixture())
    b=evaluate_steam_nozzle(request(),property_provider=CaloricFixture(hshift=-1e6,sshift=500))
    assert b.ideal_mass_flux_kg_m2_s==pytest.approx(a.ideal_mass_flux_kg_m2_s,rel=1e-10)
    assert b.throat_state.pressure_pa_abs==pytest.approx(a.throat_state.pressure_pa_abs,rel=1e-10)


def test_small_pressure_drive_converges_to_incompressible_kinetic_limit():
    gas=CaloricFixture();rho=1e6/(gas.R*800);errors=[]
    for frac in [1e-3,1e-4]:
        r=evaluate_steam_nozzle(request(back_pressure_Pa_abs=1e6*(1-frac)),property_provider=gas)
        errors.append(abs(r.ideal_mass_flux_kg_m2_s/math.sqrt(2*rho*1e6*frac)-1))
    assert errors[1]<errors[0]/8


def test_zero_drive_and_zero_requested_mass_are_explicit():
    r=evaluate_steam_nozzle(request(back_pressure_Pa_abs=1e6,target_mass_flow_kg_s=0),property_provider=CaloricFixture())
    assert r.status=='computed' and r.regime=='zero_drive'
    assert r.mass_flow_kg_s==r.required_effective_flow_area_m2==0
    r=evaluate_steam_nozzle(request(back_pressure_Pa_abs=1e6,target_mass_flow_kg_s=1),property_provider=CaloricFixture())
    assert r.status=='no_pressure_drive' and r.required_effective_flow_area_m2 is None


@pytest.mark.parametrize('field',['stagnation_pressure_Pa_abs','stagnation_temperature_K','back_pressure_Pa_abs','pressure_support_lower_Pa_abs'])
def test_unknown_state_is_withheld_before_provider(field):
    class Never:
        def resolve(self,*a):pytest.fail('unknown data invoked EOS')
    assert evaluate_steam_nozzle(request(**{field:None}),property_provider=Never()).status=='needs_data'


@pytest.mark.parametrize('field',['adiabatic_no_work_one_dimensional_total_inlet_and_single_phase_isentrope_reviewed','single_sonic_maximum_on_supported_path_reviewed','effective_area_is_reviewed_CdA_not_inferred_from_Cv'])
def test_unknown_domain_and_effective_area_mapping_review(field):
    assert evaluate_steam_nozzle(request(**{field:False}),property_provider=CaloricFixture()).status=='needs_data'


def test_unsupplied_effective_area_still_returns_flux_not_Cv():
    r=evaluate_steam_nozzle(request(effective_flow_area_m2=None,effective_area_basis_id=None,effective_area_is_reviewed_CdA_not_inferred_from_Cv=False),property_provider=CaloricFixture())
    assert r.status=='computed' and r.mass_flow_kg_s is None and r.conventional_Cv_inferred is False


def test_truncated_pressure_support_is_not_extrapolated():
    r=evaluate_steam_nozzle(request(pressure_support_lower_Pa_abs=9e5),property_provider=CaloricFixture())
    assert r.status=='insufficient_pressure_support'


def test_actual_near_saturation_expansion_is_withheld_when_wet():
    p=2e6;ts=PropsSI('T','P',p,'Q',1,'HEOS::Water')
    r=evaluate_steam_nozzle(request(stagnation_pressure_Pa_abs=p,stagnation_temperature_K=ts+1,back_pressure_Pa_abs=2e5))
    assert r.status=='out_of_scope' and r.ideal_mass_flux_kg_m2_s is None


def test_actual_dry_saturation_boundary_is_not_mislabelled_superheated_path():
    r=evaluate_steam_nozzle(request(inlet_definition='dry_saturated_total_P',stagnation_temperature_K=None))
    assert r.status=='out_of_scope'


@pytest.mark.parametrize('bad',['phase','sound','fluid','revision'])
def test_invalid_property_phase_identity_and_sound_are_not_replaced_by_gamma(bad):
    assert evaluate_steam_nozzle(request(),property_provider=CaloricFixture(bad=bad)).status=='out_of_scope' if bad in ('phase','sound','fluid') else True
    if bad=='revision':
        with pytest.raises(ValueError):evaluate_steam_nozzle(request(),property_provider=CaloricFixture(bad=bad))


@pytest.mark.parametrize('bad',['pressure','entropy'])
def test_prescribed_state_coordinates_checked(bad):
    if bad=='pressure':assert evaluate_steam_nozzle(request(),property_provider=CaloricFixture(bad=bad)).status=='out_of_scope'
    else:
        with pytest.raises(ValueError):evaluate_steam_nozzle(request(),property_provider=CaloricFixture(bad=bad))


def test_sonic_root_iteration_limit_keeps_failure_explicit():
    assert evaluate_steam_nozzle(request(maximum_root_iterations=1),property_provider=CaloricFixture()).status=='not_converged'


@pytest.mark.parametrize('changes',[{'back_pressure_Pa_abs':2e6},{'pressure_support_lower_Pa_abs':1e6},{'effective_flow_area_m2':-1},{'target_mass_flow_kg_s':-1}])
def test_invalid_pressure_and_area_contracts(changes):
    with pytest.raises(ValueError):request(**changes)
