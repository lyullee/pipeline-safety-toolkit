"""Independent nonlinear Euler, acoustic, source and event references."""
import math

import numpy as np
import pytest

from pipeline_integrity.gas_acoustic_pipe import (
    GasAcousticPipeSettings as Settings, GasPipeBoundary as Boundary,
    GasPipeBoundaryStage as Stage, IdealGasInternalEnergyEOS as IdealGas,
    gas_conservative_state, solve_gas_acoustic_pipe,
)

EOS=IdealGas(specific_gas_constant_J_kg_K=1.,gamma=1.4)


def state(rho,v,p,eos=EOS):
    return gas_conservative_state(rho,v,np.asarray(p)/(np.asarray(rho)*(eos.gamma-1))+eos.internal_energy_reference_J_kg)


def periodic():
    b=Boundary(kind="periodic")
    return (Stage(start_time_s=0.,left=b,right=b),)


def solve(u,t=.1,**kwargs):
    stages=kwargs.pop("boundary_stages",periodic())
    eos=kwargs.pop("eos",EOS)
    return solve_gas_acoustic_pipe(u,Settings(length_m=1.,inner_diameter_m=1.,duration_s=t,**kwargs),
        eos=eos,boundary_stages=stages)


def assert_ledger(result,relative=1e-11):
    ledger=result.ledger
    scale=np.maximum(1.,np.abs(ledger.initial_mass_momentum_energy)
                     +np.abs(ledger.integrated_boundary_inflow)+np.abs(ledger.integrated_distributed_sources))
    assert np.max(np.abs(ledger.residual)/scale)<relative


@pytest.mark.parametrize("kind,velocity", [("periodic",.3),("extrapolated",.3),("reservoir",.3),("reflective",0.)])
def test_uniform_nonlinear_state_is_preserved_under_consistent_ghost_boundaries(kind,velocity):
    u=state(np.ones(16),velocity,np.ones(16))
    b=Boundary(kind=kind,reservoir_conservative=tuple(u[0]) if kind=="reservoir" else None)
    result=solve(u,boundary_stages=(Stage(start_time_s=0.,left=b,right=b),))
    assert result.conservative==pytest.approx(u,rel=2e-14,abs=2e-14)
    assert_ledger(result)


def test_nonuniform_periodic_mass_momentum_and_total_energy_are_conserved():
    x=(np.arange(80)+.5)/80
    u=state(1+.15*np.sin(2*np.pi*x),.25+.04*np.sin(4*np.pi*x),1+.1*np.cos(2*np.pi*x))
    result=solve(u,t=.2)
    assert result.ledger.integrated_boundary_inflow==pytest.approx(np.zeros(3),abs=1e-15)
    assert result.ledger.integrated_distributed_sources==pytest.approx(np.zeros(3),abs=1e-15)
    assert_ledger(result)


def test_advected_entropy_contact_has_spatial_convergence_and_constant_pressure_velocity():
    errors=[]
    for n in (32,64,128):
        x=(np.arange(n)+.5)/n
        result=solve(state(1+.2*np.sin(2*np.pi*x),.6,np.ones(n)),t=.2)
        exact=1+.2*np.sin(2*np.pi*(x-.6*.2))
        errors.append(np.mean(np.abs(result.conservative[:,0]-exact)))
        assert result.pressure_pa_abs==pytest.approx(np.ones(n),abs=2e-13)
        assert result.velocity_m_s==pytest.approx(np.full(n,.6),abs=2e-13)
        assert_ledger(result)
    assert errors[1]<.65*errors[0]
    assert errors[2]<.65*errors[1]


def test_small_right_running_acoustic_mode_has_the_independent_sound_speed():
    n=256;x=(np.arange(n)+.5)/n;epsilon=1e-5
    rho=1+epsilon*np.cos(2*np.pi*x)
    a=math.sqrt(EOS.gamma)
    p=rho**EOS.gamma
    v=2*a/(EOS.gamma-1)*(rho**((EOS.gamma-1)/2)-1)
    result=solve(state(rho,v,p),t=.12)
    mode0=np.mean((rho-1)*np.exp(-2j*np.pi*x))
    mode1=np.mean((result.conservative[:,0]-1)*np.exp(-2j*np.pi*x))
    speed=-np.angle(mode1/mode0)/(2*np.pi*.12)
    assert speed==pytest.approx(a,rel=.01)
    assert 0<abs(mode1/mode0)<1.01
    assert_ledger(result)


