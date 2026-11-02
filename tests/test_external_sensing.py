"""Prepared quality/geometry/replay checks; execute on the deferred HPC test run."""

from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from pipeline_integrity.contracts import digest
from pipeline_integrity.external_sensing import (
    ExternalChannel,
    SensorFootprint,
    adapt_external_sensing,
    boundary_metadata,
    evaluate_external_monitoring,
    to_monitoring_split,
    to_sampled_trace,
    verify_external_sensing_record,
)
from pipeline_integrity.metrology import MeasurementBasis

spec = spec_from_file_location("external_example", Path(__file__).parents[1] / "examples/external_sensing.py")
example = module_from_spec(spec)
spec.loader.exec_module(example)


def rebound(q, c, **updates):
    q = type(q).model_validate(q.model_copy(update=updates).model_dump(mode="python"))
    return q, c.model_copy(update={"metadata": {"external_sensing_boundary": boundary_metadata(q)}})


def test_reviews_block_handoff():
    q, c = example.make_case()
    r = adapt_external_sensing(q, c)
    assert r["status"] == "needs_data" and r["frames"] == []
    assert verify_external_sensing_record(r)
    with pytest.raises(ValueError):
        to_monitoring_split(r, role="training", split_id="s", physical_run_id="r", source_group_id="g")


def test_censoring_not_zero_or_normal():
    q, c = example.make_case(reviewed=True)
    r = adapt_external_sensing(q, c)
    assert [f["time_s"] for f in r["frames"]] == [10.0, 11.0, 12.0, 13.0]
    assert [f["values"]["methane_local"] for f in r["frames"]] == [0.001, None, None, None]
    assert [f["valid"]["methane_local"] for f in r["frames"]] == [True, False, False, False]
    assert r["quality_sidecar"][1]["readings"][0]["censoring"]["upper"] == 1e-5
    assert r["quality_sidecar"][2]["readings"][0]["reported_value"] == 0.05
    assert r["leak_detection_probability"] is None
    split = to_monitoring_split(
        r, role="evaluation", split_id="s", physical_run_id="run", source_group_id="g"
    )
    assert split.frames[1].values["methane_local"] is None
    assert split.observation_ids == tuple(a.id for a in q.acquisitions)
    assert verify_external_sensing_record(r)


@pytest.mark.parametrize("update", [{"source_to_analysis_offset_s": None}, {"clock_error_bound_s": None}])
def test_unknown_clock_blocks_frames(update):
    q, c = example.make_case(reviewed=True)
    q, c = rebound(q, c, **update)
    assert adapt_external_sensing(q, c)["frames"] == []


@pytest.mark.parametrize(
    "state,value",
    [
        ("non_detect", 0.0),
        ("quantified", None),
        ("quantified", 0.05),
        ("quantified", 0.000001),
        ("saturated_upper", 0.001),
        ("missing", 0.0),
    ],
)
def test_contradictory_instrument_states_rejected(state, value):
    q, c = example.make_case(reviewed=True)
    first = q.acquisitions[0]
    reading = first.readings[0].model_copy(update={"state": state, "value": value})
    q, c = rebound(
        q, c, acquisitions=(first.model_copy(update={"readings": (reading,)}), *q.acquisitions[1:])
    )
    with pytest.raises(ValueError):
        adapt_external_sensing(q, c)


def test_explicit_rectangular_missingness_and_work_budget():
    q, c = example.make_case(reviewed=True)
    with pytest.raises(ValueError):
        rebound(q, c, maximum_readings=1)
    with pytest.raises(ValueError):
        rebound(q, c, acquisitions=(q.acquisitions[0].model_copy(update={"readings": ()}),))


def test_point_concentration_cannot_be_remote_column():
    q, _ = example.make_case(reviewed=True)
    with pytest.raises(ValueError):
        ExternalChannel.model_validate(
            q.channels[0].model_copy(update={"modality": "remote_column"}).model_dump(mode="python")
        )


