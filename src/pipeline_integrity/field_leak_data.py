"""Pinned controlled-field observations with unresolved pressure/clock meanings retained."""

import json
import math
from datetime import datetime, time
from fractions import Fraction
from importlib.metadata import version
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, RunContext, SignalFrame, digest
from .datasets import catalog, file_digest
from .monitoring_exchange import MonitoringSplit
from .soil_data import _numeric, _pinned, _workbook

DATASET_ID = "andean_controlled_leaks"
PROFILES = {"Q_P_80_Leak1.xlsx": ("Controlled leak 1", 80), "Q_P_46_Leak2.xlsx": ("Controlled leak 2", 46)}
HEADERS = [
    "Column1. DATE",
    "Column2. TIME",
    "Column3. PRESSURE (m)",
    "Column4. FLOW (m3/s)",
    "Column5. Controlled leak. (1 is open leak) (0 no leak)",
]


def _calendar(day, clock):
    if (
        not isinstance(day, dict)
        or not isinstance(clock, dict)
        or day.get("source_type") not in {"date", "datetime"}
        or clock.get("source_type") != "time"
    ):
        return None
    if day.get("timezone") is not None or clock.get("timezone") is not None:
        raise ValueError("source profile has no reviewed timezone mapping")
    parsed = datetime.fromisoformat(day["iso"])
    tod = time.fromisoformat(clock["iso"])
    if parsed.tzinfo is not None or tod.tzinfo is not None or parsed.time() != time(0):
        raise ValueError("source DATE must be a naive midnight date; TIME supplies the clock")
    return datetime.combine(parsed.date(), tod)


def read_field_leak_workbook(path, *, source_spec=None):
    path = Path(path)
    if path.name not in PROFILES:
        raise ValueError("select one reviewed field campaign workbook")
    spec = next(s for s in catalog() if s.id == DATASET_ID) if source_spec is None else source_spec
    raw, provenance = _pinned(path, DATASET_ID, source_spec=spec, maximum_bytes=100_000)
    sheets = _workbook(raw, maximum_cells=5000, maximum_sheets=2)
    sheet_name, expected_rows = PROFILES[path.name]
    if set(sheets) != {sheet_name} or sheets[sheet_name]["source_cells"][0][:5] != HEADERS:
        raise ValueError("field source sheet/header profile changed")
    rows = []
    ignored = []
    calendars = []
    for number, values in enumerate(sheets[sheet_name]["source_cells"][1:], 2):
        if all(v is None for v in values[:5]):
            ignored.append({"source_row": number, "source_cells": values})
            continue
        calendar = _calendar(values[0], values[1])
        flag = values[4]
        if flag is not None and (
            isinstance(flag, bool) or not isinstance(flag, (int, float)) or flag not in (0, 1)
        ):
            raise ValueError("controlled source indicator must be 0, 1 or unknown")
        calendars.append(calendar)
        rows.append(
            {
                "source_row": number,
                "observation_id": provenance["sha256"] + ":" + str(number),
                "source_cells": values,
                "local_calendar": None if calendar is None else calendar.isoformat(),
                "reported_pressure_m": _numeric(values[2]),
                "volume_flow_m3_s": _numeric(values[3]),
                "declared_controlled_leak_indicator": None if flag is None else int(flag),
                "elapsed_s": None,
            }
        )
    if len(rows) != expected_rows:
        raise ValueError("reviewed field campaign observation count changed")
    resolved = all(c is not None for c in calendars) and all(
        b > a for a, b in zip(calendars, calendars[1:], strict=False)
    )
    if resolved:
        for row, calendar in zip(rows, calendars, strict=True):
            row["elapsed_s"] = (calendar - calendars[0]).total_seconds()
    body = {
        "schema": "pipeline_integrity.field_leak_source.v1",
        "dataset_id": DATASET_ID,
        "filename": path.name,
        "sheet": sheet_name,
        "provenance": provenance,
        "catalog_entry_digest": digest(spec),
        "rows": rows,
        "excluded_nonobservation_rows": ignored,
        "source_relative_clock_resolved": resolved,
        "timezone": None,
        "utc_assigned": False,
        "pressure_reference": None,
        "pressure_Pa": None,
        "pressure_quantity_definition": "source m label; caller review required before treating as pressure head",
        "source_order_changed": False,
        "sampling_support": "unresolved",
        "indicator_is_detector_feature": False,
        "independent_field_validation": False,
        "module_sha256": file_digest(Path(__file__)),
        "dependencies": {
            n: file_digest(Path(__file__).with_name(n + ".py"))
            for n in ("contracts", "datasets", "soil_data")
        },
        "openpyxl_version": version("openpyxl"),
    }
    return dict(body, record_digest=digest(body))


