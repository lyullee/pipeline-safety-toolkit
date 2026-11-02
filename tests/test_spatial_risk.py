"""Native arithmetic references and semantic replay attacks on spatial exposure."""

import json
import os
import runpy
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from pipeline_integrity.contracts import digest
from pipeline_integrity.spatial_risk import (
    REVIEWS,
    JointCasualtyDistribution,
    JointCasualtyState,
    SpatialRiskRequest,
    evaluate_spatial_risk,
    to_signal_frames,
    verify_spatial_risk_record,
)

ROOT = Path(__file__).resolve().parents[1]
CONSEQUENCE_ROOT = Path(os.environ.get('PIPELINE_RETAINED_CONSEQUENCE_ROOT',
    str(ROOT / 'artifacts/consequences')))
MAKE = runpy.run_path(str(ROOT / "examples/spatial_risk.py"))["make_case"]
GUIDE = runpy.run_path(str(ROOT / "scripts/check_spatial_risk.py"))


def seal(record):
    record["result_digest"] = digest({k: v for k, v in record.items() if k != "result_digest"})
    return record


@pytest.fixture(scope="module")
def reference():
    # Optional owner checkouts are used as native implementations, never copied into this package.
    for relative in ("prism/lh2-tno", "human-reliability-toolkit"):
        path = ROOT.parent / "tmp/analysis/repos" / relative / "src"
        if path.is_dir():
            sys.path.insert(0, str(path))
    pytest.importorskip("human_reliability.event_tree")
    pytest.importorskip("prism_qra.risk")
    request, context = MAKE(reviewed=True)
    return request, context, evaluate_spatial_risk(request, context)


def update_condition(request, *, index=0, **changes):
    event = request.events[0]
    conditions = list(event.conditions)
    conditions[index] = conditions[index].model_copy(update=changes)
    return request.model_copy(
        update={"events": (event.model_copy(update={"conditions": tuple(conditions)}),)}
    )


def test_default_review_gate_does_not_discover_optional(monkeypatch):
    monkeypatch.setattr(
        "pipeline_integrity.spatial_risk.native_backend_metadata",
        lambda: pytest.fail("native import attempted"),
    )
    q, c = MAKE()
    record = evaluate_spatial_risk(q, c)
    assert record["status"] == "needs_data" and record["provider"] is None
    assert verify_spatial_risk_record(record, request=q, context=c)["valid"]
    with pytest.raises(ValueError):
        to_signal_frames(record, c)


def test_native_closed_form_reference(reference):
    q, c, record = reference
    assert record["status"] == "computed_diagnostic"
    a, b = record["maps"]
    assert (a["fixed_location_ir_per_year"], b["fixed_location_ir_per_year"]) == pytest.approx((0.01, 0.02))
    assert (a["present_person_ir_per_year"], b["present_person_ir_per_year"]) == pytest.approx(
        (0.00625, 0.0025)
    )
    assert sum(r["expected_fatalities_per_year"] for r in record["maps"]) == pytest.approx(0.015)
    assert record["integer_casualty_fn_curve"]["value"] == {"1": 0.01125, "2": 0.00375, "3": 0.0}
    assert record["expected_death_threshold_curve"]["value"] == {
        "expected_deaths": [1.5],
        "frequency_per_year": [0.005],
    }
    assert verify_spatial_risk_record(record, request=q, context=c)["valid"]
    frames = to_signal_frames(record, c)
    assert frames[0].clock_id == c.clock_id and frames[0].time_s == 10.0
    assert frames[0].units["expected_fatalities_per_year"] == "person/year"
    assert all(all(f.valid.values()) for f in frames)


def test_expected_deaths_cannot_become_integer_fn(reference):
    q, c, _ = reference
    event = q.events[0]
    q = q.model_copy(
        update={
            "events": (
                event.model_copy(
                    update={
                        "conditions": tuple(
                            x.model_copy(update={"joint_casualties": None}) for x in event.conditions
                        )
                    }
                ),
            )
        }
    )
    result = evaluate_spatial_risk(q, c)
    assert result["maps"][0]["expected_fatalities_per_year"] is not None
    assert result["integer_casualty_fn_curve"]["value"] is None
    assert result["expected_death_threshold_curve"]["value"]["expected_deaths"] == [1.5]


@pytest.mark.parametrize("origin", ["measured", "laboratory", "user_declared"])
def test_derived_risk_signal_does_not_become_a_measured_observation(reference, origin):
    q, c = MAKE(reviewed=True, origin=origin)
    record = evaluate_spatial_risk(q, c)
    assert record["context"]["origin"] == origin
    frames = to_signal_frames(record, c)
    assert frames[0].origin == "simulation"
    assert frames[0].stream_id.startswith(f"{c.asset_id}/{c.scenario_id}/")


