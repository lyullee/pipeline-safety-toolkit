"""Individual force ledgers, load symmetry and sliding/contact boundaries."""
import pytest

from pipeline_integrity.seabed_balance import SeabedContactBalance, evaluate_seabed_contact_balance


def request(**updates):
    return SeabedContactBalance(**(dict(submerged_weight_n_m=1000.0,
        lift_upward_n_m=200.0, drag_horizontal_n_m=240.0,
        inertia_horizontal_n_m=-40.0, static_friction_coefficient=0.4,
        passive_lateral_resistance_n_m=30.0) | updates))


def test_independent_vertical_and_horizontal_force_ledgers():
    result = evaluate_seabed_contact_balance(request())
    assert result.status == "computed"
    assert result.values == pytest.approx(dict(effective_normal_load_n_m=800.0,
        total_horizontal_load_n_m=200.0, horizontal_load_magnitude_n_m=200.0,
        friction_capacity_n_m=320.0, total_lateral_resistance_n_m=350.0,
        lateral_margin_n_m=150.0))


def test_axis_reversal_preserves_resistance_and_opposing_forces_cancel():
    a = evaluate_seabed_contact_balance(request()).values
    b = evaluate_seabed_contact_balance(request(drag_horizontal_n_m=-240.0, inertia_horizontal_n_m=40.0)).values
    assert a["lateral_margin_n_m"] == b["lateral_margin_n_m"]
    assert b["total_horizontal_load_n_m"] == -a["total_horizontal_load_n_m"]
    c = evaluate_seabed_contact_balance(request(inertia_horizontal_n_m=-240.0)).values
    assert c["horizontal_load_magnitude_n_m"] == 0
    assert c["lateral_margin_n_m"] == c["total_lateral_resistance_n_m"]


@pytest.mark.parametrize("increment,sign", [(-1.0,1),(0.0,0),(1.0,-1)])
def test_analytic_lift_threshold_brackets_sliding_boundary(increment,sign):
    # At the threshold mu*(weight-lift)+passive equals the signed-resultant magnitude.
    critical_lift = 1000.0 - (200.0-30.0)/0.4
    result = evaluate_seabed_contact_balance(request(lift_upward_n_m=critical_lift+increment)).values
    assert result["lateral_margin_n_m"] == pytest.approx(-0.4*increment, abs=1e-12)
    if sign:
        assert sign*result["lateral_margin_n_m"] > 0


def test_contact_loss_withholds_coulomb_capacity_and_preserves_signed_normal():
    at = evaluate_seabed_contact_balance(request(lift_upward_n_m=1000.0))
    after = evaluate_seabed_contact_balance(request(lift_upward_n_m=1000.01))
    assert at.values["friction_capacity_n_m"] == 0
    assert after.status == "out_of_scope"
    assert after.values["effective_normal_load_n_m"] < 0
    assert "friction_capacity_n_m" not in after.values


def test_common_force_scale_preserves_normalized_margin():
    q = request()
    base = evaluate_seabed_contact_balance(q).values
    scaled = q.model_copy(update={k:v*7 for k,v in q.model_dump().items() if k.endswith("_n_m")})
    new = evaluate_seabed_contact_balance(scaled).values
    for key, value in base.items():
        assert new[key] == pytest.approx(7*value)


@pytest.mark.parametrize("changes", [dict(static_friction_coefficient=-0.1),dict(lift_upward_n_m=float("nan"))])
def test_invalid_coefficients_or_nonfinite_loads_are_rejected(changes):
    with pytest.raises(ValueError):
        request(**changes)