def verify_field_leak_record(record, *, source_root, source_spec=None):
    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {k: v for k, v in record.items() if k != "record_digest"}
    ):
        raise ValueError("field source record seal changed")
    name = record.get("filename")
    if name not in PROFILES:
        raise ValueError("field replay filename is not an allowlisted basename")
    root = Path(source_root).resolve(strict=True)
    target = root / name
    if not target.resolve(strict=True).is_relative_to(root):
        raise ValueError("field replay target leaves source root")
    if read_field_leak_workbook(target, source_spec=source_spec) != record:
        raise ValueError("field observations, metadata or current reader changed on replay")
    return True


class FieldLeakMapping(Contract):
    id: Name
    source_record_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    asset_id: Name
    stream_id: Name
    clock_id: Name
    site_id: Name
    head_sensor_id: Name
    flow_sensor_id: Name
    head_port_id: Name
    flow_port_id: Name
    head_definition_id: Name | None = None
    flow_definition_id: Name | None = None
    temporal_support: Literal["point_report", "trailing_interval_mean", "unresolved"] = "unresolved"
    averaging_width_s: Nonnegative | None = None
    analysis_start_s: Nonnegative = 0.0
    report_to_available_offset_s: Nonnegative | None = None
    clock_error_bound_s: Nonnegative | None = None
    source_evidence_id: Name
    mapping_evidence_id: Name
    source_clock_mapping_reviewed: bool = False
    quantity_sensor_and_temporal_basis_reviewed: bool = False

    @model_validator(mode="after")
    def support(self):
        if self.temporal_support == "point_report" and self.averaging_width_s not in (None, 0.0):
            raise ValueError("point reports cannot declare a positive averaging interval")
        if (
            self.temporal_support == "trailing_interval_mean"
            and self.averaging_width_s is not None
            and self.averaging_width_s <= 0
        ):
            raise ValueError("a reviewed interval mean needs positive exposure width")
        return self


def field_leak_boundary(mapping):
    return {
        "mapping_digest": digest(mapping),
        "source_record_digest": mapping.source_record_digest,
        "asset_id": mapping.asset_id,
        "clock_id": mapping.clock_id,
    }


def _latest_available_time(*terms):
    """Round upward only when the exact declared float-term sum requires it."""
    try:
        value = math.fsum(terms)
    except OverflowError as exc:
        raise ValueError("source availability clock exceeds finite representation") from exc
    if not math.isfinite(value):
        raise ValueError("source availability clock exceeds finite representation")
    exact = sum((Fraction.from_float(float(term)) for term in terms), Fraction())
    if Fraction.from_float(value) < exact:
        value = math.nextafter(value, math.inf)
    if not math.isfinite(value):
        raise ValueError("source availability upper bound cannot be represented")
    return value


