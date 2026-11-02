"""Independent ideal-cycle limits and volume-parametrized first-law integration."""
import math
import pytest
from scipy.integrate import solve_ivp
from scipy.optimize import brentq
from pipeline_integrity.gas_clearance_cycle import GasClearanceIntakeRequest,evaluate_gas_clearance_intake
from pipeline_integrity.thermo import ThermodynamicState


class IdealCaloricGas:
    def __init__(self,cp0=1200.,slope=0.,R=300.,hoffset=0.,soffset=0.,bad=None):
        self.cp0,self.slope,self.R,self.hoffset,self.soffset,self.bad=cp0,slope,R,hoffset,soffset,bad
    def h(self,t):return self.hoffset+self.cp0*t+.5*self.slope*t*t
    def s(self,p,t):return self.soffset+self.cp0*math.log(t/300.)+self.slope*(t-300.)-self.R*math.log(p/1e5)
    def resolve(self,q):
        p=q.inputs['P']
        if 'T' in q.inputs:t=q.inputs['T']
        elif 'Hmass' in q.inputs:
            energy=q.inputs['Hmass']-self.hoffset
            t=energy/self.cp0 if self.slope==0 else 2*energy/(self.cp0+math.sqrt(self.cp0**2+2*self.slope*energy))
        else:t=brentq(lambda t:self.s(p,t)-q.inputs['Smass'],1.,5000.,xtol=1e-10)
        entropy=self.s(p,t)
        if self.bad=='entropy_coordinate' and 'Smass' in q.inputs:entropy+=1.
        if self.bad=='entropy_generation' and 'Hmass' in q.inputs:entropy-=100.
        return ThermodynamicState(fluid='Other' if self.bad=='fluid' else q.fluid,backend=q.backend,
            pressure_pa_abs=p,temperature_k=t,density_kg_m3=p/(self.R*t),enthalpy_j_kg=self.h(t),
            entropy_j_kg_k=entropy,phase='liquid' if self.bad=='phase' else 'gas',
            viscosity_pa_s=None,cp_j_kg_k=self.cp0+self.slope*t,cv_j_kg_k=self.cp0+self.slope*t-self.R,
            conductivity_w_m_k=None,speed_of_sound_m_s=None,provider_version='synthetic',provider_revision='1')


def request(**changes):
    d=dict(fluid='FixtureGas',swept_volume_m3=.003,clearance_volume_m3=.0003,
        suction_pressure_Pa_abs=2e5,suction_temperature_K=300.,residual_discharge_pressure_Pa_abs=8e5,
        residual_discharge_temperature_K=500.,property_basis_id='declared-gas-EOS',chamber_geometry_basis_id='measured-chamber',
        same_composition_single_phase_ideal_valves_adiabatic_reexpansion_isobaric_intake_reviewed=True,
        continuous_increasing_filled_volume_with_admitted_mass_reviewed=True)
    d.update(changes);return GasClearanceIntakeRequest(**d)


@pytest.mark.parametrize('gamma,ratio,clearance',[(1.1,2.,.04),(1.4,6.,.1),(1.67,20.,.05)])
def test_ideal_constant_cp_clearance_limit_and_pressure_volume_work(gamma,ratio,clearance):
    R=300.;cp=gamma*R/(gamma-1);vs=.003;ps=2e5;ts=300.
    td=ts*ratio**((gamma-1)/gamma)
    q=request(clearance_volume_m3=clearance*vs,residual_discharge_pressure_Pa_abs=ps*ratio,
        residual_discharge_temperature_K=td)
    r=evaluate_gas_clearance_intake(q,property_provider=IdealCaloricGas(cp0=cp,R=R))
    expected=1+clearance-clearance*ratio**(1/gamma)
    assert r.status=='computed' and r.regime=='fresh_intake'
    assert r.ideal_clearance_volumetric_efficiency==pytest.approx(expected,rel=3e-10)
    assert r.fresh_intake_mass_per_cycle_kg==pytest.approx(ps/(R*ts)*vs*expected,rel=3e-10)
    ve=clearance*vs*ratio**(1/gamma)
    work=(ps*ratio*clearance*vs-ps*ve)/(gamma-1)
    assert r.reexpansion_work_by_residual_gas_J==pytest.approx(work,rel=3e-10)
    assert abs(r.intake_energy_budget_residual_J)<1e-7
    assert abs(r.intake_entropy_generation_J_K)<1e-8


