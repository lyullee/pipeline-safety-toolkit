"""Pressure and volume-flow continuity at unequal-diameter/celerity junctions."""
import math
import numpy as np
import pytest
from pipeline_integrity.contracts import RunContext
from pipeline_integrity.transient import History,LiquidNode,LiquidPipe,LiquidTransient,simulate_liquid_transient


@pytest.mark.parametrize('d1,d2,c1,c2',[(.1,.2,1000.,500.),(.2,.1,500.,1000.),(.1,.1,500.,2000.),(.1,.2,500.,2000.)])
def test_unequal_celerity_and_diameter_junction_from_two_independent_continuity_equations(d1,d2,c1,c2):
    head=100.;amplitude=.05;dt=.01;sample=round((.12+100/c1)/dt)
    nodes=(LiquidNode(id='left',elevation_m=0.,initial_head_m=head,boundary='head',history=History(time_s=(0.,.1,.11),values=(head,head,head+amplitude))),
        LiquidNode(id='junction',elevation_m=0.,initial_head_m=head,boundary='withdrawal',history=History(time_s=(0.,),values=(0.,))),
        LiquidNode(id='right',elevation_m=0.,initial_head_m=head,boundary='head',history=History(time_s=(0.,),values=(head,))))
    q=LiquidTransient(nodes=nodes,pipes=(LiquidPipe(id='first',start_node='left',end_node='junction',length_m=100.,diameter_m=d1,wave_speed_m_s=c1,darcy_friction_factor=0.,initial_volume_flow_m3_s=0.),
        LiquidPipe(id='second',start_node='junction',end_node='right',length_m=100.,diameter_m=d2,wave_speed_m_s=c2,darcy_friction_factor=0.,initial_volume_flow_m3_s=0.)),
        duration_s=math.nextafter((sample+1)*dt,-math.inf),requested_timestep_s=dt,density_kg_m3=998.,ambient_pressure_pa_abs=101325.,vapour_pressure_pa_abs=2000.,
        maximum_wave_speed_adjustment=1e-12,mass_balance_tolerance_m3=1e-8)
    context=RunContext(asset_id='authored-junction',scenario_id='unequal-impedance',clock_id='elapsed',origin='synthetic')
    r=simulate_liquid_transient(q,context);assert r['status']=='computed',r['notes']
    z1=998*c1/(math.pi*d1*d1/4);z2=998*c2/(math.pi*d2*d2/4)
    incident=998*9.80665*amplitude
    # Unknown local pressure increments (reflected,transmitted): p_i+p_r=p_t;
    # the reflected wave's discharge points upstream, p_i/Z1-p_r/Z1=p_t/Z2.
    reflected,transmitted=np.linalg.solve(np.array([[1.,-1.],[1/z1,1/z2]]),np.array([-incident,incident/z1]))
    junction_head=r['pipes']['first']['end_head_m'][sample]
    observed_transmitted=(junction_head-head)*998*9.80665
    assert observed_transmitted==pytest.approx(transmitted,abs=2e-9)
    assert observed_transmitted-incident==pytest.approx(reflected,abs=2e-9)
    assert r['pipes']['second']['start_head_m'][sample]==pytest.approx(junction_head,abs=1e-12)
    assert r['maximum_junction_residual_m3_s']<1e-12
    assert r['all_volume_balances_within_tolerance']
