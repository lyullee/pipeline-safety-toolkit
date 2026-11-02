"""Deferred real-source and contract cases; hypothetical assumptions are not field data."""

from copy import deepcopy
from pathlib import Path

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.friction_data import read_friction_observations
from pipeline_integrity.friction_study import (
    FrictionStudyRequest,
    evaluate_friction_study,
    friction_study_boundary_metadata,
    verify_friction_study_record,
)
from pipeline_integrity.inverse import FitParameter, FitRequest, ResidualScale

ROOT = Path(__file__).parents[1] / "data" / "raw" / "pipe_friction_roughness"


def make_case(*, reviewed=False, **changes):
    source = read_friction_observations(ROOT)
    csv_sha = next(s["sha256"] for s in source["sources"] if s["filename"] == "dataset.csv")
    values = dict(
        id="source_friction_study",
        asset_id="source_pilot_pipe",
        event_id="conditional_six_points",
        clock_id="externally_declared_study_clock",
        inlet_port_id="head_in",
        outlet_port_id="head_out",
        source_record_digest=source["record_digest"] if reviewed else None,
        source_evidence_id="source_csv",
        geometry_property_evidence_id="conditional_assumptions",
        physical_straight_length_m=84.58 if reviewed else None,
        internal_diameter_m=0.0486 if reviewed else None,
        gravity_m_s2=9.79 if reviewed else None,
        outlet_minus_inlet_elevation_m=0 if reviewed else None,
        density_kg_m3_by_source_row=(1000.0,) * 6 if reviewed else None,
        roughness_m=1e-5 if reviewed else None,
        minor_loss_coefficient=1.0 if reviewed else None,
        head_interpretation="same_reference_pressure_head_difference" if reviewed else "unknown",
        geometry_density_viscosity_and_local_loss_boundary_reviewed=reviewed,
        pressure_head_reference_and_elevation_reviewed=reviewed,
    )
    values.update(changes)
    q = FrictionStudyRequest(**values)
    c = RunContext(
        asset_id=q.asset_id,
        scenario_id=q.event_id,
        clock_id=q.clock_id,
        origin="laboratory",
        evidence=(
            EvidenceRef(
                id="source_csv",
                citation=source["citation"],
                origin="laboratory",
                sha256=csv_sha,
                license_id="MIT",
            ),
            EvidenceRef(
                id="conditional_assumptions",
                citation="Authored hypothetical density, elevation, K and roughness; not measured source facts",
                origin="synthetic",
            ),
            EvidenceRef(
                id="external_clock_fixture",
                citation="Authored external-clock contract fixture; not timestamps in the source dataset",
                origin="synthetic",
            ),
            EvidenceRef(
                id="scale_fixture",
                citation="Authored deterministic normalization and bounded-fit fixture, not measurement covariance",
                origin="synthetic",
            ),
        ),
        metadata={"friction_study_boundary": friction_study_boundary_metadata(q)},
    )
    return q, c


def fit_request(**changes):
    values = dict(
        parameters=(FitParameter(id="roughness_m", unit="m", lower=0, upper=1e-4, initial=1e-5),),
        provider_id="friction_hydraulic_loss",
        provider_version="1",
        fit_end_s=2,
        validation_start_s=3,
        validation_end_s=5,
    )
    values.update(changes)
    return FitRequest(**values)


