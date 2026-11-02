"""Independent head/parcel references for a declared liquid opening."""
from decimal import Decimal, localcontext
import math

import pytest
from scipy.integrate import solve_ivp
from pydantic import ValidationError

from pipeline_integrity.liquid_orifice import LiquidOpeningReleaseRequest as Request, evaluate_liquid_opening_release as evaluate


def request(**changes):
    values = dict(opening_area_m2=.00017, upstream_available_pressure_pa_abs=340000.,
        receiver_static_pressure_pa_abs=110000., liquid_density_kg_m3=870.,
        supplied_discharge_coefficient=.73, liquid_state_basis_id="declared-state",
        coefficient_geometry_basis_id="reviewed-opening", available_pressure_head_and_stationary_opening_reviewed=True,
        noncavitating_single_liquid_incompressible_domain_reviewed=True,
        supplied_coefficient_applicability_reviewed=True)
    values.update(changes)
    return Request(**values)


@pytest.mark.parametrize("density,drop,length", [(770.,19000.,.13),(1100.,470000.,.71),(31.,8.,.009)])
def test_ideal_parcel_pressure_ode_and_power(density,drop,length):
    # An independently accelerated parcel traverses a constant pressure gradient.
    acceleration=drop/(density*length)
    def exit_event(t,y):return y[0]-length
    exit_event.terminal=True
    sol=solve_ivp(lambda t,y:(y[1],acceleration),(0,4*math.sqrt(2*length/acceleration)),
        (0.,0.),events=exit_event,rtol=2e-11,atol=1e-13,max_step=math.sqrt(2*length/acceleration)/30)
    velocity=sol.y_events[0][0,1]
    q=request(liquid_density_kg_m3=density,upstream_available_pressure_pa_abs=100000.+drop,
        receiver_static_pressure_pa_abs=100000.,supplied_discharge_coefficient=1.)
    result=evaluate(q)
    assert result.status=="computed"
    assert result.actual_volume_flow_m3_s==pytest.approx(q.opening_area_m2*velocity,rel=1e-10)
    assert result.available_pressure_volume_power_W==pytest.approx(.5*result.mass_flow_kg_s*velocity**2,rel=2e-10)
    assert not result.vena_contracta_or_actual_jet_velocity_inferred


@pytest.mark.parametrize("cd",[.12,.47,.91,1.])
def test_supplied_coefficient_decimal_head_reference(cd):
    q=request(supplied_discharge_coefficient=cd)
    r=evaluate(q)
    with localcontext() as ctx:
        ctx.prec=70
        d=Decimal.from_float
        velocity=(2*(d(q.upstream_available_pressure_pa_abs)-d(q.receiver_static_pressure_pa_abs))/d(q.liquid_density_kg_m3)).sqrt()
        flow=d(cd)*d(q.opening_area_m2)*velocity
    assert r.actual_volume_flow_m3_s==pytest.approx(float(flow),rel=3e-15)
    assert r.mass_flow_kg_s==pytest.approx(float(flow)*q.liquid_density_kg_m3,rel=3e-15)
    assert abs(r.native_pressure_relation_residual_pa)<2e-9
    assert not r.discharge_coefficient_estimated and not r.field_calibration_qualified


def test_native_receives_actual_flux_velocity_and_density(monkeypatch):
    import fluids.core
    original=fluids.core.dP_from_K
    captured=[]
    def capture(K,rho,V):
        captured.append((K,rho,V))
        return original(K,rho,V)
    monkeypatch.setattr(fluids.core,"dP_from_K",capture)
    q=request();r=evaluate(q)
    K,rho,V=captured[0]
    assert rho==q.liquid_density_kg_m3
    assert K==pytest.approx(1/q.supplied_discharge_coefficient**2)
    assert V==pytest.approx(r.actual_volume_flow_m3_s/q.opening_area_m2)


