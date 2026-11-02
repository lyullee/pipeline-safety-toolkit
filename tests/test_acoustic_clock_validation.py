"""Numerical-owner regressions for acoustic, clock and adapter integration APIs.

All fixtures here are authored synthetic contracts, not measurements or field evidence.
"""
import json
import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame, digest
from pipeline_integrity.clock_correction import ClockCorrectionModel
from pipeline_integrity.clock_epochs import ClockEpoch, ClockEpochRequest, evaluate_clock_epochs
from pipeline_integrity.uncertainty import EnsembleMetric, UncertainVariable
from pipeline_integrity.adapter_jobs import AdapterJob
from pipeline_integrity.adapter_uncertainty import (
    AdapterUncertainInput, AdapterUncertainMetric, adapter_ensemble_node,
)


def _evidence(identity):
    return EvidenceRef(id=identity, citation="Authored synthetic test fixture", origin="synthetic",
                       sha256=digest(("test evidence", identity)))


def test_clock_epoch_affine_mapping_preserves_rows_and_conservative_bounds():
    # Explicitly declared source epochs map to adjacent reference-time intervals.
    refs = tuple(_evidence(k) for k in ("src-a", "model-a", "review-a", "boundary-a", "order"))
    source_ctx = RunContext(asset_id="pipe", scenario_id="clock-fixture", clock_id="source-a",
                            origin="synthetic", evidence=refs[:4])
    frames = (
        SignalFrame(stream_id="pressure", clock_id="source-a", time_s=10.0,
                    values={"p": 100.0}, units={"p": "Pa"}, valid={"p": True}, origin="synthetic"),
        SignalFrame(stream_id="pressure", clock_id="source-a", time_s=11.0,
                    values={"p": None}, units={"p": "Pa"}, valid={"p": False}, origin="synthetic"),
    )
    model = ClockCorrectionModel(model_id="affine-a", source_clock_id="source-a", reference_clock_id="plant",
        source_epoch_s=10.0, reference_epoch_s=1.0, offset_s=0.0, rate_error=0.01,
        valid_source_start_s=9.0, valid_source_end_s=12.0, residual_bound_s=0.001,
        model_evidence_id="model-a", review_evidence_id="review-a", caller_reviewed=True)
    epoch = ClockEpoch(id="epoch-a", boundary_kind="initial", source_record_id="record-a",
        source_record_sha256=refs[0].sha256, source_context=source_ctx, model=model,
        source_frames=frames, source_frames_digest=digest(frames), observation_ids=("obs-a0", "obs-a1"),
        source_row_indices=(0, 1), source_time_error_bounds_s=(0.0001, 0.0001),
        source_coverage_start_s=9.5, source_coverage_end_s=11.5, source_evidence_id="src-a",
        model_evidence_sha256=refs[1].sha256, review_evidence_sha256=refs[2].sha256,
        boundary_evidence_id="boundary-a", boundary_evidence_sha256=refs[3].sha256,
        mapping_residual_bound_basis="reference_clock_seconds",
        mapping_residual_covers_parameter_uncertainty_reviewed=True)
    target = RunContext(asset_id="pipe", scenario_id="clock-fixture", clock_id="plant", origin="synthetic",
                        evidence=refs)
    request = ClockEpochRequest(id="clock-join", asset_id="pipe", reference_clock_id="plant", epochs=(epoch,),
        epoch_order_evidence_id="order", epoch_order_evidence_sha256=refs[4].sha256,
        epoch_order_and_boundaries_reviewed=True)
    result = evaluate_clock_epochs(request, target)
    assert result["status"] == "computed"
    assert [f["time_s"] for f in result["candidate_frames"]] == pytest.approx((1.0, 1.0 + 1.0 / 1.01))
    assert result["dropped_frame_count"] == 0
    assert result["source_invalid_channel_count"] == result["target_invalid_channel_count"] == 1
    bound = result["lineage"][0]["target_time_error_bound_s"]
    assert bound >= 0.0001 / 1.01 + model.residual_bound_s
    assert result["field_qualification"] is False


def test_adapter_ensemble_node_calls_native_adapter_for_each_draw():
    from pipeline_integrity import adapter_jobs
    eids = ("geometry", "particle", "input-review", "metric-review", "unit-review")
    evidence = tuple(_evidence(k) for k in eids)
    context = RunContext(asset_id="column", scenario_id="settling-fixture", clock_id="elapsed",
                         origin="synthetic", evidence=evidence)
    params = {
        "id": "settle-job", "adapter_id": "transport.particle_settling", "context": context.model_dump(mode="json"),
        "parameters": {"id":"settle", "asset_id":"column", "clock_id":"elapsed", "column_area_m2":0.01,
            "height_edges_m":[0.0,1.0], "carrier_density_kg_m3":1000.0, "carrier_viscosity_Pa_s":0.001,
            "particles":[{"id":"sand","diameter_m":0.0001,"density_kg_m3":2650.0,
                "initial_concentration_kg_m3":[0.1],"evidence_id":"particle"}],
            "elapsed_sample_time_s":[0.0,10.0], "maximum_reviewed_solids_volume_fraction":0.01,
            "dilute_noninteracting_spheres_quiescent_newtonian_carrier_reviewed":True,
            "terminal_regime_and_external_absorbing_collector_reviewed":True,
            "geometry_and_carrier_evidence_ids":["geometry"]}
    }
    # This is a JSON-shaped payload (including list-backed evidence), so use
    # Pydantic's JSON validation path rather than strict Python-object parsing.
    job = AdapterJob.model_validate_json(json.dumps(params))
    variable = UncertainVariable(id="diameter", unit="m", layer="input", evidence_id="input-review")
    metric = EnsembleMetric(id="settling_speed", unit="m/s", meaning="quantity", evidence_id="metric-review")
    output = AdapterUncertainMetric(metric=metric, result_pointer="/particle_classes/0/terminal_velocity_m_s",
        source_unit="m/s", source_meaning="quantity", evidence_id="unit-review", scalar_unit_meaning_reviewed=True)
    node = adapter_ensemble_node(job, id="settling-diameter-sweep", variables=(variable,),
        inputs=(AdapterUncertainInput(variable_id="diameter", parameter_pointer="/particles/0/diameter_m",
            target_unit="m", evidence_id="input-review", unit_mapping_reviewed=True),),
        metrics=(output,), providers={}, accept_computed_diagnostic=True)
    small = node.evaluate({"diameter": 8e-5}, context)
    large = node.evaluate({"diameter": 1.2e-4}, context)
    assert small.statuses["settling_speed"] == large.statuses["settling_speed"] == "computed"
    assert small.values["settling_speed"] > 0
    assert large.values["settling_speed"] > small.values["settling_speed"]
    missing = node.evaluate({"diameter": None}, context)
    assert missing.statuses["settling_speed"] == "needs_data"
    assert small.native_payload["field_qualification_conferred"] is False