def test_das_gauge_and_regular_trace_preserves_invalid_samples():
    q, c = example.make_case(reviewed=True)
    channel = ExternalChannel(
        id="strain",
        sensor_id="gauge_1",
        modality="DAS",
        basis=MeasurementBasis(
            quantity="strain", unit="1", definition_id="axial_gauge_average", evidence_id="authored"
        ),
        footprint=SensorFootprint(
            id="fiber_segment",
            coordinate_frame_id="local_xyz",
            kind="fiber_gauge",
            vertices_m=((0.0, 0.0, 0.0), (10.0, 0.0, 0.0)),
            registration_evidence_id="authored",
            spatial_error_bound_m=0.1,
            fiber_id="fiber",
            fiber_distance_interval_m=(100.0, 110.0),
        ),
        detection_limit=1e-8,
        detection_rule="absolute_magnitude",
        saturation_lower=-0.01,
        saturation_upper=0.01,
        instrument_configuration_sha256=digest("gauge_average"),
        evidence_id="authored",
    )
    acquisitions = tuple(
        q.acquisitions[0].model_copy(
            update={
                "id": f"a{i}",
                "time_s": float(i),
                "exposure_start_s": float(i),
                "exposure_end_s": float(i),
                "readings": (
                    q.acquisitions[0]
                    .readings[0]
                    .model_copy(
                        update={
                            "channel_id": "strain",
                            "value": 1e-6,
                            "source_row_id": f"row{i}",
                            "state": "invalid" if i == 3 else "quantified",
                        }
                    ),
                ),
            }
        )
        for i in range(8)
    )
    q, c = rebound(q, c, channels=(channel,), acquisitions=acquisitions)
    r = adapt_external_sensing(q, c)
    trace = to_sampled_trace(r, "strain", sample_period_s=1.0)
    assert trace.quantity_id == "strain" and trace.unit == "1"
    assert trace.source_digest == r["record_sha256"]
    assert trace.values[3] is None and trace.valid[3] is False
    with pytest.raises(ValueError):
        to_sampled_trace(r, "strain", sample_period_s=0.5)


@pytest.mark.parametrize("field,value", [("field_qualified", True), ("frames", []), ("quality_sidecar", [])])
def test_rehashed_tamper_rejected(field, value):
    q, c = example.make_case(reviewed=True)
    r = deepcopy(adapt_external_sensing(q, c))
    r[field] = value
    r["record_sha256"] = digest({k: v for k, v in r.items() if k != "record_sha256"})
    with pytest.raises(ValueError):
        verify_external_sensing_record(r)


def test_native_monitoring_never_called_for_unbound_receipts(monkeypatch):
    q, c = example.make_case(reviewed=True)
    r = adapt_external_sensing(q, c)
    monkeypatch.setattr(
        "pipeline_integrity.external_sensing.evaluate_monitoring",
        lambda *a, **kw: pytest.fail("must not execute owner"),
    )
    with pytest.raises(ValueError):
        evaluate_external_monitoring(
            (r, r, r),
            None,
            c,
            split_ids=("a", "b", "c"),
            physical_run_ids=("a", "b", "c"),
            source_group_ids=("a", "b", "c"),
            reference_kinds=("unresolved",) * 3,
        )


def test_context_origin_and_nan_model_copy_rejected():
    q, c = example.make_case(reviewed=True)
    with pytest.raises(ValueError):
        adapt_external_sensing(q, c.model_copy(update={"origin": "measured"}))
    with pytest.raises(ValueError):
        adapt_external_sensing(q.model_copy(update={"clock_error_bound_s": float("nan")}), c)


@pytest.mark.parametrize(
    "kind,quantity,unit",
    [
        ("line_of_sight", "path_molar_column", "mol/m^2"),
        ("pixel_polygon", "mass_column", "kg/m^2"),
    ],
)
def test_column_support_is_not_local_concentration(kind, quantity, unit):
    q, c = example.make_case(reviewed=True)
    vertices = ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0))
    if kind == "pixel_polygon":
        vertices += ((10.0, 10.0, 0.0),)
    footprint = q.channels[0].footprint.model_copy(update={"kind": kind, "vertices_m": vertices})
    channel = q.channels[0].model_copy(
        update={
            "modality": "remote_column" if kind == "pixel_polygon" else "gas_detector",
            "basis": MeasurementBasis(
                quantity=quantity, unit=unit, definition_id="authored_column", evidence_id="authored"
            ),
            "footprint": footprint,
        }
    )
    q, c = rebound(q, c, channels=(channel,))
    r = adapt_external_sensing(q, c)
    assert r["frames"][0]["units"][channel.id] == unit
    assert r["request"]["channels"][0]["footprint"]["kind"] == kind


