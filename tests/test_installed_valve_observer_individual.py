"""Independent pressure-energy and trim inverses, not source worked examples."""
import math

import pytest

from pipeline_integrity.installed_valve_observer import InstalledValveRequest,evaluate_installed_valve
from pipeline_integrity.models.library_models import LiquidControlValve

RHO=999.1032907570233
CV_PER_KV=1.1560992283536566


def request(**changes):
    state=LiquidControlValve(density_kg_m3=RHO,vapour_pressure_pa_abs=2300,
        critical_pressure_pa_abs=22e6,viscosity_pa_s=1e-5,inlet_pressure_pa_abs=1e6,
        outlet_pressure_pa_abs=5e5,volume_flow_m3_s=.01,inlet_diameter_m=.05,
        outlet_diameter_m=.05,valve_diameter_m=.05,pressure_recovery_factor=.9,valve_style_factor=1)
    data=dict(liquid_state=state,target_volume_flow_m3_s=.01,valve_outlet_pressure_pa_abs=1e6,
        lift_lower=.05,lift_upper=.95,curve_basis_id='supplied_operating_trim',
        pressure_balance_basis_id='same_actual_flow_static_pa',
        continuous_increasing_trim_on_bracket_reviewed=True,
        single_liquid_unchoked_turbulent_state_reviewed=True,same_flow_static_pressure_balance_reviewed=True)
    data.update(changes)
    return InstalledValveRequest(**data)


def run(q=None, *, pump=lambda f:2e5,system=lambda f:0,trim=lambda h:200*h):
    return evaluate_installed_valve(q or request(),pump_pressure_rise_pa=pump,
        nonvalve_system_pressure_requirement_pa=system,trim_cv_at_lift=trim)


def required_cv(flow,dp,rho=RHO):
    # SI pressure power balance and conventional Kv->Cv unit definition.
    return 3600*flow*math.sqrt((rho/RHO)*1e5/dp)*CV_PER_KV


@pytest.mark.parametrize('flow,dp,rho',[(.01,2e5,RHO),(.02,3e5,850),(.005,1e5,1100)])
def test_linear_trim_inverse_SI_pressure_flow_law(flow,dp,rho):
    q=request(target_volume_flow_m3_s=flow)
    q=q.model_copy(update={'liquid_state':q.liquid_state.model_copy(update={'density_kg_m3':rho})})
    out=run(q,pump=lambda f:dp)
    assert out.status=='computed'
    cv=required_cv(flow,dp,rho)
    assert out.required_cv==pytest.approx(cv,rel=1e-12)
    assert out.lift_fraction==pytest.approx(cv/200,rel=1e-11)
    assert out.local_static_gain_m3_s_per_lift==pytest.approx(200*flow/cv,rel=1e-8)
    assert out.valve_inlet_pressure_pa_abs==pytest.approx(q.valve_outlet_pressure_pa_abs+dp)


@pytest.mark.parametrize('rangeability',[10,30,80])
def test_equal_percentage_inverse_and_local_gain(rangeability):
    q=request()
    cv=required_cv(.01,2e5)
    trim=lambda h:150*rangeability**(h-1)
    out=run(q,trim=trim)
    h=1+math.log(cv/150)/math.log(rangeability)
    assert out.status=='computed'
    assert out.lift_fraction==pytest.approx(h,abs=1e-11)
    assert out.local_static_gain_m3_s_per_lift==pytest.approx(.01*math.log(rangeability),rel=2e-5)
    # Lower operating-range capacity remains positive; it is not hard shutoff.
    assert trim(0)>0


@pytest.mark.parametrize('a,b,static',[(2e5,3e8,1e4),(5e5,1e9,1e4),(2e5,0,2e4)])
def test_installed_gain_from_analytic_energy_balance_and_independent_flow_roots(a,b,static):
    flow=.01;dp=a-static-b*flow**2
    pump=lambda f:a-.4*b*f*f
    system=lambda f:static+.6*b*f*f
    out=run(pump=pump,system=system)
    assert out.status=='computed'
    cv=required_cv(flow,dp)
    expected_gain=200/(cv*(1/flow+b*flow/dp))
    assert out.local_static_gain_m3_s_per_lift==pytest.approx(expected_gain,rel=2e-5)
    # Solve explicit independent quadratic system: a-static = b Q² + valve R Q².
    def observed_flow(h):
        rv=1e5*(3600*CV_PER_KV/(200*h))**2
        return math.sqrt((a-static)/(b+rv))
    h=out.lift_fraction;eps=1e-5
    assert observed_flow(h)==pytest.approx(flow,rel=1e-12)
    assert (observed_flow(h+eps)-observed_flow(h-eps))/(2*eps)==pytest.approx(expected_gain,rel=1e-7)
    assert out.global_operating_point_uniqueness_proved is False