@pytest.mark.parametrize("field", ["persons", "presence_probability", "receptor_id"])
def test_unknown_exposure_and_unmapped_support_preserved(reference, field):
    q, c, _ = reference
    original = q.events[0].conditions[0]
    population = (original.population[0].model_copy(update={field: None}), original.population[1])
    q = update_condition(q, population=population, joint_casualties=None)
    result = evaluate_spatial_risk(q, c)
    row = result["maps"][0]
    assert result["status"] == "partial" and row["expected_fatalities_per_year"] is None
    assert row["known_expected_fatalities_per_year"] == pytest.approx(0.0075)
    assert row["fixed_location_ir_per_year"] == (None if field == "receptor_id" else 0.01)
    assert result["integer_casualty_fn_curve"]["value"] is None
    with pytest.raises(ValueError):
        to_signal_frames(result, c)
    frames = to_signal_frames(result, c, allow_partial=True)
    assert frames[0].valid["expected_fatalities_per_year"] is False


def test_missing_frequency_never_replaced_by_event_probability(reference):
    q, c, _ = reference
    event = q.events[0].model_copy(update={"frequency": None})
    result = evaluate_spatial_risk(q.model_copy(update={"events": (event,)}), c)
    assert result["status"] == "partial"
    assert all(row["fixed_location_ir_per_year"] is None for row in result["maps"])
    assert result["integer_casualty_fn_curve"]["value"] is None


@pytest.mark.parametrize("kind", ["wrong_mean", "wrong_support", "wrong_field_binding"])
def test_joint_distribution_requires_matching_original_marginals(reference, kind):
    q, c, _ = reference
    condition = q.events[0].conditions[0]
    joint = condition.joint_casualties
    source = deepcopy(joint.source_record)
    states = joint.states
    if kind == "wrong_mean":
        states = (
            JointCasualtyState(fatalities_by_cell={"cell:0:0": 0, "cell:0:1": 0}, probability=0.5),
            JointCasualtyState(fatalities_by_cell={"cell:0:0": 1, "cell:0:1": 1}, probability=0.5),
        )
    elif kind == "wrong_support":
        states = (
            JointCasualtyState(fatalities_by_cell={"cell:0:0": 0, "cell:0:1": 0}, probability=0.5),
            JointCasualtyState(fatalities_by_cell={"cell:0:0": 3, "cell:0:1": 0}, probability=0.5),
        )
    else:
        source["field_source_digest"] = "0" * 64
    source["states"] = [x.model_dump(mode="json") for x in states]
    changed = joint.model_copy(
        update={"states": states, "source_record": source, "source_digest": digest(source)}
    )
    with pytest.raises(ValueError):
        evaluate_spatial_risk(update_condition(q, joint_casualties=changed), c)


def test_correct_total_mean_with_impossible_spatial_marginals_refused(reference):
    q, c, _ = reference
    condition = q.events[0].conditions[0]
    population = tuple(
        p.model_copy(update={"persons": 1, "presence_probability": 1.0}) for p in condition.population
    )
    field_source = deepcopy(condition.field.source_record)
    for row, p in zip(field_source["receptors"], (0.9, 0.1), strict=True):
        row["probability"] = p
    field = condition.field.model_copy(
        update={"source_record": field_source, "source_digest": digest(field_source)}
    )
    states = (
        JointCasualtyState(fatalities_by_cell={"cell:0:0": 0, "cell:0:1": 0}, probability=0.5),
        JointCasualtyState(fatalities_by_cell={"cell:0:0": 1, "cell:0:1": 1}, probability=0.5),
    )
    source = {
        **condition.joint_casualties.source_record,
        "field_source_digest": field.source_digest,
        "population_digest": digest(population),
        "states": [s.model_dump(mode="json") for s in states],
    }
    joint = condition.joint_casualties.model_copy(
        update={"states": states, "source_record": source, "source_digest": digest(source)}
    )
    assert sum(sum(s.fatalities_by_cell.values()) * s.probability for s in states) == 0.9 + 0.1
    with pytest.raises(ValueError, match="per-cell casualty mean"):
        evaluate_spatial_risk(
            update_condition(q, population=population, field=field, joint_casualties=joint), c
        )


