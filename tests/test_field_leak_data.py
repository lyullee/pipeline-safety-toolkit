"""Authored workbook/clock/label scenarios, pending Nurion execution."""

import hashlib
from copy import deepcopy
from datetime import date, datetime, timedelta

import pytest
from openpyxl import Workbook

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.datasets import catalog
from pipeline_integrity.field_leak_data import (
    HEADERS,
    FieldLeakMapping,
    bind_field_leak_source,
    field_leak_boundary,
    read_field_leak_workbook,
    to_field_monitoring_split,
    verify_field_binding,
    verify_field_leak_record,
)


@pytest.fixture
def source(tmp_path):
    path = tmp_path / "Q_P_80_Leak1.xlsx"
    book = Workbook()
    sheet = book.active
    sheet.title = "Controlled leak 1"
    sheet.append(HEADERS)
    for index in range(80):
        stamp = datetime(2026, 1, 1, 8) + timedelta(seconds=30 * index)
        sheet.append(
            [
                date(2026, 1, 1),
                stamp.time(),
                None if index == 4 else 40.0,
                0.001,
                1 if 20 <= index < 40 else 0,
            ]
        )
    book.save(path)
    book.close()
    raw = path.read_bytes()
    official = next(s for s in catalog() if s.id == "andean_controlled_leaks")
    asset = official.assets[0].model_copy(
        update={
            "filename": path.name,
            "url": "https://example.invalid/authored.xlsx",
            "bytes": len(raw),
            "checksum": "sha256:" + hashlib.sha256(raw).hexdigest(),
        }
    )
    spec = official.model_copy(
        update={
            "assets": (asset,),
            "origin": "simulation",
            "citation": "Authored fixture; no physical measurements",
            "creators": ("Fixture author",),
            "version": "authored-fixture",
            "source": "https://example.invalid/fixture",
        }
    )
    record = read_field_leak_workbook(path, source_spec=spec)
    q = FieldLeakMapping(
        id="mapping",
        source_record_digest=record["record_digest"],
        asset_id="authored_pipe",
        stream_id="source_stream",
        clock_id="relative_clock",
        site_id="authored_site",
        head_sensor_id="head_sensor",
        flow_sensor_id="flow_sensor",
        head_port_id="head_port",
        flow_port_id="flow_port",
        source_evidence_id="source",
        mapping_evidence_id="mapping",
    )
    c = RunContext(
        asset_id=q.asset_id,
        scenario_id="authored_source",
        clock_id=q.clock_id,
        origin="simulation",
        evidence=(
            EvidenceRef(
                id="source",
                citation="Authored workbook fixture",
                origin="simulation",
                sha256=hashlib.sha256(raw).hexdigest(),
            ),
            EvidenceRef(
                id="mapping", citation="Authored mapping, no field calibration", origin="user_declared"
            ),
        ),
        metadata={"field_leak_boundary": field_leak_boundary(q)},
    )
    return tmp_path, spec, record, q, c


def reviewed(q, c):
    q = q.model_copy(
        update={
            "head_definition_id": "authored_same_head_datum",
            "flow_definition_id": "signed_volumetric_flow",
            "temporal_support": "trailing_interval_mean",
            "averaging_width_s": 30.0,
            "report_to_available_offset_s": 3.0,
            "clock_error_bound_s": 2.0,
            "source_clock_mapping_reviewed": True,
            "quantity_sensor_and_temporal_basis_reviewed": True,
        }
    )
    c = c.model_copy(update={"metadata": {"field_leak_boundary": field_leak_boundary(q)}})
    return q, c


def test_reader_retains_head_clock_missingness_and_condition(source):
    root, spec, record, _, _ = source
    assert len(record["rows"]) == 80
    assert record["rows"][1]["elapsed_s"] == 30
    assert record["rows"][4]["reported_pressure_m"] is None
    assert record["rows"][20]["declared_controlled_leak_indicator"] == 1
    assert record["utc_assigned"] is False and record["pressure_Pa"] is None
    assert verify_field_leak_record(record, source_root=root, source_spec=spec)


def test_unknown_mapping_holds_frames(source):
    root, spec, record, q, c = source
    result = bind_field_leak_source(record, q, c, source_root=root, source_spec=spec)
    assert result["status"] == "needs_data" and result["frames"] == []


