"""Support, complete exposure, censor/probability meanings and original-receipt binding."""

import copy
import runpy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from pipeline_integrity.adapters import ExternalResult
from pipeline_integrity.contracts import digest
from pipeline_integrity.creep_exchange import (
    REVIEWS,
    CreepBlock,
    CreepRiskMapping,
    CreepSequence,
    creep_risk_probability,
    evaluate_creep,
    evaluate_creep_sequence,
    load_cmsx6_specimens,
)
from pipeline_integrity.material_exchange import MaterialJob, MaterialReceipt


def base():
    return runpy.run_path("examples/creep_methods.py")["make_cases"]()


@pytest.mark.parametrize("method", list(REVIEWS))
def test_missing_reviews_do_not_import_owner(method):
    cases, _, context = base()
    request = cases["creep.norton"].model_copy(update={"method": method, "applicability_reviews": {}})
    result = evaluate_creep(request, context)
    assert result["status"] == "needs_data" and result["native_receipt"] is None


@pytest.mark.parametrize(
    "update,status",
    [
        ({"domain": None}, "needs_data"),
        ({"evaluation_temperature_k": None}, "needs_data"),
        ({"evaluation_temperature_k": 1200.0}, "out_of_scope"),
    ],
)
def test_norton_support_is_explicit_before_owner_import(update, status):
    cases, _, context = base()
    assert evaluate_creep(cases["creep.norton"].model_copy(update=update), context)["status"] == status


@pytest.mark.parametrize(
    "parameter,value", [("stress_Pa", 4e8), ("reference_stress_Pa", 4e8), ("exposure_s", 2e7)]
)
def test_norton_stress_reference_and_time_cannot_extrapolate(parameter, value):
    cases, _, context = base()
    r = cases["creep.norton"]
    assert (
        evaluate_creep(r.model_copy(update={"parameters": r.parameters | {parameter: value}}), context)[
            "status"
        ]
        == "out_of_scope"
    )


def test_arrhenius_reference_rate_units_cannot_be_unknown_or_non_si():
    cases, _, context = base()
    for unit in (None, "mm/year"):
        assert (
            evaluate_creep(
                cases["kinetics.arrhenius"].model_copy(update={"reference_rate_unit": unit}), context
            )["status"]
            == "needs_data"
        )
    r = cases["kinetics.arrhenius"]
    assert (
        evaluate_creep(
            r.model_copy(update={"parameters": r.parameters | {"reference_temperature_K": 200.0}}), context
        )["status"]
        == "out_of_scope"
    )


def test_material_condition_evidence_and_asset_clock_are_not_replaced():
    cases, _, context = base()
    r = cases["creep.norton"]
    wrong_domain = r.domain.model_copy(update={"material_condition_id": "foreign"})
    assert evaluate_creep(r.model_copy(update={"domain": wrong_domain}), context)["status"] == "out_of_scope"
    for update in ({"asset_id": "other"}, {"clock_id": "other"}, {"evidence_ids": ("absent",)}):
        with pytest.raises(ValueError):
            evaluate_creep(r.model_copy(update=update), context)
    fit = cases["creep.lognormal_life_fit"]
    assert (
        evaluate_creep(fit.model_copy(update={"material_condition_id": "other"}), context)["status"]
        == "needs_data"
    )


def test_conditional_prediction_needs_a_model_digest_and_cannot_modify_it():
    cases, _, context = base()
    r = cases["creep.norton"].model_copy(
        update={
            "method": "creep.conditional_life",
            "parameters": {
                "material_condition_id": cases["creep.norton"].material_condition_id,
                "model": {"training_digest": "a" * 64},
            },
            "applicability_reviews": dict.fromkeys(REVIEWS["creep.conditional_life"], True),
        }
    )
    assert evaluate_creep(r, context)["status"] == "needs_data"
    with pytest.raises(ValueError, match="fitted model changed"):
        evaluate_creep(r.model_copy(update={"model_reference_digest": "b" * 64}), context)


