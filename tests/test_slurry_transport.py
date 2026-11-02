"""Analytic and contract scenarios authored for deferred NURION execution."""

import importlib.util
import math
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline_integrity import slurry_transport as st

_spec = importlib.util.spec_from_file_location(
    "slurry_example", Path(__file__).parents[1] / "examples" / "slurry_transport.py"
)
_example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_example)


def computed(**changes):
    q, c = _example.make_case(reviewed=True, **changes)
    r = st.evaluate_slurry_transport(q, c)
    assert r["status"] == "computed_diagnostic", r["notes"]
    return q, c, r


def test_unreviewed_no_native_call(monkeypatch):
    q, c = _example.make_case()
    monkeypatch.setattr(st, "native_backend_metadata", lambda: pytest.fail("unreviewed native access"))
    r = st.evaluate_slurry_transport(q, c)
    assert r["status"] == "needs_data" and not r["native_called"]
    assert st.verify_slurry_transport_record(r, request=q, context=c)["valid"]


def test_context_and_nonfinite_copy_rejected():
    q, c = _example.make_case()
    with pytest.raises(ValueError):
        st.evaluate_slurry_transport(q, c.model_copy(update={"clock_id": "other"}))
    with pytest.raises(ValueError):
        st.evaluate_slurry_transport(q.model_copy(update={"volume_flow_m3_s": float("nan")}), c)


def test_constitutive_labels_are_not_silent_aliases():
    q, _ = _example.make_case()
    with pytest.raises(ValidationError):
        st.SlurryPropertySnapshot.model_validate({**q.properties.model_dump(), "rheology": "newtonian"})


def test_bingham_matches_independent_buckingham_reiner_flow():
    q, c, r = computed()
    tau = r["values"]["wall_shear_stress_Pa"]
    a = q.properties.yield_stress_Pa / tau
    radius = q.inner_diameter_m / 2
    analytic = math.pi * radius**3 * tau / (4 * q.properties.consistency_Pa_s_n) * (1 - 4 * a / 3 + a**4 / 3)
    assert analytic == pytest.approx(q.volume_flow_m3_s, rel=1e-7, abs=1e-10)
    assert st.verify_slurry_transport_record(r, request=q, context=c)["valid"]


def test_power_law_matches_analytic_pressure():
    q, _ = _example.make_case()
    p = q.properties.model_copy(update={"rheology": "power_law", "flow_index": 0.6, "yield_stress_Pa": 0.0})
    q, _, r = computed(properties=p)
    radius = q.inner_diameter_m / 2
    n = p.flow_index
    expected_tau = (
        p.consistency_Pa_s_n * (abs(q.volume_flow_m3_s) * (3 * n + 1) / (math.pi * radius**3 * n)) ** n
    )
    assert r["values"]["wall_shear_stress_Pa"] == pytest.approx(expected_tau, rel=1e-7)


def test_newtonian_owner_limit_matches_hagen_poiseuille():
    q, _ = _example.make_case()
    p = q.properties.model_copy(
        update={"rheology": "newtonian", "yield_stress_Pa": 0.0, "flow_regime": "newtonian_owner"}
    )
    q, _, r = computed(properties=p)
    expected = (
        128 * p.consistency_Pa_s_n * q.length_m * q.volume_flow_m3_s / (math.pi * q.inner_diameter_m**4)
    )
    assert r["values"]["signed_friction_pressure_drop_Pa"] == pytest.approx(expected, rel=1e-9)
    assert r["native_newtonian"] is not None


def test_reverse_flow_and_gravity_pressure_work():
    q, _, r = computed(volume_flow_m3_s=-0.0001, outlet_minus_inlet_elevation_m=1.0)
    v = r["values"]
    assert v["signed_friction_pressure_drop_Pa"] < 0
    assert v["potential_energy_rate_W"] < 0
    assert v["dissipated_power_W"] > 0
    assert abs(v["pressure_work_balance_residual_W"]) <= v["pressure_work_balance_limit_W"]
    assert v["solid_volume_flow_m3_s"] + v["carrier_volume_flow_m3_s"] == pytest.approx(q.volume_flow_m3_s)


def test_zero_flow_yield_interval_is_not_unique_pressure():
    q, _, r = computed(volume_flow_m3_s=0.0)
    v = r["values"]
    threshold = 4 * q.length_m * q.properties.yield_stress_Pa / q.inner_diameter_m
    assert v["zero_flow_friction_pressure_support_Pa"] == [-threshold, threshold]
    assert v["minimal_zero_flow_pressure_branch"] and v["dissipated_power_W"] == 0
    assert v["outlet_pressure_pa_abs"] is None
    assert v["required_inlet_minus_outlet_Pa"] is None
    assert v["pressure_prediction_unique"] is False


def test_zero_flow_yield_interval_retains_positive_branch_when_zero_shear_reference_is_negative():
    q, _ = _example.make_case()
    density = q.properties.mixture_density_kg_m3
    # Select hydrostatic rise just beyond Pin, while the supported yield
    # pressure span still permits a positive outlet-pressure branch.
    threshold = 4 * q.length_m * q.properties.yield_stress_Pa / q.inner_diameter_m
    rise = (q.inlet_pressure_pa_abs + threshold / 2) / (density * q.gravity_m_s2)
    _, _, r = computed(volume_flow_m3_s=0, outlet_minus_inlet_elevation_m=rise)
    values = r["values"]
    assert values["outlet_pressure_pa_abs"] is None
    assert values["minimal_branch_reference_outlet_pressure_pa_abs"] is None
    assert values["minimal_branch_formal_outlet_pressure_Pa"] < 0
    assert values["zero_flow_formal_outlet_pressure_interval_Pa"][1] > 0
    assert values["zero_flow_positive_absolute_pressure_branch_exists"]


@pytest.mark.parametrize(
    "changes",
    [
        {"settling_condition": "unknown"},
        {"homogeneous_condition": "unsupported"},
        {"solid_volume_fraction": 0.5},
        {"maximum_shear_rate_s_inv": 0.001},
        {"mixture_density_kg_m3": 1200.0},
    ],
)
def test_supplied_support_limits_do_not_become_guessed_correlations(changes):
    q, _ = _example.make_case()
    q, c = _example.make_case(reviewed=True, properties=q.properties.model_copy(update=changes))
    r = st.evaluate_slurry_transport(q, c)
    assert r["status"] == "out_of_scope" and r["values"] is None


def test_roughness_and_fittings_have_no_unqualified_hb_model():
    q, c = _example.make_case(reviewed=True, minor_loss_coefficient=1.0)
    r = st.evaluate_slurry_transport(q, c)
    assert r["status"] == "out_of_scope"


def test_record_and_source_tampering():
    q, c = _example.make_case()
    r = st.evaluate_slurry_transport(q, c)
    r["physical_validation_claim"] = True
    assert not st.verify_slurry_transport_record(r)["valid"]
    q, c = _example.make_case(reviewed=True, owner_pins={"wrong": True})
    with pytest.raises(ValueError):
        st.evaluate_slurry_transport(q, c)