def test_availability_origin_and_labels_preserved(source):
    root, spec, record, q, c = source
    q, c = reviewed(q, c)
    result = bind_field_leak_source(record, q, c, source_root=root, source_spec=spec)
    assert result["frames"][0]["time_s"] == 5
    assert result["frames"][0]["origin"] == "simulation"
    assert result["sidecar"][0]["source_relative_exposure_s"] == [-30, 0]
    assert set(result["frames"][0]["values"]) == {"pressure_head", "volume_flow"}
    assert result["frames"][4]["valid"]["pressure_head"] is False
    assert result["frames"][4]["values"]["pressure_head"] is None
    assert verify_field_binding(result, source_root=root, source_spec=spec)


def test_explicit_original_rows_only_in_monitoring(source):
    root, spec, record, q, c = source
    q, c = reviewed(q, c)
    result = bind_field_leak_source(record, q, c, source_root=root, source_spec=spec)
    ids = tuple(r["observation_id"] for r in record["rows"][:3])
    split = to_field_monitoring_split(
        result,
        source_root=root,
        role="evaluation",
        split_id="evaluation",
        physical_run_id="authored_run",
        source_group_id="authored_campaign",
        observation_ids=ids,
        source_spec=spec,
    )
    assert split.observation_ids == ids and split.reference_kind == "unresolved"
    with pytest.raises(ValueError, match="source order"):
        to_field_monitoring_split(
            result,
            source_root=root,
            role="evaluation",
            split_id="evaluation",
            physical_run_id="authored_run",
            source_group_id="authored_campaign",
            observation_ids=ids[::-1],
            source_spec=spec,
        )


def test_source_relabeling_and_stale_mapping_rejected(source):
    root, spec, record, q, c = source
    with pytest.raises(ValueError, match="origin"):
        bind_field_leak_source(
            record, q, c.model_copy(update={"origin": "measured"}), source_root=root, source_spec=spec
        )
    with pytest.raises(ValueError, match="binding"):
        bind_field_leak_source(
            record,
            q.model_copy(update={"head_sensor_id": "another_sensor"}),
            c,
            source_root=root,
            source_spec=spec,
        )


def test_resealed_raw_numbers_still_need_actual_source(source):
    root, spec, record, _, _ = source
    changed = deepcopy(record)
    changed["rows"][0]["reported_pressure_m"] = 999.0
    changed["record_digest"] = digest({k: v for k, v in changed.items() if k != "record_digest"})
    with pytest.raises(ValueError, match="replay"):
        verify_field_leak_record(changed, source_root=root, source_spec=spec)


def test_replay_does_not_follow_record_paths_outside_root(source):
    root, spec, record, _, _ = source
    changed = deepcopy(record)
    changed["filename"] = "../Q_P_80_Leak1.xlsx"
    changed["record_digest"] = digest({k: v for k, v in changed.items() if k != "record_digest"})
    with pytest.raises(ValueError, match="basename"):
        verify_field_leak_record(changed, source_root=root, source_spec=spec)


def test_availability_upper_bound_preserves_small_positive_clock_terms(source):
    from fractions import Fraction

    root, spec, record, q, c = source
    q, c = reviewed(q, c)
    q = q.model_copy(
        update={"analysis_start_s": 1e16, "report_to_available_offset_s": 0.1, "clock_error_bound_s": 0.1}
    )
    c = c.model_copy(update={"metadata": {"field_leak_boundary": field_leak_boundary(q)}})
    result = bind_field_leak_source(record, q, c, source_root=root, source_spec=spec)
    for frame, row in zip(result["frames"], record["rows"], strict=True):
        exact = sum(
            (
                Fraction.from_float(float(v))
                for v in (
                    q.analysis_start_s,
                    row["elapsed_s"],
                    q.report_to_available_offset_s,
                    q.clock_error_bound_s,
                )
            ),
            Fraction(),
        )
        assert Fraction.from_float(frame["time_s"]) >= exact
    assert result["frames"][0]["time_s"] > q.analysis_start_s


def monitoring_case(source, *, policy="chronological_within_run"):
    from pipeline_integrity.field_leak_data import FieldMonitoringSelection, field_monitoring_boundary
    from pipeline_integrity.monitoring_exchange import MonitoringRequest

    root, spec, record, mapping, c = source
    mapping, c = reviewed(mapping, c)
    binding = bind_field_leak_source(record, mapping, c, source_root=root, source_spec=spec)
    selections = tuple(
        FieldMonitoringSelection(
            binding_digest=binding["record_digest"],
            role=role,
            split_id=role,
            physical_run_id="one_campaign",
            source_group_id="one_source",
            observation_ids=tuple(row["observation_id"] for row in record["rows"][start : start + 10]),
            reference_kind="not_used" if role == "evaluation" else "caller_declared_reference",
        )
        for role, start in (("training", 0), ("calibration", 10), ("evaluation", 20))
    )
    request = MonitoringRequest(
        family="statistical",
        method="robust_z",
        asset_id=c.asset_id,
        clock_id=c.clock_id,
        population_id="authored_population",
        split_policy=policy,
        parameters=None,
        normalize=True,
        false_alarm_fraction=0.05,
        evidence_ids=("source", "mapping"),
    )
    bindings = (binding,) * 3
    context = c.model_copy(
        update={
            "metadata": {
                "field_monitoring_boundary": field_monitoring_boundary(request, bindings, selections)
            }
        }
    )
    return root, spec, bindings, selections, request, context