def test_same_marginals_different_reviewed_dependence_changes_fn_only(reference):
    q, c, original = reference
    condition = q.events[0].conditions[0]
    states = (
        JointCasualtyState(fatalities_by_cell={"cell:0:0": 0, "cell:0:1": 0}, probability=0.5),
        JointCasualtyState(fatalities_by_cell={"cell:0:0": 2, "cell:0:1": 1}, probability=0.5),
    )
    source = {
        **condition.joint_casualties.source_record,
        "states": [s.model_dump(mode="json") for s in states],
    }
    joint = condition.joint_casualties.model_copy(
        update={"states": states, "source_record": source, "source_digest": digest(source)}
    )
    changed = evaluate_spatial_risk(update_condition(q, joint_casualties=joint), c)
    assert changed["maps"] == original["maps"]
    assert changed["expected_death_threshold_curve"] == original["expected_death_threshold_curve"]
    assert changed["integer_casualty_fn_curve"]["value"] == {"1": 0.01, "2": 0.0025, "3": 0.0025}


@pytest.mark.parametrize("change", ["clock", "coordinates", "meaning", "units", "context", "harm"])
def test_rehashed_field_inputs_keep_original_conditional_semantics(reference, change):
    q, c, _ = reference
    condition = q.events[0].conditions[0]
    field = condition.field
    source = deepcopy(field.source_record)
    if change == "clock":
        source["window_finish_s"] = 30.0
    elif change == "coordinates":
        source["receptors"][0]["position_enu_m"][0] = 10.0
    elif change == "meaning":
        source["conditional_on"] = "already_presence_weighted"
    elif change == "units":
        source["harm_units"] = "year^-1"
    elif change == "context":
        source["context"]["asset_id"] = "another_pipe"
    else:
        source["receptors"][0]["probability"] = -0.1
    changed = field.model_copy(update={"source_record": source, "source_digest": digest(source)})
    with pytest.raises(ValueError):
        evaluate_spatial_risk(update_condition(q, field=changed), c)


@pytest.mark.parametrize(
    "change",
    [
        "map",
        "native_rate",
        "fn",
        "legacy_as_true",
        "geometry",
        "context",
        "status",
        "source_pin",
        "clock",
        "population",
        "meaning",
        "qualification",
    ],
)
def test_rehashed_stored_receipts_cannot_promote_or_relabel(reference, change):
    q, c, original = reference
    record = deepcopy(original)
    if change == "map":
        record["maps"][0]["expected_fatalities_per_year"] *= 2
    elif change == "native_rate":
        record["native_receipts"][0]["event_tree"][0]["annual_frequency_per_year"] *= 2
    elif change == "fn":
        record["integer_casualty_fn_curve"]["value"]["1"] = 0.005
    elif change == "legacy_as_true":
        record["integer_casualty_fn_curve"] = record["expected_death_threshold_curve"]
    elif change == "geometry":
        record["cells"][0]["area_m2"] = 100.0
    elif change == "context":
        record["context"]["asset_id"] = "other"
    elif change == "status":
        record["status"] = "computed"
    elif change == "source_pin":
        record["provider"]["packages"]["human_reliability"]["source_sha256"] = "0" * 64
    elif change == "clock":
        record["request"]["window_start_s"] = 0.0
    elif change == "population":
        record["request"]["events"][0]["conditions"][0]["population"][0]["persons"] = 200
    elif change == "meaning":
        record["semantics"]["fixed_location_ir"] = "annual probability"
    else:
        record["physical_validation_claim"] = True
    seal(record)
    assert not verify_spatial_risk_record(record, request=q, context=c)["valid"]
    assert not verify_spatial_risk_record(record)["valid"]
    with pytest.raises(ValueError):
        to_signal_frames(record, c)


@pytest.mark.parametrize("scope", ["grid", "request", "context", "population"])
def test_nan_model_copy_is_not_serialized_to_unknown(reference, scope):
    q, c, _ = reference
    if scope == "grid":
        q = q.model_copy(update={"grid": q.grid.model_copy(update={"receptor_height_m": float("nan")})})
    elif scope == "request":
        q = q.model_copy(update={"window_start_s": float("nan")})
    elif scope == "context":
        c = c.model_copy(update={"metadata": {"nonfinite": float("nan")}})
    else:
        condition = q.events[0].conditions[0]
        q = update_condition(
            q,
            population=(
                condition.population[0].model_copy(update={"presence_probability": float("nan")}),
                condition.population[1],
            ),
        )
    with pytest.raises(ValueError):
        evaluate_spatial_risk(q, c)


