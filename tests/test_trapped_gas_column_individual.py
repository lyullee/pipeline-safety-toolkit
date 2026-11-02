"""Independent phase-plane work quadrature, oscillation and support events."""
import math

import numpy as np
import pytest
from scipy.integrate import quad
from scipy.optimize import brentq

from pipeline_integrity.contracts import EvidenceRef,RunContext
from pipeline_integrity.transient import History
from pipeline_integrity.trapped_gas_column import TrappedGasColumnRequest,evaluate_trapped_gas_column,verify_trapped_gas_column_record


def case(**updates):
    context=RunContext(asset_id="liquid_column",scenario_id="authored_pocket",clock_id="elapsed",origin="synthetic",
        evidence=(EvidenceRef(id="authored",citation="Original constant-inertance pocket test geometry",origin="synthetic"),))
    data=dict(id="closed_pocket",asset_id=context.asset_id,clock_id=context.clock_id,
        liquid_density_kg_m3=1000.0,effective_column_length_m=20.0,inner_diameter_m=0.2,
        initial_gas_volume_m3=0.03,initial_gas_pressure_Pa_abs=200000.0,polytropic_exponent=1.4,
        drive_pressure_Pa_abs=History(time_s=(0.0,),values=(300000.0,)),
        gas_volume_support_m3=(0.005,0.08),sample_times_s=tuple(float(t) for t in np.linspace(0,5,101)),
        maximum_step_s=0.03,relative_tolerance=1e-10,
        absolute_log_volume_tolerance=1e-12,absolute_velocity_tolerance_m_s=1e-11,
        absolute_work_integration_tolerance_J=1e-9,energy_budget_absolute_tolerance_J=1e-5,
        fixed_inertance_single_pocket_and_polytropic_domain_reviewed=True,evidence_ids=("authored",))
    data.update(updates)
    return TrappedGasColumnRequest(**data),context


def phase_plane(q):
    # Independent pressure quadrature is used as work, rather than the solver's
    # logarithmic/expm1 potential expression.
    area=math.pi*q.inner_diameter_m**2/4
    mass=q.liquid_density_kg_m3*area*q.effective_column_length_m
    v0=q.initial_gas_volume_m3
    drive=q.drive_pressure_Pa_abs.values[0]
    def pressure(v):return q.initial_gas_pressure_Pa_abs*(v0/v)**q.polytropic_exponent
    def work(v):return quad(pressure,v,v0,epsabs=1e-9,epsrel=1e-12)[0]
    def kinetic(v):return drive*(v0-v)-work(v)
    turning=brentq(kinetic,q.gas_volume_support_m3[0],v0*(1-1e-5),xtol=1e-15)
    return area,mass,work,kinetic,turning


@pytest.mark.parametrize("exponent",[1.0,1.2,1.67])
def test_nonlinear_turning_volume_time_and_full_return_match_independent_work_quadrature(exponent):
    q,c=case(polytropic_exponent=exponent)
    area,mass,work,kinetic,turn=phase_plane(q)
    middle=(q.initial_gas_volume_m3+turn)/2
    half=(q.initial_gas_volume_m3-turn)/2
    def integrand(theta):
        volume=middle+half*math.cos(theta)
        return half*math.sin(theta)/(area*math.sqrt(2*kinetic(volume)/mass))
    turning_time=quad(integrand,0,math.pi,epsabs=1e-9,epsrel=1e-9)[0]
    q=q.model_copy(update={"sample_times_s":(0.0,turning_time,2*turning_time)})
    result=evaluate_trapped_gas_column(q,c)
    assert result["status"]=="computed_diagnostic",result["issue"]
    middle,last=result["rows"][1:]
    assert middle["gas_volume_m3"]==pytest.approx(turn,rel=1e-8)
    assert abs(middle["liquid_velocity_m_s"])<1e-6
    assert middle["gas_compression_work_J"]==pytest.approx(work(turn),rel=1e-8)
    assert last["gas_volume_m3"]==pytest.approx(q.initial_gas_volume_m3,rel=1e-8)
    assert abs(last["liquid_velocity_m_s"])<2e-6
    assert result["maximum_energy_budget_residual_J"]<1e-5
    assert result["gas_work_is_internal_energy_change"] is False