def test_actual_source_default_does_not_guess_geometry_density_clock_or_error_model(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unknown source assumptions invoked a hydraulic/native owner")

    monkeypatch.setattr("pipeline_integrity.create_registry", forbidden)
    q, c = make_case()
    record = evaluate_friction_study(q, c, source_root=ROOT)
    assert record["status"] == "needs_data" and record["forward_rows"] is None
    assert len(record["source_observations"]["rows"]) == 6
    assert not record["published_fitted_parameters_used_as_truth"]
    assert record["source_observations"]["clock"] is None
    assert verify_friction_study_record(record, source_root=ROOT, request=q, context=c)


def test_conditional_forward_preserves_real_rows_units_and_native_pressure_work():
    q, c = make_case(reviewed=True)
    record = evaluate_friction_study(q, c, source_root=ROOT)
    assert record["status"] == "computed_diagnostic"
    for index, row in enumerate(record["forward_rows"]):
        source = record["source_observations"]["rows"][index]
        assert row["source_row"] == source["source_row"]
        assert row["observed_pressure_head_difference_m"] == pytest.approx(
            source["inlet_pressure_head_m"] - source["outlet_pressure_head_m"]
        )
        assert row["dynamic_viscosity_Pa_s"] == pytest.approx(1000 * source["kinematic_viscosity_m2_s"])
        assert row["native"]["model_id"] == "hydraulics.liquid_loss"
        assert row["pressure_work_rate_W"] == pytest.approx(
            row["potential_energy_rate_W"] + row["dissipated_power_W"]
        )
    assert not record["independent_experiment_validation"] and not record["field_qualified"]
    assert verify_friction_study_record(record, source_root=ROOT, request=q, context=c)


@pytest.mark.parametrize(
    "changes",
    [
        {"outlet_minus_inlet_elevation_m": None},
        {"minor_loss_coefficient": None},
        {"pressure_head_reference_and_elevation_reviewed": False},
    ],
)
def test_unknown_property_boundary_is_not_filled_from_published_fit(changes):
    q, c = make_case(reviewed=True, **changes)
    record = evaluate_friction_study(q, c, source_root=ROOT)
    assert record["status"] == "needs_data" and record["forward_rows"] is None


def test_requested_fit_without_actual_clock_and_scale_retains_forward_but_withholds_fit(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("source without clock/scale was calibrated")

    monkeypatch.setattr("pipeline_integrity.friction_study.fit_parameters", forbidden)
    q, c = make_case(reviewed=True, calibration=fit_request())
    record = evaluate_friction_study(q, c, source_root=ROOT)
    assert record["status"] == "needs_data" and len(record["forward_rows"]) == 6
    assert record["calibration_record"] is None and not record["clock_invented"]


def test_external_clock_and_descriptive_scale_delegate_exact_split_to_shared_owner(monkeypatch):
    def capture(observations, request, context, predict):
        assert observations.time_s == (0.0, 1.0, 2.0, 3.0, 4.0, 5.0)
        assert request.fit_end_s < request.validation_start_s
        assert observations.channels[0].scale.unit == "m"
        prediction = predict({"roughness_m": 1e-5}, (0.0, 1.0, 2.0), context)
        assert prediction.diagnostics["source_rows"] == [2, 3, 4]
        return {"status": "budget_exhausted", "fitted_parameters": None}

    monkeypatch.setattr("pipeline_integrity.friction_study.fit_parameters", capture)
    q, c = make_case(
        reviewed=True,
        calibration=fit_request(maximum_model_evaluations=1),
        calibration_evidence_id="scale_fixture",
        source_row_time_s=(0.0, 1.0, 2.0, 3.0, 4.0, 5.0),
        clock_evidence_id="external_clock_fixture",
        external_measurement_clock_reviewed=True,
        residual_scale_m=ResidualScale(value=0.01, unit="m", source_id="scale_fixture"),
        calibration_error_model="deterministic_scaled_residuals_only",
        scale_bounds_and_same_apparatus_split_reviewed=True,
    )
    record = evaluate_friction_study(q, c, source_root=ROOT)
    assert record["calibration_status"] == "budget_exhausted" and record["status"] == "needs_data"
    assert not record["independent_experiment_validation"] and not record["covariance_inferred"]


def test_parameter_bounds_and_reference_units_are_not_silently_clipped():
    with pytest.raises(ValueError):
        make_case(
            reviewed=True,
            calibration=fit_request(
                parameters=(FitParameter(id="roughness_m", unit="1", lower=0, upper=0.001, initial=1e-5),)
            ),
        )
    with pytest.raises(ValueError):
        make_case(reviewed=True, roughness_m=0.1)
    with pytest.raises(ValueError):
        make_case(source_row_time_s=(0.0,) * 6)


def test_source_event_and_rehashed_qualification_mutations_fail_replay():
    q, c = make_case()
    with pytest.raises(ValueError):
        evaluate_friction_study(q, c.model_copy(update={"scenario_id": "other"}), source_root=ROOT)
    with pytest.raises(ValueError):
        changed, changed_context = make_case(source_record_digest="0" * 64)
        evaluate_friction_study(changed, changed_context, source_root=ROOT)
    record = deepcopy(evaluate_friction_study(q, c, source_root=ROOT))
    record["published_fitted_parameters_used_as_truth"] = True
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    with pytest.raises(ValueError):
        verify_friction_study_record(record, source_root=ROOT, request=q, context=c)


def point_fit_request(q, **changes):
    from pipeline_integrity.friction_study import build_friction_operating_points
    from pipeline_integrity.inverse import OperatingPointFitRequest

    points = build_friction_operating_points(q, read_friction_observations(ROOT))
    values = dict(
        parameters=(FitParameter(id="roughness_m", unit="m", lower=0, upper=1e-4, initial=1e-5),),
        provider_id="friction_hydraulic_loss",
        provider_version="1",
        training_point_ids=tuple(p.id for p in points[:4]),
        heldout_point_ids=tuple(p.id for p in points[4:]),
    )
    values.update(changes)
    return OperatingPointFitRequest(**values)


def test_point_axis_reuses_original_six_row_states_without_measurement_clock(monkeypatch):
    def capture(observations, request, context, predict):
        assert not hasattr(observations, "time_s") and not hasattr(observations, "clock_id")
        assert len(observations.points) == 6
        assert len({point.group_id for point in observations.points}) == 1
        assert len(request.training_point_ids) == 4 and len(request.heldout_point_ids) == 2
        point = observations.points[0]
        assert point.state_units["kinematic_viscosity_m2_s"] == "m^2/s"
        assert point.id.endswith(":row:2")
        p = predict({"roughness_m": 1e-5}, observations.points[:4], context)
        assert p.point_ids == request.training_point_ids
        assert p.point_digests == {v.id: digest(v) for v in observations.points[:4]}
        assert p.diagnostics["source_rows"] == [2, 3, 4, 5]
        return {"status": "budget_exhausted", "fitted_parameters": None}

    monkeypatch.setattr("pipeline_integrity.friction_study.fit_operating_points", capture)
    q, _ = make_case(reviewed=True)
    q, c = make_case(
        reviewed=True,
        operating_point_calibration=point_fit_request(q),
        calibration_evidence_id="scale_fixture",
        residual_scale_m=ResidualScale(value=0.01, unit="m", source_id="scale_fixture"),
        calibration_error_model="deterministic_scaled_residuals_only",
        scale_bounds_and_same_apparatus_split_reviewed=True,
    )
    result = evaluate_friction_study(q, c, source_root=ROOT)
    assert result["calibration_axis"] == "operating_points"
    assert result["calibration_status"] == "budget_exhausted"
    assert q.source_row_time_s is None and not q.external_measurement_clock_reviewed
    assert not result["clock_invented"] and not result["independent_experiment_validation"]


def test_point_provider_rejects_relabelled_source_operating_state(monkeypatch):
    def capture(observations, request, context, predict):
        point = observations.points[0]
        changed = point.model_copy(update={"state": {**point.state, "volume_flow_m3_s": 0.123}})
        with pytest.raises(ValueError, match="identity/state/units"):
            predict({"roughness_m": 1e-5}, (changed,), context)
        return {"status": "budget_exhausted", "fitted_parameters": None}

    monkeypatch.setattr("pipeline_integrity.friction_study.fit_operating_points", capture)
    q, _ = make_case(reviewed=True)
    q, c = make_case(
        reviewed=True,
        operating_point_calibration=point_fit_request(q),
        calibration_evidence_id="scale_fixture",
        residual_scale_m=ResidualScale(value=0.01, unit="m", source_id="scale_fixture"),
        calibration_error_model="deterministic_scaled_residuals_only",
        scale_bounds_and_same_apparatus_split_reviewed=True,
    )
    assert evaluate_friction_study(q, c, source_root=ROOT)["calibration_status"] == "budget_exhausted"


def test_point_fit_keeps_scale_and_physical_boundary_review_gates(monkeypatch):
    monkeypatch.setattr(
        "pipeline_integrity.friction_study.fit_operating_points",
        lambda *a, **k: pytest.fail("unreviewed operating-point scale invoked inverse owner"),
    )
    q, _ = make_case(reviewed=True)
    q, c = make_case(reviewed=True, operating_point_calibration=point_fit_request(q))
    result = evaluate_friction_study(q, c, source_root=ROOT)
    assert result["status"] == "needs_data" and result["calibration_record"] is None
    assert len(result["forward_rows"]) == 6


def test_actual_time_and_point_fit_requests_are_mutually_exclusive():
    q, _ = make_case(reviewed=True)
    with pytest.raises(ValueError, match="choose one"):
        make_case(reviewed=True, calibration=fit_request(), operating_point_calibration=point_fit_request(q))


def test_density_free_head_forward_has_no_invented_viscosity_pressure_or_power(monkeypatch):
    monkeypatch.setattr(
        "pipeline_integrity.create_registry",
        lambda *a, **k: pytest.fail("density-free source study invoked pressure registry"),
    )
    q, c = make_case(reviewed=True, density_kg_m3_by_source_row=None)
    result = evaluate_friction_study(q, c, source_root=ROOT)
    assert result["status"] == "computed_diagnostic"
    for row in result["forward_rows"]:
        assert row["head_native"]["status"] == "computed"
        assert (
            row["predicted_pressure_head_difference_m"]
            == row["head_native"]["values"]["required_inlet_minus_outlet_head_m"]
        )
        assert row["native"] is None and not row["density_dependent_quantities_computed"]
        for key in (
            "density_kg_m3",
            "dynamic_viscosity_Pa_s",
            "pressure_difference_Pa",
            "mass_flow_kg_s",
            "pressure_work_rate_W",
            "potential_energy_rate_W",
            "dissipated_power_W",
        ):
            assert row[key] is None
    assert verify_friction_study_record(result, source_root=ROOT)


def test_density_independent_head_matches_supplied_density_pressure_mapping():
    q, c = make_case(reviewed=True)
    supplied = evaluate_friction_study(q, c, source_root=ROOT)
    q, c = make_case(reviewed=True, density_kg_m3_by_source_row=None)
    independent = evaluate_friction_study(q, c, source_root=ROOT)
    assert independent["status"] == supplied["status"] == "computed_diagnostic"
    for a, b in zip(supplied["forward_rows"], independent["forward_rows"], strict=True):
        assert a["predicted_pressure_head_difference_m"] == b["predicted_pressure_head_difference_m"]
        assert a["pressure_to_head_crosscheck_residual_m"] == pytest.approx(0.0, abs=1e-10)
        assert a["pressure_work_rate_W"] is not None


def test_point_inverse_uses_source_kinematic_state_without_density(monkeypatch):
    def capture(observations, request, context, predict):
        assert all(point.state["density_kg_m3"] is None for point in observations.points)
        p = predict({"roughness_m": 1e-5}, observations.points[:4], context)
        assert p.status == "computed" and p.units == {"pressure_head_difference_m": "m"}
        assert p.point_ids == request.training_point_ids
        return {"status": "budget_exhausted", "fitted_parameters": None}

    monkeypatch.setattr("pipeline_integrity.friction_study.fit_operating_points", capture)
    q, _ = make_case(reviewed=True, density_kg_m3_by_source_row=None)
    q, c = make_case(
        reviewed=True,
        density_kg_m3_by_source_row=None,
        operating_point_calibration=point_fit_request(q),
        calibration_evidence_id="scale_fixture",
        residual_scale_m=ResidualScale(value=0.01, unit="m", source_id="scale_fixture"),
        calibration_error_model="deterministic_scaled_residuals_only",
        scale_bounds_and_same_apparatus_split_reviewed=True,
    )
    result = evaluate_friction_study(q, c, source_root=ROOT)
    assert result["calibration_status"] == "budget_exhausted" and not result["clock_invented"]
    assert result["operating_points"][0]["state"]["density_kg_m3"] is None


def joint_metric_case(*, applicability_reviewed=True):
    from pipeline_integrity.friction_study import build_friction_operating_points
    from pipeline_integrity.inverse import OperatingPointResidualMetric, ResidualRow

    q, _ = make_case(reviewed=True, density_kg_m3_by_source_row=None)
    points = build_friction_operating_points(q, read_friction_observations(ROOT))
    ordered = (points[2], points[0], points[1], *points[3:])
    rows = tuple(
        ResidualRow(point_id=p.id, channel_id="pressure_head_difference_m", unit="m") for p in ordered
    )
    matrix = tuple(
        tuple(1.0 if i == j else 0.25 if {i, j} == {0, 1} else 0.0 for j in range(6)) for i in range(6)
    )
    source_sha = digest({"authored_joint_scaling": matrix, "rows": rows})
    metric = OperatingPointResidualMetric(
        kind="residual_scale_matrix",
        rows=rows,
        matrix=matrix,
        source_sha256=source_sha,
        evidence_id="scale_fixture",
        definition_id="authored_correlated_scaling_not_measured_covariance",
        applicability_reviewed=applicability_reviewed,
    )
    recipe = point_fit_request(q, residual_metric=metric)
    q, c = make_case(
        reviewed=True,
        density_kg_m3_by_source_row=None,
        operating_point_calibration=recipe,
        calibration_evidence_id="scale_fixture",
        residual_scale_m=ResidualScale(value=777.0, unit="m", source_id="scale_fixture"),
        calibration_error_model="explicit_joint_residual_metric",
        scale_bounds_and_same_apparatus_split_reviewed=True,
    )
    c = c.model_copy(
        update={
            "evidence": tuple(
                e.model_copy(update={"sha256": source_sha}) if e.id == "scale_fixture" else e
                for e in c.evidence
            )
        }
    )
    return q, c, metric


def test_explicit_joint_metric_preserves_source_row_order_units_and_core_contract(monkeypatch):
    q, c, metric = joint_metric_case()

    def capture(observations, request, context, predict):
        assert request.residual_metric == metric
        assert request.residual_metric.rows[0].point_id == observations.points[2].id
        assert observations.channels[0].scale.value == 777.0
        assert request.residual_metric.rows[0].unit == "m"
        assert all(point.state["density_kg_m3"] is None for point in observations.points)
        result = predict({"roughness_m": 1e-5}, observations.points[:4], context)
        assert result.status == "computed"
        return {"status": "budget_exhausted", "fitted_parameters": None}

    monkeypatch.setattr("pipeline_integrity.friction_study.fit_operating_points", capture)
    result = evaluate_friction_study(q, c, source_root=ROOT)
    assert result["joint_residual_metric_supplied"] is True
    assert result["residual_metric_kind"] == "residual_scale_matrix"
    assert result["calibration_status"] == "budget_exhausted"
    assert "unit metadata only" in result["scalar_residual_scale_role"]
    assert not result["covariance_inferred"] and not result["independent_experiment_validation"]


def test_joint_metric_source_hash_cannot_conflict_with_actual_evidence():
    q, c, _ = joint_metric_case()
    c = c.model_copy(
        update={
            "evidence": tuple(
                e.model_copy(update={"sha256": "f" * 64}) if e.id == "scale_fixture" else e
                for e in c.evidence
            )
        }
    )
    with pytest.raises(ValueError, match="metric conflicts"):
        evaluate_friction_study(q, c, source_root=ROOT)


def test_unreviewed_joint_metric_retains_head_forward_and_withholds_inverse(monkeypatch):
    q, c, _ = joint_metric_case(applicability_reviewed=False)
    monkeypatch.setattr(
        "pipeline_integrity.friction_study.fit_operating_points",
        lambda *a, **k: pytest.fail("unreviewed joint metric reached inverse owner"),
    )
    result = evaluate_friction_study(q, c, source_root=ROOT)
    assert result["status"] == "needs_data" and len(result["forward_rows"]) == 6
    assert result["calibration_record"] is None


def test_joint_metric_does_not_silently_reinterpret_scalar_error_model():
    q, _, _ = joint_metric_case()
    with pytest.raises(ValueError, match="silently replace"):
        FrictionStudyRequest.model_validate(
            q.model_copy(
                update={"calibration_error_model": "deterministic_scaled_residuals_only"}
            ).model_dump(mode="python")
        )


def test_requested_joint_mode_without_metric_is_not_diagonalized(monkeypatch):
    q, _ = make_case(reviewed=True, density_kg_m3_by_source_row=None)
    q, c = make_case(
        reviewed=True,
        density_kg_m3_by_source_row=None,
        operating_point_calibration=point_fit_request(q),
        calibration_evidence_id="scale_fixture",
        residual_scale_m=ResidualScale(value=0.01, unit="m", source_id="scale_fixture"),
        calibration_error_model="explicit_joint_residual_metric",
        scale_bounds_and_same_apparatus_split_reviewed=True,
    )
    monkeypatch.setattr(
        "pipeline_integrity.friction_study.fit_operating_points",
        lambda *a, **k: pytest.fail("missing joint metric was replaced by an implicit independent model"),
    )
    result = evaluate_friction_study(q, c, source_root=ROOT)
    assert result["status"] == "needs_data" and not result["joint_residual_metric_supplied"]
    assert result["calibration_record"] is None and not result["covariance_inferred"]


def test_unknown_joint_metric_source_hash_is_withheld_not_inferred(monkeypatch):
    q, c, _ = joint_metric_case()
    c = c.model_copy(
        update={
            "evidence": tuple(
                e.model_copy(update={"sha256": None}) if e.id == "scale_fixture" else e for e in c.evidence
            )
        }
    )
    monkeypatch.setattr(
        "pipeline_integrity.friction_study.fit_operating_points",
        lambda *a, **k: pytest.fail("unknown metric source hash reached inverse owner"),
    )
    result = evaluate_friction_study(q, c, source_root=ROOT)
    assert result["status"] == "needs_data" and result["calibration_record"] is None
    assert not result["covariance_inferred"] and len(result["forward_rows"]) == 6