def test_exposure_gap_missing_life_and_unknown_calibration_do_not_drop_blocks():
    _, seq, context = base()
    first, last = seq.blocks
    missing = last.model_copy(update={"rupture_life_s": None})
    result = evaluate_creep_sequence(seq.model_copy(update={"blocks": (first, missing)}), context)
    assert result["status"] == "needs_data" and result["damage"] is None
    assert len(result["sequence"]["blocks"]) == 2
    gap = last.model_copy(update={"start_s": last.start_s + 100.0})
    result = evaluate_creep_sequence(seq.model_copy(update={"blocks": (first, gap)}), context)
    assert result["status"] == "needs_data" and result["gaps_s"] == [[100000.0, 100100.0]]
    assert (
        evaluate_creep_sequence(
            seq.model_copy(update={"blocks_life_and_linear_accumulation_reviewed": False}), context
        )["status"]
        == "needs_data"
    )


def test_sequence_no_overlap_reorder_foreign_calibration_or_support_extrapolation():
    _, seq, context = base()
    with pytest.raises(ValidationError, match="overlap"):
        CreepSequence.model_validate_json(
            seq.model_copy(update={"blocks": tuple(reversed(seq.blocks))}).model_dump_json()
        )
    for change in ({"temperature_k": 1100.0}, {"stress_pa": 4e8}):
        other = seq.blocks[-1].model_copy(update=change)
        assert (
            evaluate_creep_sequence(seq.model_copy(update={"blocks": (seq.blocks[0], other)}), context)[
                "status"
            ]
            == "out_of_scope"
        )
    foreign = seq.domain.model_copy(update={"material_condition_id": "other"})
    assert (
        evaluate_creep_sequence(seq.model_copy(update={"domain": foreign}), context)["status"]
        == "out_of_scope"
    )


def test_model_quantile_life_is_explicit_instead_of_an_implicit_median_capacity():
    _, seq, _ = base()
    first = seq.blocks[0]
    with pytest.raises(ValidationError, match="quantile"):
        CreepBlock.model_validate_json(
            first.model_copy(update={"life_basis": "conditional_model_quantile"}).model_dump_json()
        )
    q = first.model_copy(update={"life_basis": "conditional_model_quantile", "life_quantile": 0.5})
    assert CreepBlock.model_validate_json(q.model_dump_json()).life_quantile == 0.5


def test_material_job_is_reused_and_native_scope_is_retained(monkeypatch):
    import pipeline_integrity.creep_exchange as exchange

    cases, _, context = base()
    request = cases["creep.larson_miller"]
    captured = []

    def prepare(method, parameters, ctx, **kwargs):
        captured.append((method, parameters, ctx, kwargs))
        return "native_job"

    def evaluate(job, ctx, **kwargs):
        assert job == "native_job" and ctx == context
        return SimpleNamespace(
            check_integrity=lambda: None,
            native=SimpleNamespace(
                payload={
                    "status": "out_of_scope",
                    "values": {"native_raw": 9.0},
                    "units": {"native_raw": "K"},
                    "probability_semantics": {},
                    "notes": ("Native support decision",),
                }
            ),
            model_dump=lambda **kwargs: {"original": True},
        )

    monkeypatch.setattr(exchange, "prepare_material_job", prepare)
    monkeypatch.setattr(exchange, "evaluate_material_job", evaluate)
    r = evaluate_creep(request, context, client="owner")
    assert r["status"] == "out_of_scope" and r["values"] == {"native_raw": 9.0}
    assert captured[0][0:3] == (request.method, request.parameters, context)
    assert not r["damage_equals_failure_probability"]


