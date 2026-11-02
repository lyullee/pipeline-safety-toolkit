"""Independent rarefaction solution and sonic/subsonic open-edge physics."""
import math
import numpy as np
import pytest
from scipy.integrate import quad
from pipeline_integrity.gas_acoustic_pipe import (
    IdealGasInternalEnergyEOS,CoolPropGasInternalEnergyEOS,GasPipeBoundary,GasPipeBoundaryStage,
    GasAcousticPipeSettings,gas_conservative_state,solve_gas_acoustic_pipe,evaluate_pressure_outflow_face)


def initial(n=100,p=5e5,T=300.,velocity=0.,offset=0.):
    eos=IdealGasInternalEnergyEOS(internal_energy_reference_J_kg=offset)
    rho=p/(eos.specific_gas_constant_J_kg_K*T)
    state=gas_conservative_state(np.full(n,rho),np.full(n,velocity),np.full(n,eos.internal_energy_from_temperature(T)))
    return eos,state


def ledger_relative_defect(ledger):
    # Initially zero momentum is not a physical normalization for a release
    # acquiring finite boundary impulse. Include actual final/boundary/source
    # budgets, keeping the same machine-scale relative conservation tolerance.
    scale=np.maximum.reduce((np.ones(3),abs(ledger.initial_mass_momentum_energy),
        abs(ledger.final_mass_momentum_energy),abs(ledger.integrated_boundary_inflow),
        abs(ledger.integrated_distributed_sources)))
    return float(np.max(abs(ledger.residual)/scale))


@pytest.mark.parametrize('ratio,regime',[(.1,'sonic'),(.5,'subsonic'),(.9,'subsonic'),(1.,'subsonic')])
@pytest.mark.parametrize('normal',[-1,1])
def test_ideal_face_rarefaction_invariant_entropy_and_regime(ratio,regime,normal):
    eos,state=initial(2);r=evaluate_pressure_outflow_face(state[0],eos,5e5*ratio,outward_normal=normal)
    gamma=eos.gamma;rho=state[0,0];a0=math.sqrt(gamma*5e5/rho)
    assert r.regime==regime
    assert r.outward_velocity_m_s+2*r.sound_speed_m_s/(gamma-1)==pytest.approx(2*a0/(gamma-1),rel=2e-14)
    assert r.pressure_pa_abs/r.density_kg_m3**gamma==pytest.approx(5e5/rho**gamma,rel=2e-14)
    if regime=='sonic':
        assert r.outward_mach_number==pytest.approx(1,abs=1e-14)
        assert r.pressure_pa_abs==pytest.approx(5e5*(2/(gamma+1))**(2*gamma/(gamma-1)),rel=2e-14)
        assert r.pressure_pa_abs>5e5*ratio
    else:
        assert r.pressure_pa_abs==5e5*ratio
        assert 0<=r.outward_mach_number<1


def exact_rarefaction_profile(x,time,p0,pback,eos):
    gamma=eos.gamma;rho0=p0/(eos.specific_gas_constant_J_kg_K*300.)
    a0=math.sqrt(gamma*p0/rho0);critical=(2/(gamma+1))**(2*gamma/(gamma-1))
    ratio=max(pback/p0,critical)
    star_a=a0*ratio**((gamma-1)/(2*gamma))
    star_u=2*(a0-star_a)/(gamma-1);tail=star_u-star_a
    xi=(x-100.)/time
    a=np.where(xi<-a0,a0,np.where(xi>tail,star_a,2/(gamma+1)*(a0-(gamma-1)*xi/2)))
    u=np.where(xi<-a0,0.,np.where(xi>tail,star_u,2/(gamma+1)*(a0+xi)))
    rho=rho0*(a/a0)**(2/(gamma-1));p=p0*(a/a0)**(2*gamma/(gamma-1))
    return rho,u,p


@pytest.mark.parametrize('ambient',[5e4,4e5])
def test_nonlinear_open_edge_matches_exact_rarefaction_with_spatial_refinement(ambient):
    errors=[]
    # The strong rarefaction's200-cell first-order error was1.24% of initial
    # pressure. Refine to400 cells to meet the retained1.2% accuracy target.
    for n in (200,400):
        eos,state=initial(n)
        settings=GasAcousticPipeSettings(length_m=100.,inner_diameter_m=.1,duration_s=.08)
        stage=GasPipeBoundaryStage(start_time_s=0.,left=GasPipeBoundary(kind='reflective'),
            right=GasPipeBoundary(kind='pressure_outflow',ambient_pressure_pa_abs=ambient))
        r=solve_gas_acoustic_pipe(state,settings,eos=eos,boundary_stages=(stage,))
        rho,u,p=exact_rarefaction_profile(r.cell_centers_m,.08,5e5,ambient,eos)
        errors.append(float(np.mean(abs(r.pressure_pa_abs-p))/5e5))
        assert ledger_relative_defect(r.ledger)<3e-14
        assert r.ledger.final_mass_momentum_energy[0]<r.ledger.initial_mass_momentum_energy[0]
        assert r.ledger.integrated_boundary_inflow[2]<0
    assert errors[0]/errors[1]>1.35
    assert errors[-1]<.012


