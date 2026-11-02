"""Deferred job API dispatch/replay/CLI checks; no local test execution."""

import json
from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from pipeline_integrity import adapter_jobs as jobs
from pipeline_integrity.cli import main
from pipeline_integrity.contracts import digest


@pytest.fixture
def authored_job():
    path = Path(__file__).parents[1] / "examples/adapter_job.py"
    spec = spec_from_file_location("authored_job_example", path)
    example = module_from_spec(spec)
    spec.loader.exec_module(example)
    return example.make_job()


def reseal(record):
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    return record


def test_static_list_and_unknown_id_do_not_import_engines(monkeypatch, authored_job):
    def forbidden(*args, **kwargs):
        raise AssertionError("unknown/static job must not import adapter")

    monkeypatch.setattr(jobs.importlib, "import_module", forbidden)
    assert len(jobs.adapter_specs()) == 84
    q = authored_job.model_copy(update={"adapter_id": "os.system"})
    with pytest.raises(ValueError, match="allowlisted"):
        jobs.run_adapter_job(q)


def test_unregistered_provider_rejected_before_native_import(monkeypatch, authored_job):
    def forbidden(*args, **kwargs):
        raise AssertionError("unregistered provider must not import adapter")

    monkeypatch.setattr(jobs.importlib, "import_module", forbidden)
    q = authored_job.model_copy(
        update={"adapter_id": "leak.inverse_localization", "bindings": {"provider": "unregistered"}}
    )
    with pytest.raises(ValueError, match="not registered"):
        jobs.run_adapter_job(q)


def test_unknown_provider_role_or_payload_rejected(authored_job):
    for changes in ({"bindings": {"exec": "some_alias"}}, {"payloads": {"code": {"value": "import os"}}}):
        with pytest.raises(ValueError, match="undeclared"):
            jobs.run_adapter_job(authored_job.model_copy(update=changes))


def test_byte_budget_precedes_adapter_execution(monkeypatch, authored_job):
    def forbidden(*args, **kwargs):
        raise AssertionError("oversized job must not import adapter")

    monkeypatch.setattr(jobs.importlib, "import_module", forbidden)
    with pytest.raises(ValueError, match="byte budget"):
        jobs.run_adapter_job(authored_job, maximum_job_bytes=8)


def test_schema_describes_real_contract_without_evaluation():
    schema = jobs.describe_adapter("construction.route")
    assert schema["parameters_schema"]["title"] == "RoutePlanningRequest"
    assert schema["schema_generation_is_numerical_validation"] is False


def test_withheld_status_and_original_replay_preserved(authored_job):
    record = jobs.run_adapter_job(authored_job)
    assert record["result"]["status"] == "needs_data"
    assert record["engineering_qualification_conferred_by_dispatch"] is False
    assert jobs.verify_adapter_job_record(record, job=authored_job)["valid"]


def test_resealed_result_still_requires_original_adapter_replay(authored_job):
    record = deepcopy(jobs.run_adapter_job(authored_job))
    record["result"]["status"] = "computed_diagnostic"
    record["result"]["record_digest"] = digest(
        {k: v for k, v in record["result"].items() if k != "record_digest"}
    )
    with pytest.raises(ValueError):
        jobs.verify_adapter_job_record(reseal(record))


