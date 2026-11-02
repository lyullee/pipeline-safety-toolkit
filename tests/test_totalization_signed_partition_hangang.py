"""Exact signed mass-flow totalization over nonuniform, zero-crossing intervals."""

import runpy
from pathlib import Path

import pytest

from pipeline_integrity.contracts import RunContext
from pipeline_integrity.metrology import MeasurementInput
from pipeline_integrity.totalization import boundary_metadata, totalize_measurements, verify_totalization_record

MAKE = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/totalization.py"))["make_case"]


def _case(clock_offset_s=20.0):
    request, context = MAKE(meaning="point_linear", reviewed=True)
    reading_basis = request.readings[0].basis
    time_basis = request.times[0].basis
    reading_evidence = request.readings[0].evidence_id
    time_evidence = request.times[0].evidence_id
    readings = tuple(
        MeasurementInput(id=f"flow_{i}", estimate=value, basis=reading_basis,
                         evidence_id=reading_evidence, valid=True)
        for i, value in enumerate((3.0, -1.0, 2.0, -2.0))
    )
    times = tuple(
        MeasurementInput(id=f"clock_{i}", estimate=value, basis=time_basis,
                         evidence_id=time_evidence, valid=True)
        for i, value in enumerate((0.0, 2.0, 5.0, 8.0))
    )
    request = request.model_copy(update={
        "readings": readings,
        "times": times,
        "clock_offset_s": clock_offset_s,
        "covariance": None,
        "uncertainty_required": False,
        "maximum_interval_s": 3.0,
    })
    context = RunContext.model_validate(context.model_copy(update={
        "metadata": {"totalization_boundary": boundary_metadata(request)}
    }))
    return request, context


def test_nonuniform_linear_flow_exactly_partitions_positive_negative_and_net_mass():
    request, context = _case()
    result = totalize_measurements(request, context)
    assert result["status"] == "computed"
    # Independent geometric triangle/trapezoid areas for samples
    # (0,3), (2,-1), (5,2), (8,-2): positive=5.75 kg,
    # reverse=2.25 kg, net=3.5 kg over the complete 8 s interval.
    assert result["values"]["positive_model_amount"] == pytest.approx(5.75, abs=1e-12)
    assert result["values"]["negative_model_amount"] == pytest.approx(2.25, abs=1e-12)
    assert result["values"]["complete_net_amount"] == pytest.approx(3.5, abs=1e-12)
    assert result["values"]["complete_window_duration_s"] == pytest.approx(8.0)
    assert sum(v["positive_model_amount"] for v in result["intervals"]) == pytest.approx(5.75)
    assert sum(v["negative_model_amount"] for v in result["intervals"]) == pytest.approx(2.25)
    assert verify_totalization_record(result, request, context)["valid"]


def test_common_clock_offset_moves_window_labels_without_changing_mass_balance():
    early_request, early_context = _case(clock_offset_s=20.0)
    late_request, late_context = _case(clock_offset_s=200.0)
    early = totalize_measurements(early_request, early_context)
    late = totalize_measurements(late_request, late_context)
    assert early["values"]["complete_net_amount"] == pytest.approx(late["values"]["complete_net_amount"])
    assert early["values"]["positive_model_amount"] == pytest.approx(late["values"]["positive_model_amount"])
    assert early["values"]["negative_model_amount"] == pytest.approx(late["values"]["negative_model_amount"])
    assert late["intervals"][0]["start_s"] - early["intervals"][0]["start_s"] == pytest.approx(180.0)
