"""Independent friction equilibrium/power references for supplied calibration."""
import math

import pytest
from pydantic import ValidationError
from scipy.integrate import quad
from scipy.optimize import brentq

from pipeline_integrity.drag_reduction_observer import DragReductionRequest as Request, evaluate_drag_reduction as evaluate


def request(**changes):
    values=dict(baseline_friction_drop_pa=170000.,supplied_fractional_drag_reduction=.31,
        baseline_volume_flow_magnitude_m3_s=.019,operating_boundary="fixed_friction_head",
        supplied_baseline_friction_flow_exponent=1.83,measured_state_and_calibration_basis_id="reviewed-same-state-turbulent-law",
        static_head_and_other_losses_separated_reviewed=True,same_turbulent_state_and_resistance_calibration_reviewed=True,
        frozen_exponent_and_multiplier_over_projected_flow_reviewed=True)
    values.update(changes)
    return Request(**values)


@pytest.mark.parametrize("exponent",[1.3,1/.55,2.])
@pytest.mark.parametrize("reduction",[-.2,0.,.53])
@pytest.mark.parametrize("boundary",["same_flow","fixed_friction_head","fixed_hydraulic_friction_power"])
def test_independent_distributed_friction_equilibrium_and_work(exponent,reduction,boundary):
    length,baseline_flow,base_gradient=371.,.031,713.
    baseline_drop=quad(lambda x:base_gradient,0,length)[0]
    coefficient=1-reduction
    def treated_drop(flow):
        return quad(lambda x:base_gradient*coefficient*(flow/baseline_flow)**exponent,0,length)[0]
    if boundary=="same_flow":flow=baseline_flow
    else:
        target=baseline_drop if boundary=="fixed_friction_head" else baseline_drop*baseline_flow
        def residual(flow):return (treated_drop(flow) if boundary=="fixed_friction_head" else treated_drop(flow)*flow)-target
        flow=brentq(residual,baseline_flow*.01,baseline_flow*30,xtol=1e-14,rtol=1e-13)
    q=request(baseline_friction_drop_pa=baseline_drop,baseline_volume_flow_magnitude_m3_s=baseline_flow,
        supplied_baseline_friction_flow_exponent=exponent,supplied_fractional_drag_reduction=reduction,operating_boundary=boundary)
    r=evaluate(q)
    assert r.status==r.projection_status=="computed"
    assert r.projected_flow_magnitude_m3_s==pytest.approx(flow,rel=2e-12)
    assert r.projected_friction_drop_pa==pytest.approx(treated_drop(flow),rel=3e-12)
    assert r.projected_hydraulic_friction_power_W==pytest.approx(quad(lambda x:flow*base_gradient*coefficient*(flow/baseline_flow)**exponent,0,length)[0],rel=3e-12)
    if boundary=="fixed_friction_head":assert r.projected_friction_drop_pa==baseline_drop
    if boundary=="fixed_hydraulic_friction_power":assert r.projected_hydraulic_friction_power_W==pytest.approx(baseline_drop*baseline_flow)
    assert not r.dose_chemistry_or_persistence_inferred


@pytest.mark.parametrize("treated",[0.,71000.,170000.,230000.])
def test_measured_same_flow_drop_defines_signed_reduction_without_chemistry(treated):
    q=request(supplied_fractional_drag_reduction=None,observed_treated_friction_drop_pa=treated,operating_boundary="same_flow")
    r=evaluate(q)
    assert r.treated_friction_drop_at_baseline_flow_pa==pytest.approx(treated)
    assert r.fractional_drag_reduction==pytest.approx((170000.-treated)/170000.)
    if treated>170000:assert r.fractional_drag_reduction<0


def test_fixed_head_and_power_limits_are_different_and_pump_limits_do_not_clip():
    head=evaluate(request());power=evaluate(request(operating_boundary="fixed_hydraulic_friction_power"))
    assert head.projected_flow_magnitude_m3_s>power.projected_flow_magnitude_m3_s
    r=evaluate(request(supplied_pump_flow_capacity_m3_s=.019,supplied_hydraulic_friction_power_capacity_W=100.))
    assert r.pump_envelope_status=="outside_supplied_limits"
    assert r.projected_flow_magnitude_m3_s==head.projected_flow_magnitude_m3_s
    assert r.pump_flow_capacity_margin_m3_s<0 and r.hydraulic_friction_power_capacity_margin_W<0
    assert not r.actual_pump_operating_point_or_shaft_power_solved
    assert evaluate(request()).pump_envelope_status=="not_supplied"


def test_same_flow_measurement_does_not_prove_extrapolation():
    r=evaluate(request(frozen_exponent_and_multiplier_over_projected_flow_reviewed=False))
    assert r.status=="computed" and r.projection_status=="needs_data"
    assert r.treated_friction_drop_at_baseline_flow_pa>0 and r.projected_flow_magnitude_m3_s is None
    assert evaluate(request(reviewed_flow_support_m3_s=(.001,.019))).projection_status=="out_of_scope"
    assert evaluate(request(supplied_fractional_drag_reduction=1.)).projection_status=="out_of_scope"


@pytest.mark.parametrize("field",["baseline_friction_drop_pa","measured_state_and_calibration_basis_id"])
def test_missing_calibration(field):
    assert evaluate(request(**{field:None})).status=="needs_data"


@pytest.mark.parametrize("field",["static_head_and_other_losses_separated_reviewed","same_turbulent_state_and_resistance_calibration_reviewed"])
def test_unreviewed_friction_state(field):
    assert evaluate(request(**{field:False})).status=="needs_data"


def test_unknown_projection_data_preserves_valid_observation():
    assert evaluate(request(baseline_volume_flow_magnitude_m3_s=None)).projection_status=="needs_data"
    assert evaluate(request(supplied_baseline_friction_flow_exponent=None)).projection_status=="needs_data"
    assert evaluate(request(supplied_fractional_drag_reduction=None)).status=="needs_data"


@pytest.mark.parametrize("changes",[dict(supplied_fractional_drag_reduction=1.01),dict(supplied_baseline_friction_flow_exponent=0.),
    dict(baseline_friction_drop_pa=float("nan")),dict(observed_treated_friction_drop_pa=10.),dict(reviewed_flow_support_m3_s=(2.,1.))])
def test_invalid_or_dual_calibration_contract(changes):
    with pytest.raises(ValidationError):request(**changes)


def test_underflow_overflow_and_input_replay():
    assert evaluate(request(baseline_friction_drop_pa=1e308,supplied_fractional_drag_reduction=-10.)).status=="out_of_scope"
    assert evaluate(request(baseline_friction_drop_pa=1e300,observed_treated_friction_drop_pa=1e-300,supplied_fractional_drag_reduction=None)).status=="out_of_scope"
    q=request();saved=q.model_dump(mode="python")
    assert evaluate(Request.model_validate_json(q.model_dump_json()))==evaluate(q)
    assert q.model_dump(mode="python")==saved
