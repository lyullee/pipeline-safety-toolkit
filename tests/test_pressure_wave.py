"""Distinct feature/arrival/causal meanings and independently constructed timing cases."""

from copy import deepcopy

import pytest

from examples.pressure_wave import make_case, make_moc_case
from pipeline_integrity.acoustics import RelativeTiming
from pipeline_integrity.contracts import digest
from pipeline_integrity.pressure_wave import (
    bind_pressure_wave_pair_request,
    boundary_metadata,
    evaluate_pressure_wave,
    verify_pressure_wave_record,
)


def amend(q, c, **changes):
    q = q.model_copy(update=changes)
    return q, c.model_copy(update={"metadata": {"pressure_wave_boundary": boundary_metadata(q)}})


def laboratory_case(*, status="ready_for_paired_analysis"):
    q, c = make_case(reviewed=True)
    left = q.left.model_copy(update={"origin": "laboratory", "sensor_id": "P1"})
    right = q.right.model_copy(update={"origin": "laboratory", "sensor_id": "P2"})
    body = {
        "schema": "pipeline_integrity.pressure_pair_qualification.v1",
        "status": status,
        "pair_key": "authored-laboratory-pair",
        "sensor_ids": ["P1", "P2"],
        "pressure_reference": q.pressure_reference,
        "source_sha256_by_sensor": {"P1": left.source_digest, "P2": right.source_digest},
        "record_digest_by_sensor": {"P1": "a" * 64, "P2": "b" * 64},
        "pairing_evidence_id": "authored",
        "common_trigger_verified": status == "ready_for_paired_analysis",
        "sample_alignment_verified": status == "ready_for_paired_analysis",
        "clock_ids_match": True,
        "parsed_coordinates_match": True,
        "arrival_ground_truth": None,
        "leak_onset_ground_truth": None,
        "pairing_does_not_establish_units_or_event_truth": True,
    }
    qualification = {**body, "record_digest": digest(body)}
    q = q.model_copy(update={"left": left, "right": right, "source_pair_qualification": qualification})
    c = c.model_copy(
        update={
            "origin": "laboratory",
            "metadata": {"pressure_wave_boundary": boundary_metadata(q)},
        }
    )
    return q, c


def laboratory_trace_record(q):
    body = {
        "schema": "pipeline_integrity.pressure_pair_trace_record.v1",
        "qualification_digest": q.source_pair_qualification["record_digest"],
        "parsed_record_digest_by_sensor": q.source_pair_qualification["record_digest_by_sensor"],
        "asset_id": q.left.asset_id,
        "origin": q.left.origin,
        "time_origin_shift_s": 0.0,
        "time_origin_evidence_id": "authored",
        "maximum_grid_error_s": 0.0,
        "observed_grid_error_s": 0.0,
        "interpolation_or_resampling_performed": False,
        "traces": {
            "P1": q.left.model_dump(mode="json"),
            "P2": q.right.model_dump(mode="json"),
        },
    }
    return {**body, "record_digest": digest(body)}


def test_pair_request_binder_preserves_explicit_sensor_roles_and_context_boundary():
    q, c = laboratory_case()
    record = laboratory_trace_record(q)
    bound, context = bind_pressure_wave_pair_request(
        record,
        q.source_pair_qualification,
        c,
        expected_trace_record_digest=record["record_digest"],
        expected_qualification_digest=q.source_pair_qualification["record_digest"],
        request_id="bound-front",
        left_sensor_id="P2",
        right_sensor_id="P1",
        sensor_role_evidence_id="authored",
        source_event_id="authored-event",
        evidence_id="authored",
        minimum_negative_rate_pa_s=100000.0,
        minimum_rate_prominence_pa_s=100000.0,
    )
    assert bound.left.sensor_id == "P2" and bound.right.sensor_id == "P1"
    assert context.metadata["pressure_wave_sensor_roles"] == {
        "left": "P2",
        "right": "P1",
        "evidence_id": "authored",
    }
    assert context.metadata["pressure_wave_boundary"] == boundary_metadata(bound)


