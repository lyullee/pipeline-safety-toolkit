"""Owner-contract mutation, pressure readiness and thermal clock tests without copying physical solvers."""

from copy import deepcopy

import pytest

from pipeline_integrity.adapters import ExternalResult
from pipeline_integrity.contracts import EvidenceRef, Pressure, RunContext, digest
from pipeline_integrity.material_exchange import (
    PressureSection,
    evaluate_material_job,
    evaluate_pressure_section,
    prepare_material_job,
    solid_thermal_frames,
    thermal_rod_frames,
)

CTX = RunContext(
    asset_id="pipe",
    scenario_id="authored",
    clock_id="elapsed",
    origin="synthetic",
    evidence=(EvidenceRef(id="source", citation="Authored reference", origin="synthetic"),),
)


class ClientSpy:
    def __init__(self, *, status="computed", mutation=None):
        self.status, self.mutation = status, mutation
        self.calls = []
        self.description = dict(
            model_id="owner.model",
            model_version="1.0.0",
            input_schema={"type": "object"},
            output_units={"value": "K"},
            probability_semantics={},
        )
        self.values = {"value": 300.0}

    def describe(self, model_id):
        return deepcopy(self.description) | {"model_id": model_id}

    def evaluate(self, model_id, parameters, context, *, location_id, input_mode="python"):
        self.calls.append(deepcopy(parameters))
        native_context = dict(
            asset_id=context.asset_id,
            location_id=location_id,
            time_basis=context.clock_id,
            metadata=dict(
                pipeline_context_digest=digest(context),
                pipeline_scenario_id=context.scenario_id,
                pipeline_origin=context.origin,
                pipeline_input_validation_mode=input_mode,
            ),
        )
        body = dict(
            schema_version="matintegrity.assessment.v1",
            model_id=model_id,
            model_version="1.0.0",
            inputs=deepcopy(parameters),
            context=native_context,
            units=self.description["output_units"],
            probability_semantics={},
            values=deepcopy(self.values),
            status=self.status,
        )
        if self.mutation:
            self.mutation(body)
        body["input_digest"] = digest(
            {k: body[k] for k in ("model_id", "model_version", "inputs", "context")}
        )
        return ExternalResult(
            owner="matintegrity",
            owner_schema="matintegrity.assessment.v1",
            status=self.status,
            asset_id=context.asset_id,
            payload=body,
            payload_digest=digest(body),
        )


def job(client, model="owner.model", parameters=None):
    return prepare_material_job(
        model,
        {"value": 1.0} if parameters is None else parameters,
        CTX,
        location_id="section",
        evidence_ids=("source",),
        client=client,
    )


def test_owner_payload_and_native_status_are_preserved_without_acceptance():
    c = ClientSpy(status="needs_data")
    r = evaluate_material_job(job(c), CTX, client=c)
    assert r.native.status == "needs_data" and r.native.payload["values"] == {"value": 300.0}
    assert not r.field_or_design_acceptance
    r.check_integrity()


@pytest.mark.parametrize(
    "field,changed",
    [
        ("model_version", "2"),
        ("output_units", {"value": "Pa"}),
        ("probability_semantics", {"value": "unscoped_probability"}),
        ("input_schema", {"type": "array"}),
    ],
)
def test_live_schema_or_meaning_changes_require_explicit_new_review(field, changed):
    c = ClientSpy()
    j = job(c)
    c.description[field] = changed
    with pytest.raises(ValueError, match="changed"):
        evaluate_material_job(j, CTX, client=c)
    assert not c.calls