def bind_field_leak_source(record, mapping, context, *, source_root, source_spec=None):
    verify_field_leak_record(record, source_root=source_root, source_spec=source_spec)
    q = FieldLeakMapping.model_validate(mapping.model_dump(mode="python"))
    c = RunContext.model_validate(context.model_dump(mode="python"))
    source_origin = {
        "measured_process": "measured",
        "laboratory_experiment": "laboratory",
        "simulation": "simulation",
    }.get(record["provenance"]["dataset_origin"])
    if (
        source_origin is None
        or q.source_record_digest != record["record_digest"]
        or (q.asset_id, q.clock_id) != (c.asset_id, c.clock_id)
        or c.origin != source_origin
        or c.metadata.get("field_leak_boundary") != field_leak_boundary(q)
    ):
        raise ValueError("field source/mapping/asset/clock/origin context binding differs")
    evidence = {e.id: e for e in c.evidence}
    if len(evidence) != len(c.evidence):
        raise ValueError("field context evidence identities must be unique")
    if q.source_evidence_id not in evidence or q.mapping_evidence_id not in evidence:
        raise ValueError("field source and mapping evidence must exist in context")
    source_hash = evidence[q.source_evidence_id].sha256
    if source_hash is not None and source_hash != record["provenance"]["sha256"]:
        raise ValueError("field source evidence hash differs from actual workbook")
    missing = []
    if not record["source_relative_clock_resolved"] or not q.source_clock_mapping_reviewed:
        missing.append("reviewed source relative clock mapping")
    if (
        not q.quantity_sensor_and_temporal_basis_reviewed
        or q.head_definition_id is None
        or q.flow_definition_id is None
    ):
        missing.append("pressure-head/flow quantity, sensor and temporal interpretation")
    if (
        q.temporal_support == "unresolved"
        or q.report_to_available_offset_s is None
        or q.clock_error_bound_s is None
        or q.temporal_support == "trailing_interval_mean"
        and q.averaging_width_s is None
    ):
        missing.append("known source exposure, availability and clock-error support")
    body = {
        "schema": "pipeline_integrity.field_leak_binding.v1",
        "mapping": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "source_record_digest": record["record_digest"],
        "status": "needs_data" if missing else "computed",
        "missing": missing,
        "frames": [],
        "sidecar": [],
        "controlled_state_is_model_feature": False,
        "pressure_converted_to_Pa": False,
        "independent_validation": False,
        "module_sha256": file_digest(Path(__file__)),
        "dependencies": {
            n: file_digest(Path(__file__).with_name(n + ".py"))
            for n in ("contracts", "monitoring_exchange", "datasets", "soil_data")
        },
    }
    if not missing:
        for row in record["rows"]:
            elapsed = row["elapsed_s"]
            report_time = q.analysis_start_s + elapsed
            available = _latest_available_time(
                q.analysis_start_s, elapsed, q.report_to_available_offset_s, q.clock_error_bound_s
            )
            if not math.isfinite(available) or body["frames"] and available <= body["frames"][-1]["time_s"]:
                raise ValueError("mapped clock cannot represent ordered source availability times")
            values = {"pressure_head": row["reported_pressure_m"], "volume_flow": row["volume_flow_m3_s"]}
            frame = SignalFrame(
                stream_id=q.stream_id,
                clock_id=q.clock_id,
                time_s=available,
                values=values,
                units={"pressure_head": "m", "volume_flow": "m^3/s"},
                valid={name: value is not None for name, value in values.items()},
                origin=c.origin,
            )
            body["frames"].append(frame.model_dump(mode="json"))
            width = q.averaging_width_s if q.temporal_support == "trailing_interval_mean" else 0.0
            body["sidecar"].append(
                {
                    "observation_id": row["observation_id"],
                    "source_row": row["source_row"],
                    "local_calendar": row["local_calendar"],
                    "source_elapsed_s": elapsed,
                    "source_relative_exposure_s": [elapsed - width, elapsed],
                    "analysis_report_time_s": report_time,
                    "latest_reviewed_available_time_s": available,
                    "clock_error_bound_s": q.clock_error_bound_s,
                    "declared_controlled_leak_indicator": row["declared_controlled_leak_indicator"],
                    "sensor_ids": {"pressure_head": q.head_sensor_id, "volume_flow": q.flow_sensor_id},
                    "port_ids": {"pressure_head": q.head_port_id, "volume_flow": q.flow_port_id},
                }
            )
    return dict(body, record_digest=digest(body))


def verify_field_binding(binding, *, source_root, source_spec=None):
    if not isinstance(binding, dict) or binding.get("record_digest") != digest(
        {k: v for k, v in binding.items() if k != "record_digest"}
    ):
        raise ValueError("field binding seal changed")
    q = FieldLeakMapping.model_validate_json(json.dumps(binding["mapping"]))
    c = RunContext.model_validate_json(json.dumps(binding["context"]))
    matches = []
    root = Path(source_root).resolve(strict=True)
    for filename in PROFILES:
        source = root / filename
        if source.exists() and not source.resolve(strict=True).is_relative_to(root):
            raise ValueError("field replay candidate leaves source root")
        if source.is_file():
            record = read_field_leak_workbook(source, source_spec=source_spec)
            if record["record_digest"] == q.source_record_digest:
                matches.append(record)
    if (
        len(matches) != 1
        or bind_field_leak_source(matches[0], q, c, source_root=source_root, source_spec=source_spec)
        != binding
    ):
        raise ValueError("field source/mapping/output differs on replay")
    return True