@pytest.mark.parametrize("change", ["trace_digest", "qualification", "roles", "evidence", "clock"])
def test_pair_request_binder_rejects_unpinned_or_unreviewed_boundaries(change):
    q, c = laboratory_case()
    record = laboratory_trace_record(q)
    kwargs = dict(
        expected_trace_record_digest=record["record_digest"],
        expected_qualification_digest=q.source_pair_qualification["record_digest"],
        request_id="bound-front",
        left_sensor_id="P1",
        right_sensor_id="P2",
        sensor_role_evidence_id="authored",
        source_event_id="authored-event",
        evidence_id="authored",
        minimum_negative_rate_pa_s=100000.0,
        minimum_rate_prominence_pa_s=100000.0,
    )
    if change == "trace_digest":
        kwargs["expected_trace_record_digest"] = "0" * 64
    elif change == "qualification":
        kwargs["expected_qualification_digest"] = "0" * 64
    elif change == "roles":
        kwargs["right_sensor_id"] = "P1"
    elif change == "evidence":
        kwargs["sensor_role_evidence_id"] = "missing"
    else:
        c = c.model_copy(update={"clock_id": "different-clock"})
    with pytest.raises(ValueError):
        bind_pressure_wave_pair_request(record, q.source_pair_qualification, c, **kwargs)


@pytest.mark.parametrize(
    "left,right,first,direction",
    [(40, 55, "left", "left_to_right"), (55, 40, "right", "right_to_left"), (40, 40, "indeterminate", None)],
)
def test_independent_sample_support_order(left, right, first, direction):
    q, c = make_case(reviewed=True, left_index=left, right_index=right)
    r = evaluate_pressure_wave(q, c)
    assert r["status"] == "computed_diagnostic"
    assert r["arrival_delay_bounds_s"] == pytest.approx(
        [(right - left - 1) * 0.001, (right - left + 1) * 0.001]
    )
    assert r["first_arrival_receiver"] == first and r["conditional_travel_direction"] == direction
    assert r["leak_detected"] is r["leak_probability"] is r["position_m"] is None
    assert verify_pressure_wave_record(r, request=q, context=c)


def test_default_no_scipy_feature_search(monkeypatch):
    import pipeline_integrity.pressure_wave as m

    monkeypatch.setattr(m, "_feature", lambda *a: pytest.fail("unreviewed feature search"))
    q, c = make_case()
    r = evaluate_pressure_wave(q, c)
    assert r["status"] == "needs_data" and not r["native_called"] and r["features"] is None


def test_laboratory_pair_requires_ready_source_qualification_before_feature_search(monkeypatch):
    q, c = laboratory_case(status="needs_pairing_evidence")
    monkeypatch.setattr(
        "pipeline_integrity.pressure_wave._feature", lambda *a: pytest.fail("unqualified pair searched")
    )
    result = evaluate_pressure_wave(q, c)
    assert result["status"] == "needs_data" and not result["native_called"]
    assert result["issues"] == ["source-pair qualification is not ready for paired analysis"]


def test_ready_laboratory_pair_reaches_existing_feature_engine_without_claiming_leak_cause():
    q, c = laboratory_case()
    result = evaluate_pressure_wave(q, c)
    assert result["status"] == "computed_diagnostic" and result["native_called"]
    assert result["leak_detected"] is result["leak_probability"] is None


@pytest.mark.parametrize("change", ["source", "reference", "evidence", "seal"])
def test_laboratory_pair_qualification_is_bound_to_source_context(change):
    q, c = laboratory_case()
    qualification = deepcopy(q.source_pair_qualification)
    if change == "source":
        qualification["source_sha256_by_sensor"]["P1"] = "f" * 64
    elif change == "reference":
        qualification["pressure_reference"] = "gauge"
    elif change == "evidence":
        qualification["pairing_evidence_id"] = "missing"
    else:
        qualification["record_digest"] = "0" * 64
    if change != "seal":
        qualification["record_digest"] = digest(
            {key: value for key, value in qualification.items() if key != "record_digest"}
        )
    q, c = amend(q, c, source_pair_qualification=qualification)
    with pytest.raises(ValueError):
        evaluate_pressure_wave(q, c)