def test_variable_cp_against_independent_closed_expansion_and_open_intake_ODE():
    gas=IdealCaloricGas(cp0=1000.,slope=1.5);q=request(residual_discharge_temperature_K=650.)
    vc=q.clearance_volume_m3;vmax=vc+q.swept_volume_m3
    mr=q.residual_discharge_pressure_Pa_abs*vc/(gas.R*q.residual_discharge_temperature_K)
    def expansion(v,y):return [-gas.R*y[0]/(v*(gas.cp0+gas.slope*y[0]-gas.R))]
    def suction_reached(v,y):return mr*gas.R*y[0]/v-q.suction_pressure_Pa_abs
    suction_reached.direction=-1;suction_reached.terminal=True
    expanded=solve_ivp(expansion,(vc,vmax),[q.residual_discharge_temperature_K],events=suction_reached,
        method='DOP853',rtol=2e-12,atol=1e-10,max_step=(vmax-vc)/30)
    assert expanded.success and len(expanded.t_events[0])==1
    ve=expanded.t_events[0][0];te=expanded.y_events[0][0,0];hin=gas.h(q.suction_temperature_K)
    def intake(v,y):
        t=y[0];cp=gas.cp0+gas.slope*t
        return [t*(hin-gas.h(t))/(v*(cp*t+hin-gas.h(t)))]
    filled=solve_ivp(intake,(ve,vmax),[te],method='DOP853',rtol=2e-12,atol=1e-10,max_step=(vmax-ve)/30)
    assert filled.success
    temperature=filled.y[0,-1];expected_mass=q.suction_pressure_Pa_abs*vmax/(gas.R*temperature)-mr
    r=evaluate_gas_clearance_intake(q,property_provider=gas)
    assert r.status=='computed'
    assert r.reexpansion_volume_at_suction_pressure_m3==pytest.approx(ve,rel=3e-10)
    assert r.fresh_intake_mass_per_cycle_kg==pytest.approx(expected_mass,rel=3e-9)
    assert r.full_stroke_state_after_intake.temperature_k==pytest.approx(temperature,rel=3e-10)
    assert abs(r.intake_energy_budget_residual_J)<2e-7
    assert r.intake_entropy_generation_J_K>0


def test_zero_clearance_requires_no_fictitious_residual_state():
    q=request(clearance_volume_m3=0,residual_discharge_pressure_Pa_abs=None,
        residual_discharge_temperature_K=None,continuous_increasing_filled_volume_with_admitted_mass_reviewed=False)
    r=evaluate_gas_clearance_intake(q,property_provider=IdealCaloricGas())
    assert r.status=='computed' and r.regime=='no_clearance'
    assert r.ideal_clearance_volumetric_efficiency==1
    assert r.residual_discharge_state is None


def test_no_suction_event_is_zero_admission_not_negative_capacity():
    r=evaluate_gas_clearance_intake(request(clearance_volume_m3=.0009,residual_discharge_pressure_Pa_abs=1e7),property_provider=IdealCaloricGas())
    assert r.status=='computed' and r.regime=='no_suction_admission'
    assert r.fresh_intake_mass_per_cycle_kg==r.ideal_clearance_volumetric_efficiency==0
    assert r.full_stroke_state_after_intake is None and r.intake_energy_budget_residual_J is None


@pytest.mark.parametrize('side',[-1,1])
def test_suction_event_threshold_from_both_sides(side):
    gamma=4/3;threshold=11.**gamma;ratio=threshold*(1+side*1e-6)
    r=evaluate_gas_clearance_intake(request(residual_discharge_pressure_Pa_abs=2e5*ratio),property_provider=IdealCaloricGas())
    assert r.status=='computed'
    assert r.regime==('fresh_intake' if side<0 else 'no_suction_admission')


@pytest.mark.parametrize('scale',[.001,17.])
def test_extensive_chamber_volume_similarity(scale):
    a=evaluate_gas_clearance_intake(request(),property_provider=IdealCaloricGas())
    b=evaluate_gas_clearance_intake(request(swept_volume_m3=.003*scale,clearance_volume_m3=.0003*scale,
        absolute_fresh_mass_tolerance_kg=1e-15),property_provider=IdealCaloricGas())
    assert b.ideal_clearance_volumetric_efficiency==pytest.approx(a.ideal_clearance_volumetric_efficiency,rel=2e-9)
    assert b.fresh_intake_mass_per_cycle_kg==pytest.approx(a.fresh_intake_mass_per_cycle_kg*scale,rel=2e-9)


@pytest.mark.parametrize('offset',[(-4e7,-2e5),(1e7,4e4)])
def test_common_enthalpy_and_entropy_reference_invariance(offset):
    a=evaluate_gas_clearance_intake(request(),property_provider=IdealCaloricGas())
    b=evaluate_gas_clearance_intake(request(),property_provider=IdealCaloricGas(hoffset=offset[0],soffset=offset[1]))
    assert b.fresh_intake_mass_per_cycle_kg==pytest.approx(a.fresh_intake_mass_per_cycle_kg,rel=2e-8)
    assert b.intake_entropy_generation_J_K==pytest.approx(a.intake_entropy_generation_J_K,abs=1e-8)