def to_field_monitoring_split(
    binding,
    *,
    source_root,
    role,
    split_id,
    physical_run_id,
    source_group_id,
    observation_ids,
    reference_kind="unresolved",
    source_spec=None,
):
    verify_field_binding(binding, source_root=source_root, source_spec=source_spec)
    if (
        binding["status"] != "computed"
        or not observation_ids
        or len(set(observation_ids)) != len(observation_ids)
    ):
        raise ValueError("explicit unique observation selection and resolved field mapping required")
    lookup = {row["observation_id"]: i for i, row in enumerate(binding["sidecar"])}
    if any(key not in lookup for key in observation_ids):
        raise ValueError("monitoring selection includes an unknown original observation")
    indices = [lookup[key] for key in observation_ids]
    if indices != sorted(indices):
        raise ValueError("monitoring selection must preserve original source order")
    q = FieldLeakMapping.model_validate_json(json.dumps(binding["mapping"]))
    return MonitoringSplit(
        role=role,
        split_id=split_id,
        physical_run_id=physical_run_id,
        source_group_id=source_group_id,
        source_evidence_id=q.source_evidence_id,
        observation_ids=tuple(observation_ids),
        frames=tuple(SignalFrame.model_validate_json(json.dumps(binding["frames"][i])) for i in indices),
        reference_kind=reference_kind,
    )


class FieldMonitoringSelection(Contract):
    binding_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    role: Literal["training", "calibration", "evaluation"]
    split_id: Name
    physical_run_id: Name
    source_group_id: Name
    observation_ids: tuple[Name, ...] = Field(min_length=1, max_length=1000)
    reference_kind: Literal["verified_normal", "caller_declared_reference", "not_used", "unresolved"] = (
        "unresolved"
    )


def field_monitoring_boundary(request, bindings, selections):
    return {
        "request_digest": digest(request),
        "binding_digests": [b["record_digest"] for b in bindings],
        "selection_digests": [digest(s) for s in selections],
    }


def _field_monitoring_splits(bindings, selections, request, context, source_root, source_spec):
    if len(bindings) != 3 or len(selections) != 3:
        raise ValueError("three explicit field bindings and source selections are required")
    selected = tuple(FieldMonitoringSelection.model_validate(s.model_dump(mode="python")) for s in selections)
    c = RunContext.model_validate(context.model_dump(mode="python"))
    if tuple(s.role for s in selected) != ("training", "calibration", "evaluation"):
        raise ValueError("field monitoring stages must be ordered training/calibration/evaluation")
    if c.metadata.get("field_monitoring_boundary") != field_monitoring_boundary(request, bindings, selected):
        raise ValueError("field monitoring recipe/source selection context binding differs")
    mappings = []
    splits = []
    for binding, selection in zip(bindings, selected, strict=True):
        if selection.binding_digest != binding["record_digest"]:
            raise ValueError("selection refers to another field binding")
        verify_field_binding(binding, source_root=source_root, source_spec=source_spec)
        mapping = FieldLeakMapping.model_validate_json(json.dumps(binding["mapping"]))
        if (mapping.asset_id, mapping.clock_id, binding["context"]["origin"]) != (
            c.asset_id,
            c.clock_id,
            c.origin,
        ):
            raise ValueError("field monitoring asset/clock/origin differs from the source binding")
        mappings.append(mapping)
        splits.append(
            to_field_monitoring_split(
                binding,
                source_root=source_root,
                role=selection.role,
                split_id=selection.split_id,
                physical_run_id=selection.physical_run_id,
                source_group_id=selection.source_group_id,
                observation_ids=selection.observation_ids,
                reference_kind=selection.reference_kind,
                source_spec=source_spec,
            )
        )

    def support(m):
        return digest(
            {
                key: getattr(m, key)
                for key in (
                    "site_id",
                    "stream_id",
                    "head_sensor_id",
                    "flow_sensor_id",
                    "head_port_id",
                    "flow_port_id",
                    "head_definition_id",
                    "flow_definition_id",
                    "temporal_support",
                    "averaging_width_s",
                )
            }
        )

    if len({support(m) for m in mappings}) != 1:
        raise ValueError("field sensing location, quantity or temporal response differs across stages")
    groups = {b["source_record_digest"] for b in bindings}
    if request.split_policy == "chronological_within_run" and len(groups) != 1:
        raise ValueError("chronological field stages must retain one actual source campaign")
    if request.split_policy == "chronological_within_run":
        if (
            len(
                {
                    (m.analysis_start_s, m.report_to_available_offset_s, m.clock_error_bound_s)
                    for m in mappings
                }
            )
            != 1
        ):
            raise ValueError(
                "chronological field stages require one consistent source-to-analysis clock mapping"
            )
        supports = []
        for binding, selection in zip(bindings, selected, strict=True):
            rows = {row["observation_id"]: row for row in binding["sidecar"]}
            supports.append((rows[selection.observation_ids[0]], rows[selection.observation_ids[-1]]))
        for (_, previous), (following, _) in zip(supports, supports[1:], strict=False):
            if previous["source_elapsed_s"] >= following["source_elapsed_s"]:
                raise ValueError("chronological stages reverse or overlap actual source report times")
            if previous["source_relative_exposure_s"][1] > following["source_relative_exposure_s"][0]:
                raise ValueError("chronological field stages overlap original averaging exposure support")
    if request.split_policy == "separate_physical_runs" and len(groups) != 3:
        raise ValueError("distinct names cannot turn repeated workbook campaigns into three physical runs")
    return selected, c, tuple(splits)