@pytest.mark.parametrize(
    "change",
    [
        {"minimum_negative_rate_pa_s": 1.0},
        {"minimum_rate_prominence_pa_s": 1.0},
        {"same_event_reviewed": False},
        {"left_feature_to_arrival_error_s": 1.0},
    ],
)
def test_entire_pressure_analysis_policy_is_bound_before_native(change, monkeypatch):
    q, c = make_case(reviewed=True)
    monkeypatch.setattr(
        "pipeline_integrity.pressure_wave._feature", lambda *a: pytest.fail("unbound policy searched")
    )
    with pytest.raises(ValueError, match="bound to context"):
        evaluate_pressure_wave(q.model_copy(update=change), c)


def test_flat_rate_peak_retains_full_plateau_temporal_support():
    q, c = make_case(reviewed=True)
    values = [100000.0] * 128
    for i in range(41, 46):
        values[i] = 100000.0 - (i - 40) * 2000.0
    values[46:] = [90000.0] * (128 - 46)
    q, c = amend(q, c, left=q.left.model_copy(update={"values": tuple(values)}))
    result = evaluate_pressure_wave(q, c)
    feature = result["features"]["left"]["candidates"][0]
    assert feature["left_sample_index"] == 40 and feature["right_sample_index"] == 45
    assert feature["feature_support_s"] == pytest.approx([0.040, 0.045])
    assert feature["plateau_rate_samples"] == 5


@pytest.mark.parametrize(
    "changes",
    [
        {"timing": None},
        {"left_feature_to_arrival_error_s": None},
        {"right_feature_to_arrival_error_s": None},
        {"same_event_reviewed": False},
    ],
)
def test_missing_physical_timing_keeps_features_not_direction(changes):
    q, c = make_case(reviewed=True)
    q, c = amend(q, c, **changes)
    r = evaluate_pressure_wave(q, c)
    assert r["status"] == "needs_data" and r["feature_delay_bounds_s"] is not None
    assert r["arrival_delay_bounds_s"] is r["conditional_travel_direction"] is None


def test_latency_bias_and_model_error_can_reverse_or_withhold_order():
    q, c = make_case(reviewed=True)
    q, c = amend(
        q,
        c,
        timing=RelativeTiming(
            right_minus_left_latency_s=0.03, absolute_error_bound_s=0.0, source_id="authored"
        ),
    )
    r = evaluate_pressure_wave(q, c)
    assert r["first_arrival_receiver"] == "right"
    q, c = amend(q, c, left_feature_to_arrival_error_s=0.02)
    r = evaluate_pressure_wave(q, c)
    assert r["first_arrival_receiver"] == "indeterminate" and r["conditional_travel_direction"] is None


@pytest.mark.parametrize(
    "topology,reviewed", [("source_between", True), ("unresolved", True), ("through_pair", False)]
)
def test_receiver_order_not_travel_direction_without_topology(topology, reviewed):
    q, c = make_case(reviewed=True)
    q, c = amend(q, c, topology=topology, topology_reviewed=reviewed)
    r = evaluate_pressure_wave(q, c)
    assert r["first_arrival_receiver"] == "left" and r["conditional_travel_direction"] is None