@pytest.mark.parametrize("scale",[.01,3.,100.])
def test_area_pressure_density_and_coefficient_similarity(scale):
    base=evaluate(request())
    assert evaluate(request(opening_area_m2=.00017*scale)).actual_volume_flow_m3_s==pytest.approx(base.actual_volume_flow_m3_s*scale)
    q=request(upstream_available_pressure_pa_abs=110000.+230000.*scale,liquid_density_kg_m3=870.*scale)
    assert evaluate(q).actual_volume_flow_m3_s==pytest.approx(base.actual_volume_flow_m3_s)
    assert evaluate(request(upstream_available_pressure_pa_abs=10340000.,receiver_static_pressure_pa_abs=10110000.)).actual_volume_flow_m3_s==pytest.approx(base.actual_volume_flow_m3_s)


def test_zero_area_is_closed_not_an_invented_jet():
    r=evaluate(request(opening_area_m2=0.))
    assert r.status=="computed" and r.actual_volume_flow_m3_s==0
    assert r.mass_flow_kg_s==r.available_pressure_volume_power_W==0
    assert r.opening_area_mean_flux_velocity_m_s is None
    assert r.effective_total_head_coefficient is None


def test_zero_head_and_steady_duration_contract():
    r=evaluate(request(upstream_available_pressure_pa_abs=110000.))
    assert r.actual_volume_flow_m3_s==0 and r.opening_area_mean_flux_velocity_m_s==0
    r=evaluate(request(steady_duration_s=9.))
    assert r.status=="computed" and r.amount_status=="needs_data" and r.released_mass_kg is None
    assert evaluate(request(steady_duration_s=0.)).released_mass_kg==0
    q=request(steady_duration_s=9.,constant_boundary_state_for_duration_reviewed=True)
    r=evaluate(q)
    assert r.released_volume_m3==pytest.approx(9*r.actual_volume_flow_m3_s)
    assert r.released_mass_kg==pytest.approx(9*r.mass_flow_kg_s)


@pytest.mark.parametrize("field",["opening_area_m2","upstream_available_pressure_pa_abs","receiver_static_pressure_pa_abs",
    "liquid_density_kg_m3","supplied_discharge_coefficient","liquid_state_basis_id","coefficient_geometry_basis_id"])
def test_missing_required_state_is_not_defaulted(field):
    assert evaluate(request(**{field:None})).status=="needs_data"


@pytest.mark.parametrize("field",["available_pressure_head_and_stationary_opening_reviewed",
    "noncavitating_single_liquid_incompressible_domain_reviewed","supplied_coefficient_applicability_reviewed"])
def test_required_physical_review(field):
    assert evaluate(request(**{field:False})).status=="needs_data"


def test_reverse_and_endpoint_phase_screen():
    assert evaluate(request(upstream_available_pressure_pa_abs=90000.)).status=="out_of_scope"
    assert evaluate(request(saturation_pressure_pa_abs=110000.)).status=="out_of_scope"
    r=evaluate(request(saturation_pressure_pa_abs=10000.))
    assert r.status=="computed" and not r.cavitation_or_pressure_decay_solved


@pytest.mark.parametrize("field,value",[("opening_area_m2",-1.),("opening_area_m2",True),
    ("supplied_discharge_coefficient",0.),("supplied_discharge_coefficient",1.01),
    ("liquid_density_kg_m3",float("nan")),("receiver_static_pressure_pa_abs",0.),
    ("steady_duration_s",float("inf"))])
def test_invalid_contract(field,value):
    with pytest.raises(ValidationError):request(**{field:value})


def test_extreme_nonzero_flux_is_not_silently_clipped():
    assert evaluate(request(supplied_discharge_coefficient=1e-300)).status=="out_of_scope"
    assert evaluate(request(opening_area_m2=1e308)).status=="out_of_scope"
    assert evaluate(request(opening_area_m2=5e-324,supplied_discharge_coefficient=.01)).status=="out_of_scope"


def test_revalidation_replay_and_input_immutability():
    q=request();before=q.model_dump(mode="python")
    result=evaluate(q)
    assert evaluate(Request.model_validate_json(q.model_dump_json()))==result
    assert q.model_dump(mode="python")==before
    forged=q.model_copy(update={"liquid_density_kg_m3":-2.})
    with pytest.raises(ValidationError):evaluate(forged)
