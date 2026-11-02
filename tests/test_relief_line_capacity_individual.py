"""Independent differential momentum/energy, Fanno integrals and calibrated-Y contracts."""
import math
import pytest
from scipy.integrate import quad,solve_ivp
from pipeline_integrity.relief_line_capacity import (ReliefLossTerm,NetExpansionCalibration,CalibratedReliefLineRequest,
    evaluate_calibrated_relief_line,FannoReliefLineRequest,evaluate_fanno_relief_line,fanno_remaining_darcy_friction)


def calibrated(**changes):
    d=dict(inlet_pressure_Pa_abs=1e6,back_pressure_Pa_abs=9e5,inlet_temperature_K=400,
        inlet_density_kg_m3=10,heat_capacity_ratio=1.3,common_flow_area_m2=.003,
        inlet_state_definition_id='reviewed-static-pressure-density-coordinate',
        loss_terms=(ReliefLossTerm(id='pipe',K=2,coefficient_basis_id='supplied-pipe-state',applicability_reviewed=True),
            ReliefLossTerm(id='disk',K=3,coefficient_basis_id='supplied-device-KR',applicability_reviewed=True)),
        same_bore_common_K_reference_and_steady_inlet_state_reviewed=True,
        net_expansion=NetExpansionCalibration(basis_id='supplied-independent-Y',total_K=5,heat_capacity_ratio=1.3,
            inlet_state_definition_id='reviewed-static-pressure-density-coordinate',critical_pressure_drop_fraction=.6,
            expansion_evaluated_at_drop_fraction=.1,net_expansion_factor=.95,common_area_K_and_expansion_convention_reviewed=True))
    d.update(changes);return CalibratedReliefLineRequest(**d)


def fanno(**changes):
    d=dict(reservoir_stagnation_pressure_Pa_abs=1e6,reservoir_stagnation_temperature_K=500,
        back_pressure_Pa_abs=1e5,flow_area_m2=.002,integrated_darcy_friction=2,
        specific_gas_constant_J_kg_K=300,heat_capacity_ratio=1.4,friction_basis_id='reviewed-distributed-Darcy-integral',
        constant_area_adiabatic_ideal_gas_and_isentropic_entrance_reviewed=True,
        friction_integral_is_not_automatically_total_K_or_disk_KR_reviewed=True)
    d.update(changes);return FannoReliefLineRequest(**d)


def test_conditional_SI_capacity_and_independent_mass_volume_conversion():
    q=calibrated(reference_density_kg_m3=1.25,reference_volume_basis_id='supplied-reference-gas',supplied_capacity_factor=.83,capacity_factor_basis_id='supplied-criterion')
    out=evaluate_calibrated_relief_line(q)
    # Supplied-factor observation of rho*Q; no coefficient/standard table oracle.
    speed=.95*math.sqrt(2*(1e6-9e5)/(10*5))
    mass=10*.003*speed
    assert out.status=='computed' and out.regime=='subcritical'
    assert out.mass_flow_kg_s==pytest.approx(mass)
    assert out.reference_volume_flow_m3_s==pytest.approx(mass/1.25)
    assert out.supplied_factor_adjusted_mass_flow_kg_s==pytest.approx(.83*mass)
    assert out.calibration_or_standard_compliance_inferred is False


def test_supplied_critical_criterion_caps_flow_when_back_pressure_decreases():
    c=calibrated().net_expansion.model_copy(update={'expansion_evaluated_at_drop_fraction':.6,'net_expansion_factor':.7})
    a=evaluate_calibrated_relief_line(calibrated(back_pressure_Pa_abs=3e5,net_expansion=c))
    b=evaluate_calibrated_relief_line(calibrated(back_pressure_Pa_abs=1e4,net_expansion=c))
    assert a.regime==b.regime=='limited_by_supplied_sonic_criterion'
    assert a.mass_flow_kg_s==b.mass_flow_kg_s
    assert b.effective_pressure_drop_Pa==6e5


@pytest.mark.parametrize('changes',[{'net_expansion':None},{'same_bore_common_K_reference_and_steady_inlet_state_reviewed':False},
    {'inlet_density_kg_m3':None},{'common_flow_area_m2':None}])
def test_unknown_coefficient_and_state_data_are_not_invented(changes):
    assert evaluate_calibrated_relief_line(calibrated(**changes)).status=='needs_data'


@pytest.mark.parametrize('field,value',[('total_K',6),('heat_capacity_ratio',1.4),('inlet_state_definition_id','different-total-state'),('expansion_evaluated_at_drop_fraction',.2)])
def test_factor_reference_K_heat_ratio_and_operating_pressure_must_match(field,value):
    q=calibrated();q=q.model_copy(update={'net_expansion':q.net_expansion.model_copy(update={field:value})})
    with pytest.raises(ValueError):evaluate_calibrated_relief_line(q)