@pytest.mark.parametrize("exponent",[1.0,1.4])
def test_small_amplitude_limit_converges_to_independent_linear_spring_frequency(exponent):
    q,c=case(polytropic_exponent=exponent,
        drive_pressure_Pa_abs=History(time_s=(0.0,),values=(200000.0,)))
    area=math.pi*q.inner_diameter_m**2/4
    omega=math.sqrt(exponent*q.initial_gas_pressure_Pa_abs*area/
        (q.liquid_density_kg_m3*q.effective_column_length_m*q.initial_gas_volume_m3))
    times=np.linspace(0,2*math.pi/omega,121)
    errors=[]
    for amplitude in (0.02,0.01,0.005):
        request=q.model_copy(update={"initial_liquid_velocity_m_s":amplitude,
            "sample_times_s":tuple(map(float,times))})
        result=evaluate_trapped_gas_column(request,c)
        assert result["status"]=="computed_diagnostic",result["issue"]
        observed=np.array([r["liquid_velocity_m_s"] for r in result["rows"]])
        errors.append(float(np.max(np.abs(observed-amplitude*np.cos(omega*times)))))
    assert all(a>3.5*b>0 for a,b in zip(errors,errors[1:]))
    assert errors[-1]/0.005<0.003


def test_signed_friction_dissipates_during_both_directions_and_damps_compression_peak():
    q,c=case(darcy_friction_factor=0.04)
    damped=evaluate_trapped_gas_column(q,c)
    free=evaluate_trapped_gas_column(q.model_copy(update={"darcy_friction_factor":0.0}),c)
    assert damped["status"]==free["status"]=="computed_diagnostic",damped["issue"]
    rows=damped["rows"]
    assert min(r["liquid_velocity_m_s"] for r in rows)<0
    assert all(b["friction_dissipation_J"]>=a["friction_dissipation_J"]-1e-8 for a,b in zip(rows,rows[1:]))
    assert rows[-1]["friction_dissipation_J"]>0
    assert max(r["gas_pressure_Pa_abs"] for r in rows)<max(r["gas_pressure_Pa_abs"] for r in free["rows"])
    for row in rows:
        assert abs(row["polytropic_log_invariant_residual"])<2e-15
        assert row["drive_work_J"]==pytest.approx(300000*(q.initial_gas_volume_m3-row["gas_volume_m3"]),abs=2e-5)


def test_positive_volume_boundary_stops_at_independent_phase_plane_event_time():
    q,c=case()
    area,mass,_,kinetic,_=phase_plane(q)
    lower=0.025
    delta=q.initial_gas_volume_m3-lower
    def integrand(s):
        volume=q.initial_gas_volume_m3-delta*s*s
        return 2*delta*s/(area*math.sqrt(2*kinetic(volume)/mass))
    expected=quad(integrand,0,1,epsabs=1e-9,epsrel=1e-9)[0]
    result=evaluate_trapped_gas_column(q.model_copy(update={"gas_volume_support_m3":(lower,0.08)}),c)
    assert result["status"]=="computed_diagnostic",result["issue"]
    assert result["termination"]=="gas_volume_support_reached"
    last=result["rows"][-1]
    assert last["gas_volume_m3"]==pytest.approx(lower,abs=1e-14)
    assert last["time_s"]==pytest.approx(expected,abs=2e-8)
    assert last["time_s"]<q.sample_times_s[-1]


def test_quiescent_pressure_equilibrium_is_exact_for_arbitrary_supported_polytropic_exponent():
    q,c=case(polytropic_exponent=1.0,drive_pressure_Pa_abs=History(time_s=(0.0,),values=(200000.0,)))
    result=evaluate_trapped_gas_column(q,c)
    assert result["status"]=="computed_diagnostic",result["issue"]
    assert all(r["gas_volume_m3"]==q.initial_gas_volume_m3 and r["liquid_velocity_m_s"]==0
               and r["energy_budget_residual_J"]==0 for r in result["rows"])


