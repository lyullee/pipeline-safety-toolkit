"""Lossless wave-equation delay reference with a nonlinear closing orifice.

The boundary is actual effective flow area, not an assumed linear flow ramp.
Reservoir reflection returns after two one-way acoustic transit times.
"""
import math

import numpy as np
import pytest

from pipeline_integrity.contracts import RunContext
from pipeline_integrity.transient import History,LiquidNode,LiquidPipe,LiquidTransient,LiquidValve,simulate_liquid_transient


G=9.80665
RHO=998.0
C=1000.0
L=50.0
D=0.2
AREA=math.pi*D**2/4
SPEED=0.05
FLOW=AREA*SPEED
RETURN_TIME=2*L/C
CONTEXT=RunContext(asset_id="finite_valve",scenario_id="authored_area_history",clock_id="elapsed",origin="synthetic")


def case(head,closing_time,segments,*,opening=False):
    dt=L/(segments*C)
    cda=FLOW/math.sqrt(2*G*head)
    nodes=tuple(LiquidNode(id=name,elevation_m=0.0,initial_head_m=initial,boundary=boundary,
        history=History(time_s=(0.0,),values=(initial if boundary=="head" else 0.0,)))
        for name,initial,boundary in (("reservoir",head,"head"),("valve_inlet",head,"withdrawal"),("ambient",0.0,"head")))
    return LiquidTransient(nodes=nodes,
        pipes=(LiquidPipe(id="pipe",start_node="reservoir",end_node="valve_inlet",length_m=L,
            diameter_m=D,wave_speed_m_s=C,darcy_friction_factor=0.0,initial_volume_flow_m3_s=0.0 if opening else FLOW),),
        valves=(LiquidValve(id="closing_valve",start_node="valve_inlet",end_node="ambient",
            effective_area_m2=History(time_s=(0.0,closing_time),values=(0.0,cda) if opening else (cda,0.0)),
            initial_volume_flow_m3_s=0.0 if opening else FLOW,parameter_source_id="authored_area_ramp"),),
        duration_s=closing_time+3*RETURN_TIME,requested_timestep_s=dt,density_kg_m3=RHO,
        ambient_pressure_pa_abs=101325.0,vapour_pressure_pa_abs=2000.0,
        maximum_wave_speed_adjustment=1e-12,mass_balance_tolerance_m3=1e-8,
        valve_head_tolerance_m=1e-8,valve_flow_tolerance_m3_s=1e-11)


def delay_reference(times,head,closing_time,lag,*,opening=False):
    """Exact sampled lossless travelling-wave compatibility, no pipe grid."""
    b=C/(G*AREA)
    cda=FLOW/math.sqrt(2*G*head)
    heads=[]
    flows=[]
    for i,t in enumerate(times):
        incident=head+b*(0.0 if opening else FLOW) if i<lag else 2*head-heads[i-lag]+b*flows[i-lag]
        fraction=min(1,t/closing_time) if opening else max(0,1-t/closing_time)
        coefficient=b*cda*fraction*math.sqrt(2*G)
        # H + B*CdA(t)*sqrt(2gH) = incident. All selected states have H>0.
        assert incident>0
        sqrt_head=2*incident/(math.sqrt(coefficient**2+4*incident)+coefficient)
        h=sqrt_head**2
        heads.append(h)
        flows.append((incident-h)/b)
    return np.array(heads),np.array(flows)


@pytest.mark.parametrize("head",[100.0,500.0])
@pytest.mark.parametrize("closing_transits",[0.5,2.0,8.0])
def test_finite_area_closure_at_three_resolutions_matches_independent_delay_boundary(head,closing_transits):
    closing=closing_transits*RETURN_TIME
    for segments in (10,20,40):
        q=case(head,closing,segments)
        result=simulate_liquid_transient(q,CONTEXT)
        assert result["status"]=="computed",result["notes"]
        dt=result["timestep_s"]
        lag=round(RETURN_TIME/dt)
        assert lag*dt==pytest.approx(RETURN_TIME,abs=1e-14)
        assert result["grid"]["pipe"]["relative_wave_speed_adjustment"]<1e-12
        expected_head,expected_flow=delay_reference(result["time_s"],head,closing,lag)
        observed=result["pipes"]["pipe"]["end_head_m"]
        flow=result["valves"]["closing_valve"]["volume_flow_m3_s"]
        assert observed==pytest.approx(expected_head,abs=2e-7)
        assert flow==pytest.approx(expected_flow,abs=1e-10)
        assert result["maximum_junction_residual_m3_s"]<q.valve_flow_tolerance_m3_s
        assert result["maximum_pipe_volume_residual_m3"]<q.mass_balance_tolerance_m3
        closed=[i for i,t in enumerate(result["time_s"]) if t>=closing]
        assert max(abs(flow[i]) for i in closed)<1e-12
        assert min(observed)>0
        if closing_transits<1:
            # Completion before first reflected wave has the full Joukowsky jump.
            assert max(observed)-head==pytest.approx(C*SPEED/G,abs=2e-7)


def test_static_head_changes_partial_closure_flow_without_reinterpreting_area_as_flow():
    closing=8*RETURN_TIME
    early=[]
    for head in (100.0,500.0):
        q=case(head,closing,20)
        result=simulate_liquid_transient(q,CONTEXT)
        assert result["status"]=="computed",result["notes"]
        i=round((RETURN_TIME/2)/result["timestep_s"])
        fraction=1-result["time_s"][i]/closing
        actual=result["valves"]["closing_valve"]["volume_flow_m3_s"][i]
        assert abs(actual-FLOW*fraction)>1e-9
        early.append(actual/FLOW)
    assert abs(early[0]-early[1])>1e-5


@pytest.mark.parametrize("head",[100.0,500.0])
@pytest.mark.parametrize("opening_transits",[0.5,8.0])
def test_finite_opening_pressure_depression_and_return_waves_match_delay_boundary(head,opening_transits):
    opening_time=opening_transits*RETURN_TIME
    for segments in (10,20,40):
        q=case(head,opening_time,segments,opening=True)
        result=simulate_liquid_transient(q,CONTEXT)
        assert result["status"]=="computed",result["notes"]
        lag=round(RETURN_TIME/result["timestep_s"])
        expected_head,expected_flow=delay_reference(result["time_s"],head,opening_time,lag,opening=True)
        observed=result["pipes"]["pipe"]["end_head_m"]
        assert observed==pytest.approx(expected_head,abs=2e-7)
        assert result["valves"]["closing_valve"]["volume_flow_m3_s"]==pytest.approx(expected_flow,abs=1e-10)
        assert min(observed)<head and min(observed)>0
        assert result["maximum_pipe_volume_residual_m3"]<q.mass_balance_tolerance_m3
        assert result["maximum_junction_residual_m3_s"]<q.valve_flow_tolerance_m3_s
        if opening_transits<1:
            # Completing the area motion before a reservoir reflection gives the
            # independent static-head-dependent first-wave opening relation.
            parameter=(C*SPEED/G)**2/(2*head)
            depression=math.sqrt(parameter*(2*head+parameter))-parameter
            index=round(opening_time/result["timestep_s"])
            assert head-observed[index]==pytest.approx(depression,abs=2e-7)
