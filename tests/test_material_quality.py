"""Review gates, declaration identity, bounded work and original owner receipts."""

import runpy
from copy import deepcopy

import pytest

import pipeline_integrity.material_quality as quality
from pipeline_integrity.adapters import ExternalResult
from pipeline_integrity.contracts import digest
from pipeline_integrity.material_exchange import prepare_material_job


def cases():
    return runpy.run_path("examples/material_quality.py")["make_cases"]()


class OwnerSpy:
    def __init__(self):
        self.calls = []
        self.version = "spy-v1"

    def describe(self, method):
        return dict(
            model_id=method,
            model_version=self.version,
            input_schema={"type": "object"},
            output_units={"owner_value": "1"},
            probability_semantics={},
        )

    def evaluate(self, method, parameters, context, *, location_id, input_mode):
        self.calls.append((method, deepcopy(parameters), input_mode))
        values = dict(owner_value=123.0)
        if method == quality.IMPACT:
            values.update(
                target_energy_crossing_temperature_K=300.0,
                observed_temperature_domain_K=[250.0, 350.0],
                shelf_coverage_satisfied=True,
                numerical_parameter_bound_reached=False,
            )
        payload = dict(
            schema_version="matintegrity.assessment.v1",
            model_id=method,
            model_version=self.version,
            inputs=deepcopy(parameters),
            context=dict(
                asset_id=context.asset_id,
                location_id=location_id,
                time_basis=context.clock_id,
                metadata=dict(
                    pipeline_context_digest=digest(context),
                    pipeline_scenario_id=context.scenario_id,
                    pipeline_origin=context.origin,
                    pipeline_input_validation_mode=input_mode,
                ),
            ),
            units={"owner_value": "1"},
            probability_semantics={},
            values=values,
            status="computed",
            notes=[],
        )
        payload["input_digest"] = digest(
            {key: payload[key] for key in ("model_id", "model_version", "inputs", "context")}
        )
        return ExternalResult(
            owner="matintegrity",
            owner_schema="matintegrity.assessment.v1",
            status="computed",
            asset_id=context.asset_id,
            payload=payload,
            payload_digest=digest(payload),
        )


@pytest.mark.parametrize("method", list(quality.REVIEWS))
def test_owner_delegation_retains_parameters_and_original_receipt(method):
    requests, context = cases()
    request, spy = requests[method], OwnerSpy()
    result = quality.evaluate_material_quality(request, context, client=spy)
    assert result["status"] == "computed" and result["values"]["owner_value"] == 123.0
    assert spy.calls == [(method, request.parameters, "json")]
    quality.verify_material_quality_record(result)


@pytest.mark.parametrize("method", list(quality.REVIEWS))
@pytest.mark.parametrize(
    "change", [{"parameters": None}, {"applicability_reviews": {}}, {"review_evidence_ids": {}}]
)
def test_missing_conditions_stop_before_native_guard_or_import(monkeypatch, method, change):
    requests, context = cases()

    def unexpected(*_, **__):
        raise AssertionError("unresolved request reached native owner")

    monkeypatch.setattr(quality, "material_source_guard", unexpected)
    result = quality.evaluate_material_quality(requests[method].model_copy(update=change), context)
    assert result["status"] == "needs_data" and result["native_receipt"] is None
    quality.verify_material_quality_record(result)


def test_same_assay_identity_is_required_for_composition_index():
    requests, context = cases()
    request, spy = requests[quality.PITTING], OwnerSpy()
    for mapping in ({}, {"Cr": "authored-assay", "Mo": "other-assay", "N": "authored-assay"}):
        result = quality.evaluate_material_quality(
            request.model_copy(update={"element_assay_ids": mapping}), context, client=spy
        )
        assert result["status"] == "needs_data" and not spy.calls


def test_numeric_zero_missing_false_and_nan_do_not_collapse():
    requests, context = cases()
    request, spy = requests[quality.PITTING], OwnerSpy()
    zero = request.model_copy(update={"parameters": request.parameters | {"nitrogen_mass_percent": 0.0}})
    quality.evaluate_material_quality(zero, context, client=spy)
    assert spy.calls[-1][1]["nitrogen_mass_percent"] == 0.0
    missing = request.model_copy(update={"parameters": request.parameters | {"nitrogen_mass_percent": None}})
    assert quality.evaluate_material_quality(missing, context)["status"] == "needs_data"
    for value in (False, float("nan"), float("inf")):
        with pytest.raises(ValueError):
            quality.evaluate_material_quality(
                request.model_copy(
                    update={"parameters": request.parameters | {"nitrogen_mass_percent": value}}
                ),
                context,
            )


def test_native_chemistry_unknowns_and_false_policy_are_preserved():
    requests, context = cases()
    request = requests[quality.CHEMISTRY]
    p = deepcopy(request.parameters)
    p["verification"]["element_intervals"]["Ni"] = None
    p["verification"]["heat_id"] = None
    p["require_matching_heat_ids"] = False
    spy = OwnerSpy()
    quality.evaluate_material_quality(request.model_copy(update={"parameters": p}), context, client=spy)
    assert spy.calls[0][1]["verification"]["element_intervals"]["Ni"] is None
    assert spy.calls[0][1]["require_matching_heat_ids"] is False