def evaluate_field_monitoring(
    bindings,
    selections,
    request,
    context,
    *,
    source_root,
    source_spec=None,
    client=None,
    owner_source_root=None,
):
    """Call the existing PMT gateway after validating actual field sources and support."""
    from .monitoring_exchange import MonitoringRequest, evaluate_monitoring

    q = MonitoringRequest.model_validate_json(request.model_dump_json())
    selected, c, splits = _field_monitoring_splits(bindings, selections, q, context, source_root, source_spec)
    native = evaluate_monitoring(q, splits, c, client=client, owner_source_root=owner_source_root)
    body = {
        "schema": "pipeline_integrity.field_monitoring_study.v1",
        "bindings": list(bindings),
        "selections": [s.model_dump(mode="json") for s in selected],
        "monitoring_request": q.model_dump(mode="json"),
        "context": c.model_dump(mode="json"),
        "monitoring": native,
        "source_sha256": file_digest(Path(__file__)),
        "monitoring_source_sha256": file_digest(Path(__file__).with_name("monitoring_exchange.py")),
        "independent_field_validation": False,
        "controlled_indicator_used_as_feature": False,
    }
    return dict(body, record_digest=digest(body))


def verify_field_monitoring_record(record, *, source_root, source_spec=None):
    """Re-read source mappings and use original PMT semantic verification, without refitting."""
    from .monitoring_exchange import MonitoringRequest, verify_monitoring_record

    if not isinstance(record, dict) or record.get("record_digest") != digest(
        {k: v for k, v in record.items() if k != "record_digest"}
    ):
        raise ValueError("field monitoring study seal changed")
    if (
        record.get("schema") != "pipeline_integrity.field_monitoring_study.v1"
        or record.get("independent_field_validation") is not False
        or record.get("controlled_indicator_used_as_feature") is not False
        or record.get("source_sha256") != file_digest(Path(__file__))
        or record.get("monitoring_source_sha256")
        != file_digest(Path(__file__).with_name("monitoring_exchange.py"))
    ):
        raise ValueError("field monitoring scope or current source changed")
    q = MonitoringRequest.model_validate_json(json.dumps(record["monitoring_request"]))
    c = RunContext.model_validate_json(json.dumps(record["context"]))
    selections = tuple(
        FieldMonitoringSelection.model_validate_json(json.dumps(s)) for s in record["selections"]
    )
    _, _, splits = _field_monitoring_splits(record["bindings"], selections, q, c, source_root, source_spec)
    native = record["monitoring"]
    if (
        native.get("request") != q.model_dump(mode="json")
        or native.get("context") != c.model_dump(mode="json")
        or native.get("splits") != [s.model_dump(mode="json") for s in splits]
    ):
        raise ValueError("original monitoring request/context/splits differ from actual field selection")
    verify_monitoring_record(native)
    return {
        "valid": True,
        "meaning": "current source re-read and original monitoring semantic verification; no refit or historical authentication",
    }
