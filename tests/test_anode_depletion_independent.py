"""Independent prescribed-current quadrature and first-capacity-crossing oracle."""
import runpy
import pytest
from scipy.integrate import quad
from scipy.optimize import brentq
from pipeline_integrity.anode_lifetime import AnodeLifetimeRequest, evaluate_anode_lifetime

CASES,CONTEXT=runpy.run_path('examples/electrochemical_methods.py')['make_cases']()
BASE=CASES['protection.cathodic_budget']

@pytest.mark.parametrize('f0,f1,j0,j1',[(.1,.1,.2,.2),(.1,.7,.02,.08),(.1,.8,.1,.01)])
@pytest.mark.parametrize('capacity_ratio',[.25,.8,1.5])
def test_charge_ledger_and_first_depletion_match_independent_current_integral(f0,f1,j0,j1,capacity_ratio):
    duration=20.;area=12.;drain=.03
    def current(t):
        u=t/duration
        return area*(f0+u*(f1-f0))*(j0+u*(j1-j0))+drain
    demand=quad(current,0.,duration,epsabs=1e-12,epsrel=1e-12)[0]
    capacity=capacity_ratio*demand
    parameters=BASE.parameters|dict(time_s=[0.,duration],protected_area_m2=area,
        bare_surface_fraction=[f0,f1],bare_surface_current_density_A_m2=[j0,j1],additional_current_drain_A=[drain,drain],
        anode_electrochemical_capacity_C_kg=1e4,anode_utilization_fraction=.8,
        single_anode_mass_kg=capacity/8000.,installed_anodes=1,single_anode_minimum_available_current_A=10.)
    q=AnodeLifetimeRequest(budget=BASE.model_copy(update={'parameters':parameters}),time_absolute_tolerance_s=1e-8)
    r=evaluate_anode_lifetime(q,CONTEXT)
    assert r['status']=='computed'
    assert r['demand_charge_C']==pytest.approx(demand,rel=2e-12)
    assert r['consumed_charge_C']+r['charge_deficit_C']==pytest.approx(demand)
    assert r['consumed_charge_C']+r['remaining_usable_charge_C']==pytest.approx(capacity)
    if capacity_ratio<1:
        event=brentq(lambda t:quad(current,0.,t,epsabs=1e-12)[0]-capacity,0.,duration,xtol=1e-12)
        lo,hi=r['depletion_time_bracket_s']
        assert lo-1e-10<=event<=hi+1e-10
        assert r['depletion_clock_time_s']==pytest.approx(event,abs=1e-8)
    else:
        assert r['depletion']=='not_reached_in_supplied_history'
        assert r['depletion_clock_time_s'] is None
    assert not r['future_extrapolation_performed']