def test_complete_pipe_release_changes_from_sonic_to_subsonic_with_conservative_budgets():
    eos,state=initial(50)
    times=tuple(i*.001 for i in range(21))
    q=GasAcousticPipeSettings(length_m=2.,inner_diameter_m=.1,duration_s=.02,
        darcy_friction_factor=.02,observation_times_s=times)
    stage=GasPipeBoundaryStage(start_time_s=0.,left=GasPipeBoundary(kind='reflective'),
        right=GasPipeBoundary(kind='pressure_outflow',ambient_pressure_pa_abs=1e5))
    r=solve_gas_acoustic_pipe(state,q,eos=eos,boundary_stages=(stage,))
    faces=[evaluate_pressure_outflow_face(s.conservative[-1],eos,1e5) for s in r.observations]
    assert faces[0].regime=='sonic'
    assert any(f.regime=='subsonic' for f in faces[1:])
    assert min(s.temperature_K.min() for s in r.observations)<270
    assert ledger_relative_defect(r.ledger)<2e-13
    assert r.ledger.integrated_distributed_sources[2]==0


def test_opening_event_exact_clock_and_negative_energy_reference():
    answers=[]
    for offset in (0.,-1e6):
        eos,state=initial(40,offset=offset)
        closed=GasPipeBoundary(kind='reflective');open_end=GasPipeBoundary(kind='pressure_outflow',ambient_pressure_pa_abs=1e5)
        stages=(GasPipeBoundaryStage(start_time_s=0.,left=closed,right=closed),GasPipeBoundaryStage(start_time_s=.013,left=closed,right=open_end))
        q=GasAcousticPipeSettings(length_m=20.,inner_diameter_m=.1,duration_s=.025,observation_times_s=(.013,.025))
        r=solve_gas_acoustic_pipe(state,q,eos=eos,boundary_stages=stages)
        assert r.boundary_event_times_s==(.013,)
        assert np.array_equal(r.observations[0].conservative,state)
        assert ledger_relative_defect(r.ledger)<1e-13
        answers.append(r)
    assert answers[0].pressure_pa_abs==pytest.approx(answers[1].pressure_pa_abs,rel=2e-13)
    assert answers[0].conservative[:,0]==pytest.approx(answers[1].conservative[:,0],rel=2e-13)


@pytest.mark.parametrize('ambient,expected',[(2e4,'sonic'),(1.8e5,'subsonic')])
def test_coolprop_face_entropy_and_independent_pressure_characteristic_integral(ambient,expected):
    import CoolProp
    from CoolProp import AbstractState
    eos=CoolPropGasInternalEnergyEOS('Nitrogen',energy_reference_offset_J_kg=-1e6)
    reference=AbstractState('HEOS','Nitrogen');reference.update(CoolProp.PT_INPUTS,2e5,300.)
    rho=reference.rhomass();e=reference.umass()-1e6;s=reference.smass()
    cell=gas_conservative_state(rho,0.,e)
    face=evaluate_pressure_outflow_face(cell,eos,ambient)
    assert face.regime==expected
    def inverse_impedance(pressure):
        reference.update(CoolProp.PSmass_INPUTS,pressure,s)
        return 1/(reference.rhomass()*reference.speed_sound())
    velocity=-quad(inverse_impedance,2e5,face.pressure_pa_abs,epsabs=1e-7,epsrel=1e-10)[0]
    assert face.outward_velocity_m_s==pytest.approx(velocity,abs=2e-7)
    reference.update(CoolProp.DmassUmass_INPUTS,face.density_kg_m3,face.internal_energy_J_kg+1e6)
    assert reference.smass()==pytest.approx(s,abs=2e-6)
    if expected=='sonic':assert face.outward_mach_number==pytest.approx(1,abs=2e-9)
    else:assert face.pressure_pa_abs==ambient and 0<face.outward_mach_number<1


def test_pressure_outflow_unknown_inputs_and_inflow_withheld():
    eos,state=initial(2)
    with pytest.raises(ValueError,match='inflow'):evaluate_pressure_outflow_face(state[0],eos,6e5)
    with pytest.raises(ValueError):GasPipeBoundary(kind='pressure_outflow')
    with pytest.raises(ValueError):GasPipeBoundary(kind='reservoir',ambient_pressure_pa_abs=1e5,reservoir_conservative=tuple(state[0]))
    with pytest.raises(ValueError):evaluate_pressure_outflow_face(state[0],eos,0.)


def test_real_gas_pressure_outflow_short_conservative_step_with_negative_reference():
    import CoolProp
    from CoolProp import AbstractState
    eos=CoolPropGasInternalEnergyEOS('Nitrogen',energy_reference_offset_J_kg=-1e6)
    native=AbstractState('HEOS','Nitrogen');native.update(CoolProp.PT_INPUTS,2e5,300.)
    state=gas_conservative_state(np.full(4,native.rhomass()),np.zeros(4),np.full(4,native.umass()-1e6))
    q=GasAcousticPipeSettings(length_m=10.,inner_diameter_m=.1,duration_s=1e-4)
    stage=GasPipeBoundaryStage(start_time_s=0.,left=GasPipeBoundary(kind='reflective'),
        right=GasPipeBoundary(kind='pressure_outflow',ambient_pressure_pa_abs=1.8e5))
    result=solve_gas_acoustic_pipe(state,q,eos=eos,boundary_stages=(stage,))
    assert result.ledger.final_mass_momentum_energy[0]<result.ledger.initial_mass_momentum_energy[0]
    assert result.temperature_K[-1]<300
    assert ledger_relative_defect(result.ledger)<1e-13


def test_supersonic_outgoing_characteristics_extrapolate_and_opposite_orientation():
    eos,state=initial(2,velocity=700.)
    face=evaluate_pressure_outflow_face(state[0],eos,1e5)
    assert face.regime=='supersonic_extrapolated' and face.outward_velocity_m_s==700.
    with pytest.raises(ValueError,match='shock'):evaluate_pressure_outflow_face(state[0],eos,6e5)