def test_field_monitoring_keeps_unreviewed_owner_withheld(source):
    from pipeline_integrity.field_leak_data import evaluate_field_monitoring, verify_field_monitoring_record

    root, spec, bindings, selections, request, context = monitoring_case(source)

    class ForbiddenClient:
        def run(self, *args, **kwargs):
            raise AssertionError("unresolved monitoring reviews must not run the owner")

    result = evaluate_field_monitoring(
        bindings, selections, request, context, source_root=root, source_spec=spec, client=ForbiddenClient()
    )
    assert result["monitoring"]["status"] == "needs_data"
    assert result["controlled_indicator_used_as_feature"] is False
    assert result["independent_field_validation"] is False
    assert verify_field_monitoring_record(result, source_root=root, source_spec=spec)["valid"]


def test_names_cannot_manufacture_separate_campaigns(source):
    from pipeline_integrity.field_leak_data import evaluate_field_monitoring

    root, spec, bindings, selections, request, context = monitoring_case(
        source, policy="separate_physical_runs"
    )
    with pytest.raises(ValueError, match="three physical runs"):
        evaluate_field_monitoring(bindings, selections, request, context, source_root=root, source_spec=spec)


def test_changed_field_monitoring_selection_requires_new_binding(source):
    from pipeline_integrity.field_leak_data import evaluate_field_monitoring

    root, spec, bindings, selections, request, context = monitoring_case(source)
    changed = (selections[0].model_copy(update={"physical_run_id": "different"}), *selections[1:])
    with pytest.raises(ValueError, match="context binding"):
        evaluate_field_monitoring(bindings, changed, request, context, source_root=root, source_spec=spec)


@pytest.mark.parametrize("field", ["analysis_start_s", "report_to_available_offset_s", "clock_error_bound_s"])
def test_monitoring_stages_cannot_retime_the_same_campaign_independently(source, field):
    from pipeline_integrity.field_leak_data import evaluate_field_monitoring, field_monitoring_boundary

    root, spec, bindings, selections, request, context = monitoring_case(source)
    _, _, record, mapping, c = source
    mapping, c = reviewed(mapping, c)
    mapping = mapping.model_copy(update={field: getattr(mapping, field) + 1.0})
    c = c.model_copy(update={"metadata": {"field_leak_boundary": field_leak_boundary(mapping)}})
    replacement = bind_field_leak_source(record, mapping, c, source_root=root, source_spec=spec)
    bindings = (bindings[0], replacement, bindings[2])
    selections = (
        selections[0],
        selections[1].model_copy(update={"binding_digest": replacement["record_digest"]}),
        selections[2],
    )
    context = context.model_copy(
        update={
            "metadata": {
                "field_monitoring_boundary": field_monitoring_boundary(request, bindings, selections)
            }
        }
    )
    with pytest.raises(ValueError, match="consistent source-to-analysis"):
        evaluate_field_monitoring(bindings, selections, request, context, source_root=root, source_spec=spec)


def test_distinct_report_rows_cannot_hide_overlapping_training_calibration_exposures(source):
    from pipeline_integrity.field_leak_data import evaluate_field_monitoring, field_monitoring_boundary

    root, spec, _, mapping, c = source
    mapping, c = reviewed(mapping, c)
    mapping = mapping.model_copy(update={"averaging_width_s": 60.0})
    c = c.model_copy(update={"metadata": {"field_leak_boundary": field_leak_boundary(mapping)}})
    record = source[2]
    binding = bind_field_leak_source(record, mapping, c, source_root=root, source_spec=spec)
    _, _, _, selections, request, context = monitoring_case(source)
    bindings = (binding,) * 3
    selections = tuple(s.model_copy(update={"binding_digest": binding["record_digest"]}) for s in selections)
    context = context.model_copy(
        update={
            "metadata": {
                "field_monitoring_boundary": field_monitoring_boundary(request, bindings, selections)
            }
        }
    )
    with pytest.raises(ValueError, match="averaging exposure"):
        evaluate_field_monitoring(bindings, selections, request, context, source_root=root, source_spec=spec)