def test_pressure_origin_and_placeholder_native_flow_do_not_change_actual_observation():
    q=request();other=q.model_copy(update={'valve_outlet_pressure_pa_abs':2e6,
        'liquid_state':q.liquid_state.model_copy(update={'volume_flow_m3_s':.3})})
    a,b=run(q),run(other)
    assert b.status=='computed'
    assert b.lift_fraction==pytest.approx(a.lift_fraction)
    assert b.local_static_gain_m3_s_per_lift==pytest.approx(a.local_static_gain_m3_s_per_lift)


@pytest.mark.parametrize('field',['continuous_increasing_trim_on_bracket_reviewed',
    'single_liquid_unchoked_turbulent_state_reviewed','same_flow_static_pressure_balance_reviewed'])
def test_unknown_review_withholds_before_callbacks(field):
    q=request(**{field:False})
    out=run(q,pump=lambda f:pytest.fail('unknown invoked curve'))
    assert out.status=='needs_data'


def test_missing_curve_is_not_invented():
    assert run(trim=None).status=='needs_data'


def test_insufficient_range_is_not_extrapolated():
    assert run(trim=lambda h:2*h).status=='not_bracketed'


def test_different_reference_bores_are_not_silently_mixed():
    q=request();q=q.model_copy(update={'liquid_state':q.liquid_state.model_copy(update={'inlet_diameter_m':.1})})
    assert run(q).status=='out_of_scope'


@pytest.mark.parametrize('pump',[lambda f:-1,lambda f:float('nan')])
def test_invalid_static_budget_or_nonfinite_curve(pump):
    assert run(pump=pump).status=='out_of_scope'


def test_low_re_branch_is_not_credited_by_turbulent_observer():
    q=request();q=q.model_copy(update={'liquid_state':q.liquid_state.model_copy(update={'viscosity_pa_s':1})})
    assert run(q).status=='out_of_scope'


def test_choked_native_branch_is_not_credited():
    assert run(request(valve_outlet_pressure_pa_abs=1e4),pump=lambda f:2e5).status=='out_of_scope'


@pytest.mark.parametrize('trim',[lambda h:200*(1-h),lambda h:0,lambda h:float('inf')])
def test_invalid_or_nonmonotone_trim_rejected(trim):
    with pytest.raises(ValueError):run(trim=trim)


def test_endpoint_inverse_retained_without_two_sided_gain():
    cv=required_cv(.01,2e5)
    q=request(lift_lower=.2)
    out=run(q,trim=lambda h:cv+200*(h-.2))
    assert out.status=='singular_local_response' and out.lift_fraction==pytest.approx(.2)
    assert out.local_static_gain_m3_s_per_lift is None


def test_operating_turnpoint_is_not_hidden_by_finite_inverse():
    # dp proportional Q² makes required Cv independent of Q: infinite static sensitivity.
    out=run(pump=lambda f:2e9*f*f)
    assert out.status=='singular_local_response' and out.lift_fraction is not None


def test_reversed_response_has_no_stable_branch_claim():
    out=run(pump=lambda f:2e13*f**4)
    assert out.status=='singular_local_response'


def test_gain_at_trim_kink_fails_step_refinement():
    cv=required_cv(.01,2e5)
    # Unresolved local curvature; continuous monotonic, but not a resolved derivative.
    trim=lambda h:cv+200*(h-.5)+100*math.copysign(abs(h-.5)**1.2,h-.5)
    out=run(request(lift_lower=.4,lift_upper=.6),trim=trim)
    assert out.status=='singular_local_response'


def test_invalid_interval_rejected():
    with pytest.raises(ValueError):request(lift_lower=.9,lift_upper=.2)


@pytest.mark.parametrize('field,value',[('valve_outlet_pressure_pa_abs',1000),('critical_pressure_pa_abs',1000)])
def test_declared_liquid_phase_pressure_support_is_checked(field,value):
    q=request()
    if field=='critical_pressure_pa_abs':
        q=q.model_copy(update={'liquid_state':q.liquid_state.model_copy(update={field:value})})
        # This impossible liquid state is rejected by request revalidation;
        # it cannot proceed to the operating-domain calculation.
        with pytest.raises(ValueError, match='vapour pressure must be below'):
            run(q)
        return
    else:q=q.model_copy(update={field:value})
    assert run(q).status=='out_of_scope'
