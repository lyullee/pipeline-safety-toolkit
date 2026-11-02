"""Independent energy root, event integration and loss-free envelope limits."""
import math
import pytest
from scipy.integrate import solve_ivp
from scipy.optimize import brentq
from pipeline_integrity.elastic_drop_energy_bound import ElasticDropEnergyBoundRequest,evaluate_elastic_drop_energy_bound

def calc(**kw):
    return evaluate_elastic_drop_energy_bound(ElasticDropEnergyBoundRequest(**(dict(falling_mass_kg=5.,supported_gravity_mass_kg=3.,
        lumped_dynamic_mass_kg=12.,target_stiffness_n_m=20000.,gravity_m_s2=9.81,
        fall_height_above_undeflected_target_m=.2,maximum_reviewed_linear_target_deflection_m=1.,
        linear_target_and_energy_equivalent_envelope_reviewed=True)|kw)))

@pytest.mark.parametrize('supported',[0.,3.,20.])
@pytest.mark.parametrize('height',[0.,.05,.5])
def test_peak_matches_independent_initial_final_energy_root(supported,height):
    result=calc(supported_gravity_mass_kg=supported,fall_height_above_undeflected_target_m=height)
    w=5*9.81;wb=supported*9.81;k=20000.
    static=(w+wb)/k
    def energy(d):return .5*k*d*d-(w+wb)*d-w*height+.5*wb*wb/k
    root=brentq(energy,static,1.,xtol=2e-15)
    assert result.peak_total_target_deflection_m==pytest.approx(root,rel=2e-13,abs=3e-15)
    assert result.peak_spring_reaction_n==pytest.approx(k*root,rel=2e-13)
    assert abs(result.energy_balance_residual_j)<1e-11

def test_event_ode_and_constant_energy_agree_with_equivalent_oscillator_clock():
    result=calc();m=12.;k=20000.;w=5*9.81;wb=3*9.81
    initial=wb/k;v0=math.sqrt(2*w*(.2+initial)/m)
    def rhs(t,state):return [state[1],(w+wb-k*state[0])/m]
    def turning(t,state):return state[1]
    turning.direction=-1;turning.terminal=True
    reference=solve_ivp(rhs,(0.,1.),[initial,v0],events=turning,rtol=2e-11,atol=1e-12,dense_output=True)
    assert len(reference.t_events[0])==1
    event_time=reference.t_events[0][0]
    assert event_time==pytest.approx(result.equivalent_oscillator_peak_time_s,rel=2e-10)
    assert reference.y_events[0][0,0]==pytest.approx(result.peak_total_target_deflection_m,rel=2e-10)
    for t in (event_time*.2,event_time*.7,event_time):
        x,v=reference.sol(t)
        invariant=.5*m*v*v+.5*k*x*x-(w+wb)*x
        assert invariant==pytest.approx(w*.2-.5*wb*wb/k,rel=2e-10,abs=1e-10)

def test_zero_gap_unpreloaded_sudden_weight_has_two_times_static_reaction():
    result=calc(supported_gravity_mass_kg=0.,fall_height_above_undeflected_target_m=0.)
    assert result.peak_spring_reaction_n==pytest.approx(2*5*9.81,rel=1e-14)

def test_equivalent_mass_changes_the_clock_but_not_the_energy_bound():
    one=calc(lumped_dynamic_mass_kg=3.);two=calc(lumped_dynamic_mass_kg=12.)
    assert two.peak_total_target_deflection_m==pytest.approx(one.peak_total_target_deflection_m)
    assert two.equivalent_oscillator_peak_time_s==pytest.approx(2*one.equivalent_oscillator_peak_time_s)
    assert two.actual_collision_momentum_rebound_or_contact_force_predicted is False

def test_unknown_stiffness_does_not_become_a_nominal_target():
    assert calc(target_stiffness_n_m=None).status=='needs_data'

def test_excessive_deflection_is_outside_reviewed_linear_support():
    assert calc(maximum_reviewed_linear_target_deflection_m=.001).status=='out_of_scope'

def test_unreviewed_collision_idealization_is_withheld():
    assert calc(linear_target_and_energy_equivalent_envelope_reviewed=False).status=='needs_data'