@pytest.mark.parametrize("change", ["unknown", "invalid", "flat", "multiple", "endpoint"])
def test_ambiguous_and_missing_edges_are_not_zero_or_selected_by_strength(change):
    q, c = make_case(reviewed=True)
    values = list(q.left.values)
    valid = list(q.left.valid)
    if change == "unknown":
        values[20] = None
        valid[20] = False
    elif change == "invalid":
        valid[20] = False
    elif change == "flat":
        values = [100000.0] * 128
    elif change == "multiple":
        values = [v if i < 80 else v - 20000 for i, v in enumerate(values)]
    else:
        values = [100000.0] + [90000.0] * 127
    q, c = amend(q, c, left=q.left.model_copy(update={"values": tuple(values), "valid": tuple(valid)}))
    r = evaluate_pressure_wave(q, c)
    assert r["status"] == "needs_data" and r["arrival_delay_bounds_s"] is None


@pytest.mark.parametrize("field", ["clock", "unit", "quantity", "origin", "evidence", "boundary", "nan"])
def test_context_units_and_nonfinite_clones_are_invalid(field):
    q, c = make_case(reviewed=True)
    if field == "clock":
        q = q.model_copy(update={"left": q.left.model_copy(update={"clock_id": "wrong"})})
    elif field == "unit":
        q = q.model_copy(update={"left": q.left.model_copy(update={"unit": "kPa"})})
    elif field == "quantity":
        q = q.model_copy(update={"left": q.left.model_copy(update={"quantity_id": "acceleration"})})
    elif field == "origin":
        c = c.model_copy(update={"origin": "measured"})
    elif field == "evidence":
        c = c.model_copy(update={"evidence": ()})
    elif field == "boundary":
        c = c.model_copy(update={"metadata": {}})
    else:
        q = q.model_copy(
            update={"left": q.left.model_copy(update={"values": (float("nan"), *q.left.values[1:])})}
        )
    with pytest.raises(ValueError):
        evaluate_pressure_wave(q, c)


@pytest.mark.parametrize("what", ["bounds", "direction", "cause", "native", "source", "context"])
def test_rehashed_mutations_refused(what):
    q, c = make_case(reviewed=True)
    r = deepcopy(evaluate_pressure_wave(q, c))
    if what == "bounds":
        r["arrival_delay_bounds_s"][0] -= 1.0
    elif what == "direction":
        r["conditional_travel_direction"] = "right_to_left"
    elif what == "cause":
        r["leak_detected"] = True
    elif what == "native":
        r["features"]["left"]["candidates"][0]["left_sample_index"] = 0
    elif what == "source":
        r["implementation_sha256"] = "0" * 64
    else:
        r["context"]["scenario_id"] = "changed"
    r["record_digest"] = digest({k: v for k, v in r.items() if k != "record_digest"})
    with pytest.raises(ValueError):
        verify_pressure_wave_record(r, request=q, context=c)


@pytest.mark.parametrize("step", [0.001, 0.002])
def test_actual_moc_pressure_wave_matches_separate_distance_over_speed(step):
    q, c, native = make_moc_case(timestep_s=step)
    r = evaluate_pressure_wave(q, c)
    assert native["status"] == "computed" and native["all_volume_balances_within_tolerance"]
    assert r["status"] == "computed_diagnostic"
    lower, upper = r["arrival_delay_bounds_s"]
    reference = -(700.0 - 300.0) / 1000.0
    assert lower <= reference <= upper
    assert upper - lower == pytest.approx(2 * native["timestep_s"])
    assert r["conditional_travel_direction"] == "right_to_left" and r["leak_detected"] is None
    assert verify_pressure_wave_record(r, request=q, context=c)


def test_gauge_negative_values_and_nonpositive_absolute_pressure_are_distinct():
    q, c = make_case(reviewed=True)
    traces = [
        t.model_copy(update={"values": tuple(v - 100000.0 for v in t.values)}) for t in (q.left, q.right)
    ]
    q, c = amend(q, c, left=traces[0], right=traces[1])
    assert evaluate_pressure_wave(q, c)["status"] == "out_of_scope"
    q, c = amend(q, c, pressure_reference="gauge")
    assert evaluate_pressure_wave(q, c)["status"] == "computed_diagnostic"