@pytest.mark.parametrize("change", ["physical_scope", "embedded_evidence", "policy_bool"])
def test_chemistry_scope_evidence_and_policy_are_exact(change):
    requests, context = cases()
    request = requests[quality.CHEMISTRY]
    p = deepcopy(request.parameters)
    if change == "physical_scope":
        p["verification"]["physical_scope_id"] = "different-sheet"
    elif change == "embedded_evidence":
        p["verification"]["evidence"][0]["citation"] = "unrelated"
    else:
        p["require_matching_heat_ids"] = 0
    with pytest.raises(ValueError):
        quality.evaluate_material_quality(request.model_copy(update={"parameters": p}), context)


def test_impact_censoring_and_observed_support_are_not_inferred():
    requests, context = cases()
    request = requests[quality.IMPACT]
    for update in (
        {"impact_observations_complete": False},
        {"impact_observations_complete": None},
        {"declared_temperature_support_K": None},
    ):
        result = quality.evaluate_material_quality(request.model_copy(update=update), context)
        assert result["status"] == "needs_data" and result["native_receipt"] is None
    result = quality.evaluate_material_quality(
        request.model_copy(update={"declared_temperature_support_K": (260.0, 340.0)}), context
    )
    assert result["status"] == "out_of_scope" and result["native_receipt"] is None


@pytest.mark.parametrize(
    "update",
    [
        {"composition_basis": "mass_fraction"},
        {"input_units": {"N": "%"}},
        {"asset_id": "other"},
        {"clock_id": "other"},
        {"evidence_ids": ("absent",)},
    ],
)
def test_units_basis_context_and_evidence_must_match(update):
    requests, context = cases()
    with pytest.raises(ValueError):
        quality.evaluate_material_quality(requests[quality.PITTING].model_copy(update=update), context)


@pytest.mark.parametrize("budget", ["observations", "elements", "nodes"])
def test_work_budget_precedes_serialization(monkeypatch, budget):
    requests, context = cases()
    request = requests[quality.IMPACT if budget == "observations" else quality.CHEMISTRY]
    p = deepcopy(request.parameters)
    update = {}
    if budget == "observations":
        update["maximum_observations"] = 8
    elif budget == "elements":
        p["certificate"]["element_intervals"] = {f"Element{i}": None for i in range(119)}
    else:
        update["maximum_payload_nodes"] = 100
        p["excess"] = [0] * 101
    request = request.model_copy(update={"parameters": p, **update})
    monkeypatch.setattr(
        quality, "json_value", lambda _: (_ for _ in ()).throw(AssertionError("serialized too soon"))
    )
    with pytest.raises(ValueError, match="budget"):
        quality.evaluate_material_quality(request, context)


def test_pinned_jobs_bind_original_parameters_and_model_version():
    requests, context = cases()
    request, spy = requests[quality.PITTING], OwnerSpy()
    job = prepare_material_job(
        request.method,
        request.parameters,
        context,
        location_id=request.location_id,
        evidence_ids=request.evidence_ids,
        client=spy,
    )
    assert (
        quality.evaluate_material_quality(request, context, pinned_job=job, client=spy)["status"]
        == "computed"
    )
    with pytest.raises(ValueError, match="differs"):
        quality.evaluate_material_quality(
            request.model_copy(update={"parameters": request.parameters | {"nitrogen_mass_percent": 0.2}}),
            context,
            pinned_job=job,
            client=spy,
        )
    spy.version = "changed"
    with pytest.raises(ValueError):
        quality.evaluate_material_quality(request, context, pinned_job=job, client=spy)


@pytest.mark.parametrize("mutation", ["values", "context", "request", "meaning", "source_pin"])
def test_resealed_record_rejects_transplants_and_relabelling(mutation):
    requests, context = cases()
    record = quality.evaluate_material_quality(requests[quality.PITTING], context, client=OwnerSpy())
    if mutation == "values":
        record["values"]["owner_value"] = 999.0
    elif mutation == "context":
        record["context"]["scenario_id"] = "different"
    elif mutation == "request":
        record["request"]["parameters"]["nitrogen_mass_percent"] = 0.2
    elif mutation == "meaning":
        record["composition_index_is_corrosion_rate"] = True
    else:
        record["request"]["owner_source_tree_sha256"] = "0" * 64
    record["record_digest"] = digest({key: value for key, value in record.items() if key != "record_digest"})
    with pytest.raises(ValueError):
        quality.verify_material_quality_record(record)


def test_resealed_withheld_record_cannot_claim_computed_values():
    requests, context = cases()
    record = quality.evaluate_material_quality(
        requests[quality.PITTING].model_copy(update={"parameters": None}), context
    )
    record["status"] = "computed"
    record["record_digest"] = digest({key: value for key, value in record.items() if key != "record_digest"})
    with pytest.raises(ValueError):
        quality.verify_material_quality_record(record)