def test_reverse_initial_motion_reaches_upper_volume_support_at_work_quadrature_time():
    upper=0.0303
    q,c=case(initial_liquid_velocity_m_s=-0.3,gas_volume_support_m3=(0.005,upper),
        drive_pressure_Pa_abs=History(time_s=(0.0,),values=(200000.0,)))
    area=math.pi*q.inner_diameter_m**2/4
    mass=q.liquid_density_kg_m3*area*q.effective_column_length_m
    initial=0.5*mass*q.initial_liquid_velocity_m_s**2
    def kinetic(volume):
        work=quad(lambda v:q.initial_gas_pressure_Pa_abs*(q.initial_gas_volume_m3/v)**q.polytropic_exponent,
            volume,q.initial_gas_volume_m3,epsabs=1e-10)[0]
        return initial+200000*(q.initial_gas_volume_m3-volume)-work
    expected=quad(lambda volume:1/(area*math.sqrt(2*kinetic(volume)/mass)),
        q.initial_gas_volume_m3,upper,epsabs=1e-11)[0]
    result=evaluate_trapped_gas_column(q,c)
    assert result["status"]=="computed_diagnostic",result["issue"]
    last=result["rows"][-1]
    assert result["termination"]=="gas_volume_support_reached"
    assert last["gas_volume_m3"]==pytest.approx(upper,abs=1e-14)
    assert last["time_s"]==pytest.approx(expected,abs=1e-9)
    assert last["liquid_velocity_m_s"]<0 and last["drive_work_J"]<0


def test_drive_ramp_and_ode_refinement_converge_with_energy_and_signed_work():
    q,c=case(drive_pressure_Pa_abs=History(time_s=(0.0,1.0,5.0),values=(200000.0,320000.0,220000.0)),
        maximum_step_s=0.2)
    results=[]
    for tolerance in (1e-5,1e-7,1e-10):
        result=evaluate_trapped_gas_column(q.model_copy(update={"relative_tolerance":tolerance,
            "energy_budget_absolute_tolerance_J":0.1,"energy_budget_relative_tolerance":1e-4}),c)
        assert result["status"]=="computed_diagnostic",result["issue"]
        results.append(result)
    # Temporal tolerance refinement with the pressure ramp unchanged.
    volumes=[np.array([r["gas_volume_m3"] for r in result["rows"]]) for result in results]
    first=float(np.max(np.abs(volumes[0]-volumes[2])))
    second=float(np.max(np.abs(volumes[1]-volumes[2])))
    assert first>3*second>0
    assert second/q.initial_gas_volume_m3<1e-5
    assert results[-1]["maximum_energy_budget_residual_J"]<1e-4


def test_domain_evidence_and_work_budget_guards_never_return_an_unqualified_trace():
    q,c=case()
    for update in ({"gas_volume_support_m3":(0.03,0.08)},
                   {"drive_pressure_Pa_abs":History(time_s=(0.0,),values=(-10000.0,))},
                   {"sample_times_s":(1.0,2.0)}):
        with pytest.raises(ValueError):
            TrappedGasColumnRequest.model_validate({**q.model_dump(),**update})
    with pytest.raises(ValueError,match="require review"):
        evaluate_trapped_gas_column(q.model_copy(update={"fixed_inertance_single_pocket_and_polytropic_domain_reviewed":False}),c)
    with pytest.raises(ValueError,match="evidence"):
        evaluate_trapped_gas_column(q,c.model_copy(update={"evidence":()}))
    limited=evaluate_trapped_gas_column(q.model_copy(update={"maximum_rhs_evaluations":1}),c)
    assert limited["status"]=="withheld" and limited["rows"]==[]


def test_receipt_replays_with_explicit_absolute_pressure_and_state_identity():
    import copy
    q,c=case(sample_times_s=(0.0,0.1,0.2))
    result=evaluate_trapped_gas_column(q,c)
    assert result["status"]=="computed_diagnostic",result["issue"]
    assert verify_trapped_gas_column_record(result,request=q,context=c)
    changed=copy.deepcopy(result)
    changed["rows"][-1]["gas_pressure_Pa_abs"]+=1000
    with pytest.raises(ValueError,match="receipt changed"):
        verify_trapped_gas_column_record(changed)