def test_zero_K_does_not_become_unbounded_flow_or_nozzle_Cd():
    q=calibrated(loss_terms=(ReliefLossTerm(id='zero',K=0,coefficient_basis_id='zero',applicability_reviewed=True),))
    assert evaluate_calibrated_relief_line(q).status=='out_of_scope'


def test_zero_drive_and_unreviewed_optional_factor_basis():
    assert evaluate_calibrated_relief_line(calibrated(back_pressure_Pa_abs=1e6)).mass_flow_kg_s==0
    out=evaluate_calibrated_relief_line(calibrated(supplied_capacity_factor=.8))
    assert out.status=='needs_data' and out.supplied_factor_adjusted_mass_flow_kg_s is None


@pytest.mark.parametrize('m,g',[(.2,1.1),(.5,1.4),(.9,1.67),(.999999,1.4)])
def test_Fanno_integral_against_independent_positive_differential_quadrature(m,g):
    expected=quad(lambda x:2*(1-x*x)/(g*x**3*(1+(g-1)*x*x/2)),m,1,epsabs=1e-20,epsrel=1e-11)[0]
    assert fanno_remaining_darcy_friction(m,g)==pytest.approx(expected,rel=3e-10,abs=1e-22)


@pytest.mark.parametrize('g,m1,k',[(1.1,.15,2.),(1.4,.25,1.),(1.67,.3,.8)])
def test_subcritical_capacity_against_independent_P_T_velocity_momentum_energy_ODE(g,m1,k):
    rg=300.;cp=g*rg/(g-1);p0=1e6;t0=500.;t1=t0/(1+(g-1)*m1*m1/2)
    p1=p0*(t1/t0)**(g/(g-1));u1=m1*math.sqrt(g*rg*t1);flux=p1/(rg*t1)*u1
    # State integration has no F(M) lookup or production pressure/Mach roots.
    def rhs(x,y):
        u,p,t,wall=y;du=g*u**3/(2*(g*rg*t-u*u))
        return [du,-flux*du-.5*flux*u,-u/cp*du,.5*flux*u]
    sol=solve_ivp(rhs,(0,k),[u1,p1,t1,0],method='DOP853',rtol=1e-12,atol=[1e-10,1e-5,1e-9,1e-5],max_step=k/25)
    assert sol.success
    u2,p2,t2,wall=sol.y[:,-1]
    q=fanno(heat_capacity_ratio=g,integrated_darcy_friction=k,back_pressure_Pa_abs=p2)
    out=evaluate_fanno_relief_line(q)
    assert out.status=='computed' and out.regime=='subcritical'
    assert out.profile[0].Mach==pytest.approx(m1,rel=1e-9)
    assert out.mass_flux_kg_m2_s==pytest.approx(flux,rel=1e-9)
    assert out.profile[-1].velocity_m_s==pytest.approx(u2,rel=1e-9)
    assert out.profile[-1].temperature_K==pytest.approx(t2,rel=1e-9)
    assert out.wall_momentum_loss_Pa==pytest.approx(wall,rel=1e-9)
    assert abs(out.momentum_residual_Pa)<1e-4
    assert out.maximum_specific_energy_residual_J_kg<1e-7
    assert out.entropy_increase_J_kg_K>0


def test_choked_profile_conserves_mass_energy_and_loses_stagnation_pressure():
    out=evaluate_fanno_relief_line(fanno())
    assert out.status=='computed' and out.regime=='choked_at_line_exit'
    assert out.profile[-1].Mach==1
    for p in out.profile:
        assert p.mass_flux_kg_m2_s==pytest.approx(out.mass_flux_kg_m2_s,rel=1e-14)
        assert p.stagnation_temperature_K==pytest.approx(500,rel=1e-14)
    assert all(b.stagnation_pressure_Pa_abs<a.stagnation_pressure_Pa_abs for a,b in zip(out.profile,out.profile[1:]))
    assert out.entropy_increase_J_kg_K>0 and out.wall_momentum_loss_Pa>0
    assert out.original_total_K_expansion_calibration_qualified is False


