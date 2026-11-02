"""Independent ideal entropy laws, real v-dp integrals and finite stage counts."""
import math
import pytest
from scipy.integrate import quad
from CoolProp.CoolProp import PropsSI
from pipeline_integrity.compression_inverse_observers import (
    CompressionEfficiencyObservationRequest as EfficiencyRequest,observe_endpoint_compression_efficiency as efficiency,
    EqualRatioStageCountRequest as CountRequest,observe_minimum_equal_ratio_stage_count as count_stages)
from pipeline_integrity.thermo import ThermodynamicState


class IdealProvider:
    cp=1000.;r=287.
    def __init__(self,origin=0.,phase='gas'):self.origin=origin;self.phase=phase
    def resolve(self,q):
        p=q.inputs['P']
        if 'T' in q.inputs:t=q.inputs['T']
        elif 'Hmass' in q.inputs:t=(q.inputs['Hmass']-self.origin)/self.cp
        else:t=300*math.exp((q.inputs['Smass']+self.r*math.log(p/1e5))/self.cp)
        return ThermodynamicState(fluid=q.fluid,backend=q.backend,pressure_pa_abs=p,temperature_k=t,
            density_kg_m3=p/(self.r*t),enthalpy_j_kg=self.cp*t+self.origin,
            entropy_j_kg_k=self.cp*math.log(t/300)-self.r*math.log(p/1e5),phase=self.phase,
            viscosity_pa_s=None,cp_j_kg_k=self.cp,cv_j_kg_k=self.cp-self.r,conductivity_w_m_k=None,
            speed_of_sound_m_s=math.sqrt(self.cp/(self.cp-self.r)*self.r*t),provider_version='independent-ideal',provider_revision='fixed')


def efficiency_request(**changes):
    d=dict(fluid='Methane',inlet_pressure_Pa_abs=1e5,inlet_temperature_K=300.,outlet_pressure_Pa_abs=4e5,
        measured_outlet_temperature_K=500.,endpoint_basis='negligible_velocity_static',
        steady_adiabatic_same_composition_negligible_height_change_reviewed=True,property_basis_id='authored-reference')
    d.update(changes);return EfficiencyRequest(**d)
def count_request(**changes):
    d=dict(fluid='Methane',inlet_pressure_Pa_abs=1e5,target_pressure_Pa_abs=2e6,inlet_and_reset_temperature_K=300.,
        maximum_discharge_temperature_K=420.,common_differential_efficiency=.8,maximum_stages=8,
        identical_stages_perfect_temperature_reset_zero_interstage_loss_reviewed=True,
        steady_adiabatic_single_gas_path_reviewed=True,property_basis_id='authored-reference')
    d.update(changes);return CountRequest(**d)


@pytest.mark.parametrize('ratio',[2.,10.])
@pytest.mark.parametrize('eta',[.6,.8,1.])
def test_endpoint_efficiency_from_independent_entropy_temperature_law(ratio,eta):
    ts=300*ratio**(.287);actual=300+(ts-300)/eta
    r=efficiency(efficiency_request(outlet_pressure_Pa_abs=1e5*ratio,measured_outlet_temperature_K=actual,mass_flow_kg_s=.4),property_provider=IdealProvider())
    assert r.status=='computed'
    assert r.endpoint_isentropic_efficiency==pytest.approx(eta,rel=2e-14)
    assert r.reversible_work_J_kg==pytest.approx(1000*(ts-300),rel=2e-14)
    assert r.observed_fluid_power_W==pytest.approx(.4*1000*(actual-300),rel=2e-14)
    assert r.entropy_increase_J_kg_K>=-1e-12
    assert not r.differential_polytropic_efficiency_inferred


@pytest.mark.parametrize('origin',[-1e8,1e8])
def test_endpoint_enthalpy_reference_shift_and_unknown_flow(origin):
    a=efficiency(efficiency_request(),property_provider=IdealProvider())
    b=efficiency(efficiency_request(),property_provider=IdealProvider(origin))
    assert b.endpoint_isentropic_efficiency==pytest.approx(a.endpoint_isentropic_efficiency,rel=2e-12)
    assert b.observed_enthalpy_rise_J_kg==pytest.approx(a.observed_enthalpy_rise_J_kg)
    assert b.observed_fluid_power_W is None


