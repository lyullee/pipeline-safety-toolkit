"""Switched ODE/event and control-volume references independent of reflection."""
import math
import numpy as np
import pytest
from scipy.integrate import solve_ivp
from pipeline_integrity.rainfall_inventory import RainfallInventoryRequest as Request,evaluate_rainfall_inventory as evaluate

def request(times=(0.,2.,5.),rain=(.002,0.),drain=(.001,.001),initial=.001,**kw):
    return Request(id='authored',time_s=times,rain_depth_rate_m_s=rain,drain_capacity_depth_rate_m_s=drain,
        initial_water_depth_m=initial,footprint_area_m2=7.,uniform_footprint_prescribed_rates_and_dry_boundary_reviewed=True,**kw)

CASES=[((0.,2.,5.),(.002,0.),(.001,.001),.001),
 ((0.,2.,5.),(0.,.002),(.001,.001),.0005),
 ((0.,3.,7.,9.),(.002,0.,.001),(.001,.001,.001),0.),
 ((0.,1.,2.),(0.,0.),(0.,.001),0.),
 ((3.,7.,13.),(.0001,.0003),(.0002,.0002),.01),
 ((0.,1.,2.,4.),(.003,0.,.005),(.002,.005,.001),0.)]

@pytest.mark.parametrize('times,rain,drain,initial',CASES)
def test_switched_ode_with_dry_event(times,rain,drain,initial):
    observations=tuple(sorted(set(np.linspace(times[0],times[-1],101))|set(times)))
    q=request(times,rain,drain,initial,observation_times_s=observations);a=evaluate(q).values
    ref={};h=initial;dry_events=[];drained=0.;maximum=h
    for left,right,r,d in zip(times,times[1:],rain,drain):
        net=r-d
        def zero(t,y):return y[0]
        zero.terminal=True;zero.direction=-1
        if h==0. and net<=0:
            end=left;sol=None
        else:
            # Extend only the numerical event bracket by clock roundoff so an
            # exact end-of-interval dry event is not lost to dense-output ulps.
            clock_roundoff=64*math.ulp(right)
            sol=solve_ivp(lambda t,y:[net],(left,right+clock_roundoff),[h],events=zero if net<0 else None,
                dense_output=True,rtol=1e-11,atol=1e-14)
            assert sol.success;end=min(right,sol.t[-1])
            if sol.t_events and len(sol.t_events[0]) and sol.t_events[0][0]<=right+clock_roundoff:
                event=float(sol.t_events[0][0]);end=right if abs(event-right)<=clock_roundoff else event;dry_events.append(end)
        for t in observations:
            if left<=t<=right:ref[t]=max(0.,float(sol.sol(t)[0])) if sol is not None and t<=end else 0.
        last=max(0.,float(sol.sol(right)[0])) if sol is not None and end==right else 0.
        drained+=h+r*(right-left)-last;h=last;maximum=max(maximum,h)
    assert [v['depth_m'] for v in a['observations']]==pytest.approx([ref[t] for t in observations],abs=1e-12)
    assert a['cumulative_actual_drained_depth_m']==pytest.approx(drained,abs=1e-12)
    assert a['maximum_depth_m']==pytest.approx(maximum,abs=1e-12)
    got=[v['time_s'] for v in a['events'] if v['kind']=='wet_to_dry']
    assert got==pytest.approx(dry_events,abs=1e-10)
    assert abs(a['depth_budget_residual_m'])<1e-12

@pytest.mark.parametrize('density,gravity,area',[(1000.,9.81,3.),(800.,1.62,12.),(None,None,.5)])
def test_volume_mass_pressure_and_actual_drain_scaling(density,gravity,area):
    q=request(water_density_kg_m3=density,gravity_m_s2=gravity,observation_times_s=(1.,)).model_copy(update={'footprint_area_m2':area})
    v=evaluate(q).values;o=v['observations'][0];h=.002
    assert o['depth_m']==pytest.approx(h,abs=1e-15)
    assert o['volume_m3']==pytest.approx(area*h)
    assert o['mass_kg']==(None if density is None else pytest.approx(area*h*density))
    assert o['bottom_hydrostatic_pressure_pa']==(None if gravity is None else pytest.approx(density*gravity*h))
    assert abs(v['volume_budget_residual_m3'])<1e-12

def test_known_interior_dry_event_and_capacity_is_not_outflow():
    v=evaluate(request((0.,10.),(0.,),(.002,),.003)).values
    assert v['events']==[dict(kind='wet_to_dry',time_s=1.5)]
    assert v['cumulative_drain_capacity_depth_m']==pytest.approx(.02)
    assert v['cumulative_actual_drained_depth_m']==pytest.approx(.003)
    assert v['cumulative_unmet_drawdown_depth_m']==pytest.approx(.017)
    assert v['final']['depth_m']==0.

def test_dry_roof_rain_passes_through_without_storage():
    v=evaluate(request((0.,4.),(.001,),(.003,),0.)).values
    assert v['maximum_depth_m']==0.
    assert v['cumulative_actual_drained_depth_m']==pytest.approx(.004)
    assert v['cumulative_unmet_drawdown_depth_m']==pytest.approx(.008)

def test_interval_partition_invariance_and_dry_wet_events():
    a=evaluate(request((0.,4.,10.),(.002,0.),(.001,.001),0.)).values
    b=evaluate(request((0.,2.,4.,6.,8.,10.),(.002,.002,0.,0.,0.),(.001,)*5,0.)).values
    for key in ('maximum_depth_m','cumulative_actual_drained_depth_m','cumulative_unmet_drawdown_depth_m'):
        assert a[key]==pytest.approx(b[key],abs=1e-14)
    assert a['events']==b['events']==[dict(kind='dry_to_wet',time_s=0.),dict(kind='wet_to_dry',time_s=8.)]

def test_absent_review_withheld():
    assert evaluate(request().model_copy(update={'uniform_footprint_prescribed_rates_and_dry_boundary_reviewed':False})).status=='needs_data'

def test_invalid_time_rate_and_pressure_contract():
    for update in ({'time_s':(0.,0.,5.)},{'rain_depth_rate_m_s':(.001,)},{'gravity_m_s2':9.81}):
        with pytest.raises(ValueError):evaluate(request().model_copy(update=update))