def test_sonic_event_is_continuous_and_lower_back_pressure_does_not_increase_capacity():
    baseline=evaluate_fanno_relief_line(fanno());pc=baseline.critical_back_pressure_Pa_abs
    sub=evaluate_fanno_relief_line(fanno(back_pressure_Pa_abs=pc*(1+1e-5)))
    low=evaluate_fanno_relief_line(fanno(back_pressure_Pa_abs=pc/10))
    assert sub.status=='computed' and sub.regime=='subcritical'
    assert sub.mass_flow_kg_s<=baseline.mass_flow_kg_s
    assert sub.mass_flow_kg_s==pytest.approx(baseline.mass_flow_kg_s,rel=1e-8)
    assert low.mass_flow_kg_s==baseline.mass_flow_kg_s


def test_zero_distributed_friction_reuses_existing_ideal_nozzle_limit():
    from pipeline_integrity.gas_restricted_vent import ideal_vent_capacity
    from pipeline_integrity.gas_acoustic_pipe import IdealGasInternalEnergyEOS
    for pb in (8e5,1e5):
        q=fanno(integrated_darcy_friction=0,back_pressure_Pa_abs=pb)
        out=evaluate_fanno_relief_line(q)
        mass,*_=ideal_vent_capacity(stagnation_pressure_pa_abs=1e6,stagnation_temperature_K=500,
            ambient_pressure_pa_abs=pb,vent_area_m2=.002,discharge_coefficient=1.,eos=IdealGasInternalEnergyEOS(specific_gas_constant_J_kg_K=300,gamma=1.4))
        assert out.status=='computed' and out.mass_flow_kg_s==pytest.approx(mass,rel=1e-12)
        assert out.entropy_increase_J_kg_K==pytest.approx(0,abs=1e-10)


def test_friction_to_zero_and_low_Mach_asymptotic_limits():
    zero=evaluate_fanno_relief_line(fanno(integrated_darcy_friction=0)).mass_flow_kg_s
    flows=[evaluate_fanno_relief_line(fanno(integrated_darcy_friction=k)).mass_flow_kg_s for k in (.1,.01,.001)]
    assert flows[0]<flows[1]<flows[2]<zero
    assert abs(flows[2]/zero-1)<.001
    errors=[]
    for eps in (.01,.001,.0001):
        q=fanno(back_pressure_Pa_abs=1e6*(1-eps),integrated_darcy_friction=2.)
        out=evaluate_fanno_relief_line(q)
        expected=.002*math.sqrt(2*(1e6/(300*500))*1e6*eps/(1+2))
        errors.append(abs(out.mass_flow_kg_s/expected-1))
    assert errors[1]<errors[0]/8 and errors[2]<errors[1]/8


def test_pressure_area_similarity_and_profile_sampling_do_not_add_new_losses():
    a=evaluate_fanno_relief_line(fanno())
    b=evaluate_fanno_relief_line(fanno(reservoir_stagnation_pressure_Pa_abs=3e6,back_pressure_Pa_abs=3e5,flow_area_m2=.006,
        samples_at_friction_fraction=(0.,.1,.3,.6,.8,1.)))
    assert b.mass_flow_kg_s==pytest.approx(a.mass_flow_kg_s*9)
    assert b.profile[0].Mach==pytest.approx(a.profile[0].Mach)
    assert b.profile[-1].pressure_Pa_abs==pytest.approx(a.profile[-1].pressure_Pa_abs*3)


def test_independent_explicit_midpoint_refinement_converges_to_endpoint():
    q=fanno(back_pressure_Pa_abs=8e5,integrated_darcy_friction=1.)
    out=evaluate_fanno_relief_line(q);m0=out.profile[0].Mach;g=1.4
    def slope(m):return g*m**3*(1+(g-1)*m*m/2)/(2*(1-m*m))
    errors=[]
    for n in (10,20,40):
        m=m0;step=1/n
        for i in range(n):m+=step*slope(m+.5*step*slope(m))
        errors.append(abs(m-out.profile[-1].Mach))
    assert errors[1]<errors[0]/3.7 and errors[2]<errors[1]/3.7


@pytest.mark.parametrize('changes',[{'integrated_darcy_friction':None},{'constant_area_adiabatic_ideal_gas_and_isentropic_entrance_reviewed':False},
    {'friction_integral_is_not_automatically_total_K_or_disk_KR_reviewed':False}])
def test_Fanno_missing_constitutive_mapping_is_withheld(changes):
    assert evaluate_fanno_relief_line(fanno(**changes)).status=='needs_data'


def test_Fanno_zero_drive_support_and_root_failure_are_explicit():
    assert evaluate_fanno_relief_line(fanno(back_pressure_Pa_abs=1e6)).mass_flow_kg_s==0
    assert evaluate_fanno_relief_line(fanno(minimum_inlet_Mach=.9)).status=='insufficient_Mach_support'
    assert evaluate_fanno_relief_line(fanno(maximum_root_iterations=1)).status=='not_converged'