def exact_riemann_profile(x,time,left=(1.,0.,1.),right=(.125,0.,.1),gamma=1.4):
    """Independent pressure matching of exact ideal-gas shock/rarefaction curves."""
    def branch(p,side):
        rho,v,pk=side;a=math.sqrt(gamma*pk/rho)
        if p>pk:
            return (p-pk)*math.sqrt(2/((gamma+1)*rho)/(p+(gamma-1)/(gamma+1)*pk))
        return 2*a/(gamma-1)*((p/pk)**((gamma-1)/(2*gamma))-1)
    lo=1e-12;hi=max(left[2],right[2])
    while branch(hi,left)+branch(hi,right)+right[1]-left[1]<0:hi*=2
    for _ in range(90):
        p=.5*(lo+hi)
        if branch(p,left)+branch(p,right)+right[1]-left[1]>0:hi=p
        else:lo=p
    ps=.5*(lo+hi);us=.5*(left[1]+right[1]+branch(ps,right)-branch(ps,left))
    output=[]
    for location in x:
        xi=(location-.5)/time
        side=left if xi<=us else right
        rho,v,p=side;a=math.sqrt(gamma*p/rho);ratio=ps/p;b=(gamma-1)/(gamma+1)
        rhostar=rho*((ratio+b)/(b*ratio+1) if ps>p else ratio**(1/gamma))
        if ps>p:
            wave=v+(-1 if xi<=us else 1)*a*math.sqrt((gamma+1)/(2*gamma)*ratio+(gamma-1)/(2*gamma))
            outer=xi<wave if xi<=us else xi>wave
            output.append(side if outer else (rhostar,us,ps));continue
        astar=a*ratio**((gamma-1)/(2*gamma))
        head=v-a if xi<=us else v+a;tail=us-astar if xi<=us else us+astar
        if (xi<head if xi<=us else xi>head):output.append(side)
        elif (xi>tail if xi<=us else xi<tail):output.append((rhostar,us,ps))
        else:
            if xi<=us:
                vf=2/(gamma+1)*(a+.5*(gamma-1)*v+xi)
                af=2/(gamma+1)*(a+.5*(gamma-1)*(v-xi))
            else:
                vf=2/(gamma+1)*(-a+.5*(gamma-1)*v+xi)
                af=2/(gamma+1)*(a-.5*(gamma-1)*(v-xi))
            output.append((rho*(af/a)**(2/(gamma-1)),vf,p*(af/a)**(2*gamma/(gamma-1))))
    return np.asarray(output)


def test_nonlinear_sod_shock_contact_rarefaction_matches_independent_exact_riemann_solution():
    errors=[]
    for n in (160,320):
        x=(np.arange(n)+.5)/n
        u=state(np.where(x<.5,1.,.125),np.zeros(n),np.where(x<.5,1.,.1))
        b=Boundary(kind="extrapolated")
        result=solve(u,t=.2,boundary_stages=(Stage(start_time_s=0.,left=b,right=b),))
        exact=exact_riemann_profile(x,.2)
        error=np.mean(np.abs(result.conservative[:,0]-exact[:,0]))
        errors.append(error)
        if n==320:
            assert error<.025
            assert np.mean(np.abs(result.pressure_pa_abs-exact[:,2]))<.025
        assert result.ledger.integrated_boundary_inflow[1]==pytest.approx(.9*.2*math.pi/4,rel=2e-6)
        assert_ledger(result)
    assert errors[1]<.82*errors[0]