@pytest.mark.parametrize('fluid,p1,p2,t1,t2',[('Methane',2e6,8e6,300.,450.),('Hydrogen',1e5,1e6,300.,750.)])
def test_native_endpoint_reference_from_reversible_specific_volume_pressure_integral(fluid,p1,p2,t1,t2):
    ref='HEOS::'+fluid;s=PropsSI('Smass','P',p1,'T',t1,ref)
    reversible=quad(lambda p:1/PropsSI('Dmass','P',p,'Smass',s,ref),p1,p2,epsabs=1e-6,epsrel=1e-10)[0]
    actual=PropsSI('Hmass','P',p2,'T',t2,ref)-PropsSI('Hmass','P',p1,'T',t1,ref)
    r=efficiency(efficiency_request(fluid=fluid,inlet_pressure_Pa_abs=p1,outlet_pressure_Pa_abs=p2,
        inlet_temperature_K=t1,measured_outlet_temperature_K=t2))
    assert 0<reversible/actual<1 and r.status=='computed'
    assert r.endpoint_isentropic_efficiency==pytest.approx(reversible/actual,rel=2e-8)


@pytest.mark.parametrize('temperature',[300.,301.,330.])
def test_heat_cooled_or_inconsistent_endpoints_do_not_become_efficiency(temperature):
    r=efficiency(efficiency_request(measured_outlet_temperature_K=temperature),property_provider=IdealProvider())
    assert r.status=='out_of_scope'


@pytest.mark.parametrize('ratio',[4.,20.,100.])
@pytest.mark.parametrize('eta',[.8,1.])
def test_minimum_stage_count_from_independent_logarithmic_temperature_bound(ratio,eta):
    q=count_request(target_pressure_Pa_abs=1e5*ratio,common_differential_efficiency=eta)
    expected=math.ceil((287/(1000*eta))*math.log(ratio)/math.log(420/300))
    r=count_stages(q,property_provider=IdealProvider())
    assert r.status=='computed' and r.least_feasible_stage_count==expected
    assert [c.stage_count for c in r.candidates]==list(range(1,expected+1))
    assert not any(c.meets_temperature_limit for c in r.candidates[:-1])
    assert r.candidates[-1].meets_temperature_limit
    for c in r.candidates:
        t=300*ratio**(287/(1000*eta*c.stage_count))
        assert c.maximum_discharge_temperature_K==pytest.approx(t,rel=2e-10)
        assert c.specific_fluid_work_J_kg==pytest.approx(c.stage_count*1000*(t-300),rel=2e-9)
    assert r.selected_fluid_power_W is None and not r.global_train_optimum_or_vendor_rating_inferred


def test_bounded_no_solution_and_known_mass_power():
    a=count_stages(count_request(target_pressure_Pa_abs=1e7,maximum_stages=2),property_provider=IdealProvider())
    assert a.status=='no_feasible_candidate_within_bound' and a.least_feasible_stage_count is None
    b=count_stages(count_request(mass_flow_kg_s=.3),property_provider=IdealProvider())
    assert b.selected_fluid_power_W==pytest.approx(.3*b.selected_specific_fluid_work_J_kg)


@pytest.mark.parametrize('changes',[{'inlet_pressure_Pa_abs':None},{'common_differential_efficiency':None},
    {'identical_stages_perfect_temperature_reset_zero_interstage_loss_reviewed':False}])
def test_unknown_stage_inputs_or_cooling_rule_not_assumed(changes):
    assert count_stages(count_request(**changes),property_provider=IdealProvider()).status=='needs_data'


def test_unresolved_candidate_and_endpoint_phase_are_not_false_minimum():
    assert count_stages(count_request(),property_provider=IdealProvider(phase='liquid')).status=='needs_data'
    with pytest.raises(ValueError,match='gaslike'):
        efficiency(efficiency_request(),property_provider=IdealProvider(phase='liquid'))
    assert efficiency(efficiency_request(measured_outlet_temperature_K=None)).status=='needs_data'
    assert efficiency(efficiency_request(steady_adiabatic_same_composition_negligible_height_change_reviewed=False)).status=='needs_data'


def test_native_stage_count_forward_states_respect_limit_and_all_preceding_counts():
    r=count_stages(count_request(inlet_pressure_Pa_abs=2e6,target_pressure_Pa_abs=8e6,maximum_discharge_temperature_K=390.,maximum_stages=5))
    assert r.status=='computed'
    assert r.candidates[-1].maximum_discharge_temperature_K<=390.
    assert all(c.maximum_discharge_temperature_K>390. for c in r.candidates[:-1])