def test_integer_probability_and_count_contracts():
    with pytest.raises(ValueError):
        JointCasualtyState(fatalities_by_cell={"cell": 1.5}, probability=1.0)
    with pytest.raises(ValueError):
        JointCasualtyState(fatalities_by_cell={"cell": True}, probability=1.0)
    with pytest.raises(ValueError):
        JointCasualtyDistribution(
            states=(JointCasualtyState(fatalities_by_cell={"cell": 0}, probability=0.2),),
            dependence_evidence_id="e",
            source_record={"states": [{"fatalities_by_cell": {"cell": 0}, "probability": 0.2}]},
            source_digest=digest({"states": [{"fatalities_by_cell": {"cell": 0}, "probability": 0.2}]}),
        )


def test_work_budget_checked_before_optional_import(reference, monkeypatch):
    q, c, _ = reference
    monkeypatch.setattr(
        "pipeline_integrity.spatial_risk.native_backend_metadata",
        lambda: pytest.fail("native touched beyond budget"),
    )
    result = evaluate_spatial_risk(q.model_copy(update={"maximum_native_rows": 1}), c)
    assert result["status"] == "needs_data" and result["issues"] == ["native_work_budget_exceeded"]


def test_reviews_and_clock_revalidated(reference):
    q, c, _ = reference
    with pytest.raises(ValueError):
        evaluate_spatial_risk(q.model_copy(update={"clock_id": "other"}), c)
    with pytest.raises(ValueError):
        SpatialRiskRequest.model_validate(
            {**q.model_dump(mode="python"), "reviews": dict.fromkeys(REVIEWS, "yes")}
        )


def test_stored_nan_and_wrong_context_refused(reference):
    q, c, record = reference
    bad = deepcopy(record)
    bad["maps"][0]["expected_fatalities_per_year"] = float("nan")
    assert not verify_spatial_risk_record(bad)["valid"]
    with pytest.raises(ValueError):
        to_signal_frames(record, c.model_copy(update={"metadata": {"location": "other"}}))


def test_actual_native_ignition_receipt_cannot_disagree_with_native_request(reference):
    path = CONSEQUENCE_ROOT / 'wind_270.json'
    if not path.is_file():
        pytest.skip("optional retained actual consequence, reproduced by check_consequences.py")
    original = json.loads(path.read_text(encoding="utf-8"))
    data = original["native_result"]["data"]
    data["ignition_branch_probabilities"] = {
        key: 0.9 if key == "direct_fire" else 0.1 if key == "no_ignition" else 0.0
        for key in data["ignition_branch_probabilities"]
    }
    for row in data["receptors"]:
        row["harm_given_observed_release_low_high"] = [
            sum(
                data["ignition_branch_probabilities"][key] * branch[key]
                for key in data["ignition_branch_probabilities"]
            )
            for branch in row["branch_harm_low_high"]
        ]
    original["result_digest"] = digest({k: v for k, v in original.items() if k != "result_digest"})
    make = runpy.run_path(str(ROOT / "examples/spatial_risk.py"))["make_native_case"]
    request, context = make(original, reviewed=True)
    with pytest.raises(ValueError, match="ignition weights differ"):
        evaluate_spatial_risk(request, context)


@pytest.mark.parametrize("attack", GUIDE["NATIVE_ATTACKS"])
def test_actual_original_native_forgeries_are_not_validated_as_new_inputs(reference, attack):
    path = CONSEQUENCE_ROOT / 'wind_270.json'
    if not path.is_file():
        pytest.skip("optional retained actual consequence")
    original = json.loads(path.read_text(encoding="utf-8"))
    forged = GUIDE["native_attack"](original, attack)
    make = runpy.run_path(str(ROOT / "examples/spatial_risk.py"))["make_native_case"]
    q, c = make(forged, reviewed=True)
    with pytest.raises(ValueError):
        evaluate_spatial_risk(q, c)


def test_actual_unadapted_withheld_result_remains_unknown(reference):
    path = CONSEQUENCE_ROOT / 'unadapted.json'
    if not path.is_file():
        pytest.skip("optional retained actual withheld consequence")
    original = json.loads(path.read_text(encoding="utf-8"))
    make = runpy.run_path(str(ROOT / "examples/spatial_risk.py"))["make_native_case"]
    q, c = make(original, reviewed=True)
    result = evaluate_spatial_risk(q, c)
    assert result["status"] == "partial"
    assert result["maps"][0]["fixed_location_ir_per_year"] is None
    assert verify_spatial_risk_record(result, request=q, context=c)["valid"]
    frames = to_signal_frames(result, c, allow_partial=True)
    assert frames[0].values["fixed_location_ir_per_year"] is None
    assert frames[0].valid["fixed_location_ir_per_year"] is False