@pytest.mark.parametrize('change',[dict(intake_cycles_per_second=None,cycle_clock_id='clock',repeatable_given_states_per_cycle_reviewed=True),
    dict(intake_cycles_per_second=5,cycle_clock_id=None,repeatable_given_states_per_cycle_reviewed=True),
    dict(intake_cycles_per_second=5,cycle_clock_id='clock',repeatable_given_states_per_cycle_reviewed=False)])
def test_rate_requires_known_clock_frequency_and_repeatable_given_states(change):
    r=evaluate_gas_clearance_intake(request(mode='rate',**change),property_provider=IdealCaloricGas())
    assert r.status=='needs_data' and r.fresh_intake_mass_per_cycle_kg>0 and r.ideal_fresh_mass_flow_kg_s is None


@pytest.mark.parametrize('rate',[0.,12.])
def test_reviewed_cycle_rate_preserves_zero_and_units(rate):
    r=evaluate_gas_clearance_intake(request(mode='rate',intake_cycles_per_second=rate,
        cycle_clock_id='observed-cycle-clock',repeatable_given_states_per_cycle_reviewed=True),property_provider=IdealCaloricGas())
    assert r.status=='computed'
    assert r.ideal_fresh_mass_flow_kg_s==pytest.approx(rate*r.fresh_intake_mass_per_cycle_kg)


@pytest.mark.parametrize('change',[dict(suction_pressure_Pa_abs=None),dict(suction_temperature_K=None),
    dict(residual_discharge_pressure_Pa_abs=None),dict(residual_discharge_temperature_K=None),
    dict(same_composition_single_phase_ideal_valves_adiabatic_reexpansion_isobaric_intake_reviewed=False),
    dict(continuous_increasing_filled_volume_with_admitted_mass_reviewed=False)])
def test_missing_boundary_does_not_query_properties(change):
    class Never:
        def resolve(self,q):raise AssertionError('unknown boundary must not query states')
    r=evaluate_gas_clearance_intake(request(**change),property_provider=Never())
    assert r.status=='needs_data' and r.fresh_intake_mass_per_cycle_kg is None


@pytest.mark.parametrize('bad',['fluid','entropy_coordinate'])
def test_property_identity_and_prescribed_entropy_not_changed(bad):
    with pytest.raises(ValueError):evaluate_gas_clearance_intake(request(),property_provider=IdealCaloricGas(bad=bad))


def test_negative_entropy_generation_is_not_a_valid_filling_solution():
    r=evaluate_gas_clearance_intake(request(),property_provider=IdealCaloricGas(bad='entropy_generation'))
    assert r.status=='out_of_scope' and r.intake_entropy_generation_J_K<0


def test_condensed_phase_is_not_a_gas_clearance_cycle():
    r=evaluate_gas_clearance_intake(request(),property_provider=IdealCaloricGas(bad='phase'))
    assert r.status=='out_of_scope'


def test_user_mass_search_support_can_fail_without_a_false_zero_capacity():
    r=evaluate_gas_clearance_intake(request(initial_fresh_mass_upper_kg=1e-12,maximum_bracket_expansions=0),property_provider=IdealCaloricGas())
    assert r.status=='no_bracket' and r.fresh_intake_mass_per_cycle_kg is None


def test_iteration_failure_is_not_accepted():
    r=evaluate_gas_clearance_intake(request(residual_discharge_temperature_K=900.,maximum_solver_iterations=1),
        property_provider=IdealCaloricGas(cp0=1000.,slope=2.))
    assert r.status=='not_converged'


@pytest.mark.parametrize('fluid,pd,td',[('Nitrogen',2e6,500.),('Methane',3e6,500.)])
def test_actual_EOS_mass_energy_entropy_and_full_stroke_volume_budgets(fluid,pd,td):
    q=request(fluid=fluid,residual_discharge_pressure_Pa_abs=pd,residual_discharge_temperature_K=td)
    r=evaluate_gas_clearance_intake(q)
    assert r.status=='computed' and r.regime=='fresh_intake'
    end_mass=r.full_stroke_state_after_intake.density_kg_m3*(q.swept_volume_m3+q.clearance_volume_m3)
    assert end_mass==pytest.approx(r.residual_mass_kg+r.fresh_intake_mass_per_cycle_kg,rel=2e-9)
    assert abs(r.intake_volume_budget_residual_m3)<1e-10
    assert abs(r.intake_energy_budget_residual_J)<1e-6
    assert abs(r.reexpansion_entropy_residual_J_kg_K)<1e-6
    assert r.intake_entropy_generation_J_K>=-1e-8