def test_resealed_identity_cannot_claim_current_source(authored_job):
    record = deepcopy(jobs.run_adapter_job(authored_job))
    record["identity"]["adapter_module_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="identity"):
        jobs.verify_adapter_job_record(reseal(record))


def test_false_dictionary_verifier_is_not_truthy_success(monkeypatch, authored_job):
    record = jobs.run_adapter_job(authored_job)
    module = jobs._module(jobs._spec(authored_job.adapter_id))
    monkeypatch.setattr(module, "verify_route_record", lambda *a, **k: {"valid": False})
    with pytest.raises(ValueError, match="did not validate"):
        jobs.verify_adapter_job_record(record)


def test_cli_dispatch_and_verify_without_provider_loading(tmp_path, capsys, authored_job):
    source = tmp_path / "job.json"
    output = tmp_path / "result.json"
    source.write_text(authored_job.model_dump_json(), encoding="utf-8-sig")
    assert main(["adapter-run", str(source), "--output", str(output)]) == 0
    assert main(["adapter-verify", str(output)]) == 0
    assert json.loads(capsys.readouterr().out)["valid"] is True


def test_cli_budget_rejects_input_before_decode(tmp_path, capsys):
    source = tmp_path / "too-large.json"
    source.write_text("not JSON and over budget")
    assert main(["adapter-run", str(source), "--maximum-input-bytes", "2"]) == 2
    assert json.loads(capsys.readouterr().err)["error_type"] == "ValueError"


def test_cli_static_adapter_list_does_not_build_numeric_registry(monkeypatch, capsys):
    import pipeline_integrity.cli as cli

    def forbidden():
        raise AssertionError("static adapter listing must not construct a numerical registry")

    monkeypatch.setattr(cli, "create_registry", forbidden)
    assert cli.main(["adapters"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == len(jobs.adapter_specs())


def test_job_inherits_finite_serializer_without_validator_name_collision(authored_job):
    document = authored_job.model_dump_json()
    restored = jobs.AdapterJob.model_validate_json(document)
    assert restored == authored_job
    invalid = json.loads(document)
    invalid["parameters"]["bad"] = float("nan")
    with pytest.raises(ValueError):
        jobs.AdapterJob.model_validate_json(json.dumps(invalid))


@pytest.mark.parametrize(
    "document",
    [
        '{"adapter_id":"construction.route","adapter_id":"os.system"}',
        '{"context":{"asset_id":"one","asset_id":"two"}}',
        '{"parameters":{"x":NaN}}',
        '{"parameters":{"x":1e9999}}',
    ],
)
def test_ambiguous_or_nonfinite_raw_json_never_imports_adapter(monkeypatch, document):
    monkeypatch.setattr(jobs, "_module", lambda *a: pytest.fail("invalid raw JSON imported an adapter"))
    with pytest.raises(ValueError):
        jobs.run_adapter_job(document)


def test_resealed_unknown_outer_approval_field_is_not_validated(authored_job, monkeypatch):
    record = deepcopy(jobs.run_adapter_job(authored_job))
    record["approval_granted"] = True
    monkeypatch.setattr(jobs, "_module", lambda *a: pytest.fail("unknown outer schema imported an adapter"))
    with pytest.raises(ValueError, match="schema"):
        jobs.verify_adapter_job_record(reseal(record))


def test_unicode_job_has_same_budget_in_model_and_dictionary_forms(authored_job):
    context = authored_job.context.model_copy(
        update={"metadata": {**authored_job.context.metadata, "format_example": 1e-6}}
    )
    q = authored_job.model_copy(update={"id": "한글-작업" * 100, "context": context})
    limit = len(
        json.dumps(q.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    )
    record = jobs.run_adapter_job(q, maximum_job_bytes=limit)
    assert jobs.verify_adapter_job_record(record, maximum_job_bytes=limit)["valid"] is True
    assert jobs._job(q.model_dump(mode="json"), limit) == q


def test_source_identity_is_checked_on_both_sides_of_dispatch(authored_job, monkeypatch):
    original = jobs._identity
    calls = []

    def changed(*args):
        value = original(*args)
        calls.append(1)
        value["fixture_source_generation"] = len(calls)
        return value

    monkeypatch.setattr(jobs, "_identity", changed)
    with pytest.raises(ValueError, match="changed during evaluation"):
        jobs.run_adapter_job(authored_job)


def test_source_identity_change_during_original_verify_is_rejected(authored_job, monkeypatch):
    record = jobs.run_adapter_job(authored_job)
    original = jobs._identity
    calls = []

    def changed(*args):
        value = original(*args)
        calls.append(1)
        if len(calls) >= 2:
            value["adapter_module_sha256"] = "f" * 64
        return value

    monkeypatch.setattr(jobs, "_identity", changed)
    with pytest.raises(ValueError, match="changed during verification"):
        jobs.verify_adapter_job_record(record)


def test_original_verifier_telemetry_is_retained_without_blanket_equality(authored_job, monkeypatch):
    record = jobs.run_adapter_job(authored_job)
    module = jobs._module(jobs._spec(authored_job.adapter_id))
    checked = {"valid": True, "elapsed_seconds": 0.123, "original_policy": "native telemetry retained"}
    monkeypatch.setattr(module, "verify_route_record", lambda *a, **kw: checked)
    assert jobs.verify_adapter_job_record(record)["adapter_verification"] == checked


def test_real_typed_arrest_payload_is_retained_even_when_not_consumed():
    path = Path(__file__).parents[1] / "examples/fracture_arrest.py"
    spec = spec_from_file_location("authored_arrest_for_job", path)
    example = module_from_spec(spec)
    spec.loader.exec_module(example)
    request, context, trajectory = example.make_case()
    q = jobs.AdapterJob(
        id="authored_typed_payload",
        adapter_id="structural.fracture_arrest",
        parameters=request.model_dump(mode="json"),
        context=context,
        payloads={"trajectory": trajectory.model_dump(mode="json")},
    )
    record = jobs.run_adapter_job(q)
    assert record["result"]["status"] == "needs_data" and record["result"]["trajectory"] is None
    assert record["job"]["payloads"]["trajectory"] == trajectory.model_dump(mode="json")
    assert jobs.verify_adapter_job_record(record, job=q)["valid"] is True


def test_cli_verify_rejects_duplicate_keys_before_adapter_import(tmp_path, monkeypatch, capsys):
    source = tmp_path / "duplicate.json"
    source.write_text('{"schema":"one","schema":"two"}')
    monkeypatch.setattr(jobs, "_module", lambda *a: pytest.fail("duplicate JSON imported an adapter"))
    assert main(["adapter-verify", str(source)]) == 2
    assert "duplicate JSON" in json.loads(capsys.readouterr().err)["error"]


@pytest.mark.parametrize(
    "evaluate,verify",
    [
        (
            "def evaluate(*, request, context): pass",
            "def verify(record, *, request=None, context=None): pass",
        ),
        (
            "def evaluate(request, context, *, additional): pass",
            "def verify(record, *, request=None, context=None): pass",
        ),
        (
            "async def evaluate(request, context): pass",
            "def verify(record, *, request=None, context=None): pass",
        ),
        ("def evaluate(request, context): pass", "def verify(request, context=None): pass"),
    ],
)
def test_static_exporter_rejects_dispatch_incompatible_signatures(tmp_path, monkeypatch, evaluate, verify):
    path = Path(__file__).parents[1] / "scripts/export_adapter_jobs.py"
    spec = spec_from_file_location("authored_export_review", path)
    exporter = module_from_spec(spec)
    spec.loader.exec_module(exporter)
    package = tmp_path / "src/pipeline_integrity"
    (package / "catalog_data").mkdir(parents=True)
    (package / "catalog_data/capabilities.json").write_text(
        json.dumps({"capabilities": [{"id": "construction.route"}]})
    )
    (package / "adapter_jobs.py").write_text(
        'SPECS = (AdapterSpec("construction.route", "authored", "Inputs", "evaluate", "verify"),)'
    )
    (package / "authored.py").write_text("class Inputs: pass\n" + evaluate + "\n" + verify + "\n")
    monkeypatch.setattr(exporter, "ROOT", tmp_path)
    with pytest.raises(ValueError):
        exporter.main()