@pytest.mark.parametrize("u0", [-2.,2.])
def test_stationary_adiabatic_wall_friction_preserves_total_energy_and_heats_internal_energy(u0):
    rho,p=1.,1.
    initial=state(np.full(4,rho),u0,np.full(4,p))
    result=solve(initial,t=.25,darcy_friction_factor=.2,maximum_step_s=.0005)
    expected=u0/(1+.2*abs(u0)*.25/2)
    assert result.velocity_m_s==pytest.approx(np.full(4,expected),rel=2e-8)
    assert result.ledger.final_mass_momentum_energy[2]==pytest.approx(result.ledger.initial_mass_momentum_energy[2],rel=2e-13)
    expected_temperature=(1/(EOS.gamma-1)+.5*u0*u0-.5*expected*expected)/EOS.cv_J_kg_K
    assert result.temperature_K==pytest.approx(np.full(4,expected_temperature),rel=2e-8)
    assert result.ledger.integrated_distributed_sources[2]==0
    assert_ledger(result)


@pytest.mark.parametrize("ambient", [.5,2.])
def test_uniform_wall_heating_matches_exact_constant_density_exponential_energy_balance(ambient):
    result=solve(state(np.ones(4),0.,np.ones(4)),t=.4,wall_heat_transfer_W_m2_K=.3,
        ambient_temperature_K=ambient,maximum_step_s=.0005)
    expected=ambient+(1-ambient)*math.exp(-4*.3/EOS.cv_J_kg_K*.4)
    assert result.temperature_K==pytest.approx(np.full(4,expected),rel=2e-9)
    assert_ledger(result)


def test_uniform_gravity_acceleration_changes_total_energy_by_force_work_only():
    result=solve(state(np.ones(4),2.,np.ones(4)),t=.2,signed_axial_gravity_m_s2=-3.)
    assert result.velocity_m_s==pytest.approx(np.full(4,1.4),abs=2e-13)
    assert result.temperature_K==pytest.approx(np.ones(4),abs=2e-13)
    assert_ledger(result)


def test_negative_energy_reference_preserves_identical_thermodynamic_evolution():
    x=(np.arange(50)+.5)/50;rho=1+.1*np.sin(2*np.pi*x)
    shifted=IdealGas(specific_gas_constant_J_kg_K=1.,gamma=1.4,internal_energy_reference_J_kg=-20.)
    a=solve(state(rho,.2,np.ones(50)),t=.1)
    b=solve(state(rho,.2,np.ones(50),shifted),t=.1,eos=shifted)
    assert np.all(b.conservative[:,2]<0)
    assert b.pressure_pa_abs==pytest.approx(a.pressure_pa_abs,abs=4e-14)
    assert b.conservative[:,0]==pytest.approx(a.conservative[:,0],abs=4e-14)
    assert_ledger(b)


def test_boundary_closure_event_is_hit_exactly_and_reflective_wall_has_zero_mass_energy_flux():
    n=160;u=state(np.ones(n),.2,np.ones(n));open_end=Boundary(kind="extrapolated")
    wall=Boundary(kind="reflective")
    stages=(Stage(start_time_s=0.,left=open_end,right=open_end),Stage(start_time_s=.023,left=open_end,right=wall))
    result=solve(u,t=.12,boundary_stages=stages)
    assert .023 in result.step_times_s and result.boundary_event_times_s==(.023,)
    # The outgoing/ingoing mirror-state Riemann solution gives the wall pressure.
    exact=exact_riemann_profile(np.array([.5]),.1,left=(1.,.2,1.),right=(1.,-.2,1.))
    assert result.pressure_pa_abs[-1]==pytest.approx(exact[0,2],rel=.015)
    # Before closure left/right fluxes cancel; afterwards only unchanged left inflow remains.
    area=math.pi/4
    assert result.ledger.integrated_boundary_inflow[0]==pytest.approx(area*.2*(.12-.023),rel=2e-6)
    assert_ledger(result)


def test_boundary_event_at_final_time_is_recorded_without_an_extra_step():
    b=Boundary(kind="extrapolated");wall=Boundary(kind="reflective")
    result=solve(state(np.ones(8),.1,np.ones(8)),t=.05,boundary_stages=(
        Stage(start_time_s=0.,left=b,right=b),Stage(start_time_s=.05,left=b,right=wall)))
    assert result.boundary_event_times_s==(.05,)
    assert result.step_times_s[-1]==.05