def prediction_fixture():
    cases, _, context = base()
    parameters = {"model": {"training_digest": "a" * 64}, "current_age_s": 5.0, "horizon_s": 3.0}
    units, meaning = (
        {"probability_given_survival": "1"},
        {"probability_given_survival": "conditional_first_failure_in_period"},
    )
    values = {"probability_given_survival": 0.2, "median_life_s": None}
    job = MaterialJob(
        model_id="creep.conditional_life",
        model_version="1.0.0",
        asset_id=context.asset_id,
        location_id="span",
        clock_id=context.clock_id,
        parameters=parameters,
        output_units=units,
        probability_semantics=meaning,
        input_schema_digest="b" * 64,
        evidence_ids=("reference",),
    )
    payload = {"values": values, "units": units, "probability_semantics": meaning}
    external = ExternalResult(
        owner="matintegrity",
        owner_schema="matintegrity.assessment.v1",
        status="computed",
        asset_id=context.asset_id,
        payload=payload,
        payload_digest=digest(payload),
    )
    native = MaterialReceipt(
        job=job, context=context, native=external, implementation_sha256="b" * 64, result_digest="pending"
    )
    native = native.model_copy(
        update={"result_digest": digest(native.model_dump(mode="json", exclude={"result_digest"}))}
    )
    body = dict(
        schema="pipeline_integrity.creep_exchange.v1",
        status="computed",
        request=dict(method="creep.conditional_life", parameters=parameters),
        context=context.model_dump(mode="json"),
        values=values,
        probability_semantics=meaning,
        native_receipt=native.model_dump(mode="json"),
    )
    prediction = {**body, "record_digest": digest(body)}
    mapping = CreepRiskMapping(
        expected_prediction_digest=prediction["record_digest"],
        event_id="creep",
        physical_scope_id="one_declared_span",
        condition_id="constant_condition",
        period_start_s=10.0,
        period_finish_s=13.0,
        fresh_material_origin_s=5.0,
        conditioning="Alive at 10s under declared unchanged conditions",
        failure_definition="First creep rupture",
        age_origin_evidence_id="reference",
        evidence_ids=("reference",),
        physical_event_and_age_mapping_reviewed=True,
    )
    return prediction, mapping


def test_risk_mapping_preserves_conditional_period_instead_of_demand_or_frequency():
    prediction, mapping = prediction_fixture()
    result = creep_risk_probability(prediction, mapping)
    assert result["status"] == "computed"
    p = result["probability"]
    assert p["measure"] == "mission_probability" and p["probability"] == 0.2
    assert p["qualification"] == "research" and len(p["dependency_groups"]) == 1
    assert not result["expected_counts_or_demand_probability_inferred"]
    assert prediction["values"]["median_life_s"] is None
    assert not result["loss_of_containment_model_included"]


@pytest.mark.parametrize(
    "change", [{"period_start_s": 11.0}, {"period_finish_s": 14.0}, {"fresh_material_origin_s": 4.0}]
)
def test_age_or_period_mapping_cannot_change_source_conditional_probability(change):
    prediction, mapping = prediction_fixture()
    with pytest.raises(ValueError, match="age do not match"):
        creep_risk_probability(prediction, mapping.model_copy(update=change))


def test_risk_review_changed_hash_and_resealed_wrapper_must_not_override_native_values():
    prediction, mapping = prediction_fixture()
    assert (
        creep_risk_probability(
            prediction, mapping.model_copy(update={"physical_event_and_age_mapping_reviewed": False})
        )["status"]
        == "needs_data"
    )
    changed = copy.deepcopy(prediction)
    changed["values"]["probability_given_survival"] = 0.8
    with pytest.raises(ValueError, match="changed after sealing"):
        creep_risk_probability(changed, mapping)
    changed["record_digest"] = digest({k: v for k, v in changed.items() if k != "record_digest"})
    with pytest.raises(ValueError, match="original material receipt"):
        creep_risk_probability(
            changed, mapping.model_copy(update={"expected_prediction_digest": changed["record_digest"]})
        )


def test_primary_workbook_requires_supplier_checksum_before_owner_import(tmp_path):
    path = tmp_path / "changed.xlsx"
    path.write_bytes(b"not the pinned primary source")
    with pytest.raises(ValueError, match="supplier checksum"):
        load_cmsx6_specimens(path)


@pytest.mark.parametrize("quantile", [0.0, 1.0])
def test_continuous_model_quantile_life_cannot_claim_zero_or_infinite_endpoint(quantile):
    _, sequence, _ = base()
    block = sequence.blocks[0].model_copy(
        update={"life_basis": "conditional_model_quantile", "life_quantile": quantile}
    )
    with pytest.raises(ValidationError):
        CreepBlock.model_validate_json(block.model_dump_json())


def test_damage_quantity_cannot_be_bound_as_a_failure_probability():
    prediction, mapping = prediction_fixture()
    changed = copy.deepcopy(prediction)
    changed["request"]["method"] = "creep.time_fraction"
    changed["record_digest"] = digest({k: v for k, v in changed.items() if k != "record_digest"})
    with pytest.raises(ValueError, match="conditional-life"):
        creep_risk_probability(
            changed, mapping.model_copy(update={"expected_prediction_digest": changed["record_digest"]})
        )