@pytest.mark.parametrize(
    "field",
    ["model_id", "version", "units", "probability", "clock", "scenario", "origin", "input", "boolean"],
)
def test_result_cannot_relabel_model_context_or_supplied_inputs(field):
    def change(p):
        if field == "model_id":
            p["model_id"] = "other"
        elif field == "version":
            p["model_version"] = "other"
        elif field == "units":
            p["units"] = {"value": "Pa"}
        elif field == "probability":
            p["probability_semantics"] = {"value": "detection"}
        elif field == "clock":
            p["context"]["time_basis"] = "foreign"
        elif field == "scenario":
            p["context"]["metadata"]["pipeline_scenario_id"] = "foreign"
        elif field == "origin":
            p["context"]["metadata"]["pipeline_origin"] = "measured"
        elif field == "input":
            p["inputs"]["value"] = 99.0
        else:
            p["inputs"]["value"] = True

    c = ClientSpy(mutation=change)
    with pytest.raises(ValueError):
        evaluate_material_job(job(c), CTX, client=c)


def test_source_clock_evidence_and_nested_mutation_are_guarded():
    c = ClientSpy()
    j = job(c)
    with pytest.raises(ValueError):
        evaluate_material_job(j, CTX.model_copy(update={"clock_id": "foreign"}), client=c)
    with pytest.raises(ValueError):
        evaluate_material_job(j.model_copy(update={"evidence_ids": ("absent",)}), CTX, client=c)
    r = evaluate_material_job(j, CTX, client=c)
    r.native.payload["values"]["value"] = 301.0
    with pytest.raises(ValueError, match="payload changed"):
        r.check_integrity()


def section(**changes):
    return PressureSection(
        **(
            dict(
                asset_id="pipe",
                location_id="section",
                clock_id="elapsed",
                time_s=0.0,
                inner_radius_m=0.1,
                outer_radius_m=0.2,
                evaluation_radii_m=(0.1, 0.15, 0.2),
                internal_pressure=None,
                external_pressure=None,
                pressure_loaded_closed_ends=True,
                geometry_evidence_id="source",
                pressure_evidence_id="source",
            )
            | changes
        )
    )


def test_unknown_pressures_never_import_owner_or_become_zero_loads():
    r = evaluate_pressure_section(section(), CTX)
    assert r["status"] == "needs_data" and r["material_records"] == []
    with pytest.raises(ValueError):
        section(internal_pressure=Pressure(value=1.0, unit="Pa", reference="differential"))
    with pytest.raises(ValueError):
        section(evaluation_radii_m=(0.1, 0.1, 0.2))


def test_section_preserves_needs_data_and_withholds_bad_radial_tractions():
    p = Pressure(value=200000.0, unit="Pa", reference="absolute")
    q = Pressure(value=100000.0, unit="Pa", reference="absolute")
    s = section(internal_pressure=p, external_pressure=q)
    c = ClientSpy(status="needs_data")
    assert evaluate_pressure_section(s, CTX, client=c)["status"] == "needs_data"
    c = ClientSpy()
    c.values = {"radial_stress_Pa": 0.0}
    r = evaluate_pressure_section(s, CTX, client=c)
    assert (
        r["status"] == "withheld" and r["boundary_traction_residuals"]["inner_radial_traction_pa"] == 200000.0
    )


def test_computed_thermal_rows_become_simulated_frames_with_the_same_clock():
    c = ClientSpy()
    c.values = {"cell_centers_m": [0.005, 0.015], "temperature_history_K": [[300.0, 300.0], [301.0, 302.0]]}
    r = evaluate_material_job(
        job(c, "thermal.transient_conduction_1d", {"time_s": [0.0, 1.0]}), CTX, client=c
    )
    f = list(solid_thermal_frames(r))
    assert f[-1].clock_id == "elapsed" and f[-1].origin == "simulation"
    assert f[-1].values == {"cell_temperature:0": 301.0, "cell_temperature:1": 302.0}
    c.status = "out_of_scope"
    r = evaluate_material_job(
        job(c, "thermal.transient_conduction_1d", {"time_s": [0.0, 1.0]}), CTX, client=c
    )
    with pytest.raises(ValueError):
        list(solid_thermal_frames(r))


def test_unreviewed_archive_cannot_claim_laboratory_source_identity(tmp_path):
    p = tmp_path / "authored.zip"
    p.write_bytes(b"authored, not a source archive")
    with pytest.raises(ValueError, match="reviewed source archive"):
        thermal_rod_frames(p, "run")
