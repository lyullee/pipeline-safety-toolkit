"""Independent filling ODE, parcel momentum/energy and arrival events."""
from decimal import Decimal,localcontext
import math

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from pipeline_integrity.prescribed_pipe_fill import ExponentialPipeFill,evaluate_prescribed_pipe_fill,time_to_prescribed_fill_position


def case(**updates):
    return ExponentialPipeFill(**{**dict(id="authored_fill",asset_id="pipe",clock_id="elapsed",fluid_basis_id="constant_liquid",
        area_m2=0.08,liquid_density_kg_m3=900.0,maximum_volume_flow_m3_s=0.04,rise_time_s=2.0,
        start_time_s=3.0,initial_filled_length_m=0.0,uniform_velocity_fixed_inlet_and_moving_front_reviewed=True),**updates})


def reference(q,stop,events=None):
    rho_a=q.liquid_density_kg_m3*q.area_m2
    target=q.maximum_volume_flow_m3_s/q.area_m2
    def rhs(t,y):
        x,v=y[:2]
        accel=(target-v)/q.rise_time_s
        mass=rho_a*x
        incoming_momentum=rho_a*v*v
        external=mass*accel
        incoming_energy=0.5*rho_a*v**3
        external_power=external*v
        return (v,accel,incoming_momentum+external,incoming_energy+external_power,
                q.area_m2*v,incoming_momentum,external,incoming_energy,external_power)
    solution=solve_ivp(rhs,(0,stop),(q.initial_filled_length_m,0,0,0,0,0,0,0,0),
        method="DOP853",rtol=2e-12,atol=1e-13,max_step=q.rise_time_s/20,dense_output=True,events=events)
    assert solution.success
    return solution


@pytest.mark.parametrize("rise",[0.05,2.0,20.0])
@pytest.mark.parametrize("initial_length",[0.0,5.0])
def test_front_mass_momentum_and_energy_match_independent_relaxation_ode(rise,initial_length):
    q=case(rise_time_s=rise,initial_filled_length_m=initial_length)
    solution=reference(q,8*rise)
    for time in (0.001*rise,0.5*rise,2*rise,8*rise):
        result=evaluate_prescribed_pipe_fill(q,q.start_time_s+time)
        assert result.status=="computed",result.notes
        values=result.values
        x,v,momentum,kinetic,volume,incoming_impulse,external_impulse,incoming_energy,external_work=solution.sol(time)
        assert values["front_position_m"]==pytest.approx(x,rel=2e-9,abs=1e-13)
        assert values["liquid_velocity_m_s"]==pytest.approx(v,rel=2e-9,abs=1e-13)
        assert values["added_liquid_volume_m3"]==pytest.approx(volume,rel=2e-9,abs=1e-13)
        assert values["stored_liquid_mass_kg"]==pytest.approx(q.liquid_density_kg_m3*(volume+q.area_m2*initial_length),rel=2e-9)
        assert values["axial_liquid_momentum_kg_m_s"]==pytest.approx(momentum,rel=2e-9,abs=1e-11)
        assert momentum==pytest.approx(incoming_impulse+external_impulse,rel=2e-11,abs=1e-12)
        assert values["liquid_kinetic_energy_J"]==pytest.approx(kinetic,rel=2e-9,abs=1e-12)
        assert kinetic==pytest.approx(incoming_energy+external_work,rel=2e-11,abs=1e-12)
        assert values["elbow_reaction_N"] is None and values["elbow_reaction_status"]=="needs_data"


@pytest.mark.parametrize("initial_length",[0.0,3.0])
def test_arrival_root_matches_independent_ode_front_crossing(initial_length):
    q=case(initial_filled_length_m=initial_length)
    scale=q.maximum_volume_flow_m3_s*q.rise_time_s/q.area_m2
    for factor in (0.001,1.0,10.0):
        target=initial_length+scale*factor
        def event(t,y):return y[0]-target
        event.terminal=True
        event.direction=1
        solution=reference(q,30*q.rise_time_s,events=event)
        assert len(solution.t_events[0])==1
        result=time_to_prescribed_fill_position(q,target)
        assert result.status=="computed",result.notes
        expected=q.start_time_s+solution.t_events[0][0]
        assert result.values["arrival_time_s"]==pytest.approx(expected,abs=1e-8)
        replay=evaluate_prescribed_pipe_fill(q,result.values["arrival_time_s"])
        assert replay.values["front_position_m"]==pytest.approx(target,abs=1e-9)


def test_tiny_startup_inventory_keeps_quadratic_volume_instead_of_cancelling_to_zero():
    q=case(start_time_s=0.0)
    z=1e-8
    result=evaluate_prescribed_pipe_fill(q,q.rise_time_s*z)
    assert result.status=="computed",result.notes
    with localcontext() as context:
        context.prec=60
        d=Decimal("1e-8")
        expected=float(Decimal(str(q.maximum_volume_flow_m3_s))*Decimal(str(q.rise_time_s))*(d+(-d).exp()-1))
    assert result.values["added_liquid_volume_m3"]>0
    assert result.values["added_liquid_volume_m3"]==pytest.approx(expected,rel=3e-15,abs=0)


@pytest.mark.parametrize("density_scale,area_scale",[(5.0,1.0),(1.0,4.0),(0.2,0.5)])
def test_density_and_same_velocity_area_flow_scaling_preserve_front(density_scale,area_scale):
    q=case()
    scaled=q.model_copy(update={"liquid_density_kg_m3":q.liquid_density_kg_m3*density_scale,
        "area_m2":q.area_m2*area_scale,"maximum_volume_flow_m3_s":q.maximum_volume_flow_m3_s*area_scale})
    baseline=evaluate_prescribed_pipe_fill(q,5.0).values
    actual=evaluate_prescribed_pipe_fill(scaled,5.0).values
    for key in ("front_position_m","liquid_velocity_m_s","liquid_acceleration_m_s2"):
        assert actual[key]==pytest.approx(baseline[key])
    for key in ("stored_liquid_mass_kg","axial_liquid_momentum_kg_m_s","inlet_axial_momentum_flux_N",
                "net_external_axial_force_on_fluid_N","liquid_kinetic_energy_J"):
        assert actual[key]==pytest.approx(baseline[key]*density_scale*area_scale)


def test_initial_rest_and_prestart_state_do_not_manufacture_a_nonzero_elbow_force():
    q=case()
    for time in (0.0,q.start_time_s):
        result=evaluate_prescribed_pipe_fill(q,time)
        assert result.status=="computed"
        assert result.values["front_position_m"]==0
        assert result.values["axial_momentum_accumulation_N"]==0
        assert result.values["net_external_axial_force_on_fluid_N"]==0
        assert result.values["elbow_reaction_N"] is None
    initial=time_to_prescribed_fill_position(q,0.0)
    assert initial.values["arrival_time_s"]==q.start_time_s


def test_input_clock_review_and_unresolvable_domain_are_explicit():
    q=case()
    for time in (-1.0,math.inf):
        with pytest.raises(ValueError):evaluate_prescribed_pipe_fill(q,time)
    with pytest.raises(ValueError,match="require review"):
        evaluate_prescribed_pipe_fill(q.model_copy(update={"uniform_velocity_fixed_inlet_and_moving_front_reviewed":False}),4.0)
    assert time_to_prescribed_fill_position(q,-1.0).status=="out_of_scope"
    early=evaluate_prescribed_pipe_fill(q.model_copy(update={"start_time_s":0.0}),1e-200)
    assert early.status=="out_of_scope" and early.values=={}