def test_native_exchange_delegated_without_adapter_statistics(monkeypatch):
    q, c = example.make_case(reviewed=True)
    records = []
    for index in range(3):
        acquisitions = tuple(
            a.model_copy(
                update={
                    "id": f"run{index}_{a.id}",
                    "readings": tuple(
                        r.model_copy(update={"source_row_id": f"run{index}_{r.source_row_id}"})
                        for r in a.readings
                    ),
                }
            )
            for a in q.acquisitions
        )
        qq, cc = rebound(q, c, id=f"q{index}", acquisitions=acquisitions)
        records.append(adapt_external_sensing(qq, cc))
    c = c.model_copy(update={"metadata": {"external_sensing_records": [r["record_sha256"] for r in records]}})
    sentinel = object()

    def owner(request, splits, context, **kwargs):
        assert request is sentinel and [s.role for s in splits] == ["training", "calibration", "evaluation"]
        assert splits[2].frames[1].valid["methane_local"] is False
        return {"owner_result": "delegated"}

    monkeypatch.setattr("pipeline_integrity.external_sensing.evaluate_monitoring", owner)
    assert evaluate_external_monitoring(
        records,
        sentinel,
        c,
        split_ids=("a", "b", "c"),
        physical_run_ids=("a", "b", "c"),
        source_group_ids=("a", "b", "c"),
        reference_kinds=("unresolved",) * 3,
    ) == {"owner_result": "delegated"}


def test_exposure_summary_is_available_at_end_not_midpoint():
    q, c = example.make_case(reviewed=True)
    first = q.acquisitions[0].model_copy(
        update={"time_s": 0.25, "exposure_start_s": 0.0, "exposure_end_s": 0.5}
    )
    q, c = rebound(q, c, acquisitions=(first, *q.acquisitions[1:]))
    r = adapt_external_sensing(q, c)
    assert r["frames"][0]["time_s"] == 10.5
    assert r["quality_sidecar"][0]["analysis_report_time_s"] == 10.25
    assert r["quality_sidecar"][0]["analysis_exposure_s"] == [10.0, 10.5]


def test_explicit_equal_boxcar_exposures_keep_a_distinct_trace_response():
    q, c = example.make_case(reviewed=True)
    channel = ExternalChannel(
        id="strain",
        sensor_id="gauge",
        modality="DAS",
        basis=MeasurementBasis(
            quantity="strain", unit="1", definition_id="authored_gauge", evidence_id="authored"
        ),
        footprint=SensorFootprint(
            id="fiber",
            coordinate_frame_id="xyz",
            kind="fiber_gauge",
            vertices_m=((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)),
            registration_evidence_id="authored",
            spatial_error_bound_m=0.1,
            fiber_id="fiber",
            fiber_distance_interval_m=(0.0, 1.0),
        ),
        detection_limit=1e-8,
        detection_rule="absolute_magnitude",
        saturation_lower=-0.01,
        saturation_upper=0.01,
        instrument_configuration_sha256=digest("authored boxcar response"),
        evidence_id="authored",
    )
    acquisitions = tuple(
        q.acquisitions[0].model_copy(
            update={
                "id": f"a{i}",
                "time_s": i + 0.25,
                "exposure_start_s": float(i),
                "exposure_end_s": i + 0.5,
                "readings": (
                    q.acquisitions[0]
                    .readings[0]
                    .model_copy(
                        update={
                            "channel_id": "strain",
                            "value": 1e-6,
                            "state": "quantified",
                            "source_row_id": f"r{i}",
                        }
                    ),
                ),
            }
        )
        for i in range(8)
    )
    q, c = rebound(q, c, channels=(channel,), acquisitions=acquisitions)
    r = adapt_external_sensing(q, c)
    with pytest.raises(ValueError, match="nonzero exposures"):
        to_sampled_trace(r, "strain", sample_period_s=1.0)
    channel = channel.model_copy(update={"temporal_response": "uniform_exposure_mean"})
    q, c = rebound(q, c, channels=(channel,))
    r = adapt_external_sensing(q, c)
    trace = to_sampled_trace(r, "strain", sample_period_s=1.0)
    assert trace.quantity_id == "strain:uniform_exposure_mean:0.5s:end_time"
    assert trace.start_time_s == 10.5
    assert trace.source_digest == r["record_sha256"]