@pytest.mark.parametrize("left,right", [("periodic","reflective"),("reservoir","extrapolated")])
def test_invalid_boundary_semantics_are_not_silently_changed(left,right):
    with pytest.raises(ValueError): Stage(start_time_s=0.,left=Boundary(kind=left),right=Boundary(kind=right))


def test_invalid_initial_thermodynamic_state_is_rejected_before_time_integration():
    with pytest.raises(ValueError):solve(gas_conservative_state(np.ones(4),0.,-np.ones(4)))


def test_reservoir_cfl_growth_triggers_retries_without_losing_boundary_budget():
    u=state(np.ones(20),0.,np.ones(20))
    reservoir=Boundary(kind="reservoir",reservoir_conservative=tuple(state(1.,0.,1000.)))
    wall=Boundary(kind="reflective")
    result=solve(u,t=.002,boundary_stages=(Stage(start_time_s=0.,left=reservoir,right=wall),))
    assert result.rejected_steps>0
    assert np.all(result.pressure_pa_abs>0) and np.all(result.temperature_K>0)
    assert_ledger(result)


def test_exhausted_retry_budget_raises_without_returning_a_computed_trajectory():
    u=state(np.ones(20),0.,np.ones(20))
    reservoir=Boundary(kind="reservoir",reservoir_conservative=tuple(state(1.,0.,1000.)))
    wall=Boundary(kind="reflective")
    with pytest.raises(RuntimeError,match="retries exhausted"):
        solve(u,t=.002,maximum_positivity_retries=0,
              boundary_stages=(Stage(start_time_s=0.,left=reservoir,right=wall),))


def test_explicit_observation_times_return_independent_readonly_states_with_exact_clock():
    u=state(np.ones(4),0.,np.ones(4));requested=(0.,.07,.19,.2)
    result=solve(u,t=.2,wall_heat_transfer_W_m2_K=.3,ambient_temperature_K=2.,
        maximum_step_s=.0005,observation_times_s=requested)
    assert tuple(s.time_s for s in result.observations)==requested
    for snapshot in result.observations:
        assert snapshot.time_s in result.step_times_s
        expected=2-math.exp(-4*.3/EOS.cv_J_kg_K*snapshot.time_s)
        assert snapshot.temperature_K==pytest.approx(np.full(4,expected),rel=2e-9)
        assert not snapshot.conservative.flags.writeable
        assert not snapshot.pressure_pa_abs.flags.writeable
        assert not np.shares_memory(snapshot.conservative,result.conservative)
    with pytest.raises(ValueError):result.observations[0].conservative[0,0]=2.
    assert result.observations[-1].conservative==pytest.approx(result.conservative)
    assert solve(u,t=.01).observations==()
    assert_ledger(result)


def test_snapshot_at_boundary_event_is_continuous_state_before_new_ghost_evolution():
    n=60;u=state(np.ones(n),.2,np.ones(n));open_end=Boundary(kind="extrapolated")
    wall=Boundary(kind="reflective")
    stages=(Stage(start_time_s=0.,left=open_end,right=open_end),Stage(start_time_s=.023,left=open_end,right=wall))
    result=solve(u,t=.08,boundary_stages=stages,observation_times_s=(.023,.04,.08))
    assert result.observations[0].conservative==pytest.approx(u,abs=2e-14)
    assert result.observations[1].pressure_pa_abs[-1]>1
    assert result.boundary_event_times_s==(.023,)
    assert result.observations[-1].conservative==pytest.approx(result.conservative)
    assert_ledger(result)


@pytest.mark.parametrize("times", [(.05,.05),(.08,.04),(-.001,.05)])
def test_invalid_observation_clock_is_not_sorted_clipped_or_deduplicated(times):
    with pytest.raises(ValueError):
        solve(state(np.ones(4),0.,np.ones(4)),observation_times_s=times)
