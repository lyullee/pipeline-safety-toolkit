"""Window quantities, correlated clocks, sample meaning and reusable energy integration."""

import copy
import math
import runpy
from pathlib import Path

import numpy as np
import pytest
from pydantic import ValidationError

from pipeline_integrity.contracts import digest
from pipeline_integrity.quadrature import integrate_linear_interval
from pipeline_integrity.totalization import (
    boundary_metadata,
    totalize_measurements,
    verify_totalization_record,
)

MAKE = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/totalization.py"))["make_case"]


def recontext(request, context):
    return context.model_copy(update={"metadata": {"totalization_boundary": boundary_metadata(request)}})


@pytest.mark.parametrize(
    "meaning,net,variance",
    [("point_linear", 10.0, 0.0374), ("interval_mean", 10.0, 0.0524), ("cumulative_counter", 5.0, 0.02)],
)
def test_independent_analytic_integrals_and_full_reading_clock_uncertainty(meaning, net, variance):
    request, context = MAKE(meaning=meaning, reviewed=True)
    result = totalize_measurements(request, context)
    assert result["status"] == "computed"
    assert result["values"]["complete_net_amount"] == net
    assert result["values"]["complete_window_duration_s"] == 3.0
    assert result["intervals"][0]["start_s"] == 20.0
    assert result["uncertainty"]["output_covariance"][0][0] == pytest.approx(variance)
    assert verify_totalization_record(result, request, context)["valid"]


def test_common_clock_offset_covariance_cancels_but_individual_clock_error_does_not():
    request, context = MAKE(reviewed=True)
    matrix = [list(row) for row in request.covariance.values]
    for i in range(3, 6):
        for j in range(3, 6):
            matrix[i][j] = 0.0001
    common = request.covariance.model_copy(update={"values": tuple(map(tuple, matrix))})
    result = totalize_measurements(request.model_copy(update={"covariance": common}), context)
    assert result["status"] == "computed"
    assert result["uncertainty"]["output_covariance"][0][0] == pytest.approx(0.035)
    assert result["uncertainty"]["outputs"]["window_duration"]["standard_uncertainty"] == 0.0


def test_cross_axis_reading_time_covariance_is_not_dropped():
    request, context = MAKE(reviewed=True)
    matrix = np.asarray(request.covariance.values).copy()
    matrix[0, 3] = matrix[3, 0] = 0.0003
    covariance = request.covariance.model_copy(update={"values": tuple(map(tuple, matrix))})
    result = totalize_measurements(request.model_copy(update={"covariance": covariance}), context)
    assert result["uncertainty"]["output_covariance"][0][0] == pytest.approx(0.0374 - 0.0006)


@pytest.mark.parametrize("meaning", ["point_linear", "interval_mean", "cumulative_counter"])
def test_different_valid_sample_meanings_are_never_automatically_interchanged(meaning):
    request, context = MAKE(meaning=meaning, reviewed=True)
    changed = "interval_mean" if meaning != "interval_mean" else "point_linear"
    with pytest.raises(ValidationError):
        totalize_measurements(request.model_copy(update={"sample_meaning": changed}), context)


def test_signed_linear_zero_crossing_keeps_positive_negative_and_uncertain_net():
    request, context = MAKE(reviewed=True)
    readings = tuple(
        v.model_copy(update={"estimate": amount})
        for v, amount in zip(request.readings[:2], (2.0, -2.0), strict=True)
    )
    times = (request.times[0], request.times[1].model_copy(update={"estimate": 2.0}))
    request = request.model_copy(update={"readings": readings, "times": times, "covariance": None})
    result = totalize_measurements(request, context)
    assert result["status"] == "needs_data"
    assert result["values"]["complete_net_amount"] == 0
    assert result["values"]["positive_model_amount"] == result["values"]["negative_model_amount"] == 1.0
    assert not result["uncertainty"]["outputs"]


@pytest.mark.parametrize("meaning", ["point_linear", "interval_mean", "cumulative_counter"])
def test_missing_covariance_keeps_nominal_quantity_without_exact_uncertainty(meaning):
    request, context = MAKE(meaning=meaning, reviewed=True)
    result = totalize_measurements(request.model_copy(update={"covariance": None}), context)
    assert result["status"] == "needs_data" and result["values"]["complete_net_amount"] is not None
    assert result["uncertainty"]["status"] == "needs_data"


@pytest.mark.parametrize("change", ["missing", "unknown_quality", "gap"])
def test_missing_intervals_are_not_filled_or_reported_as_complete_total(change):
    request, context = MAKE(reviewed=True)
    if change == "gap":
        request = request.model_copy(update={"maximum_interval_s": 1.5})
    else:
        values = (
            request.readings[0],
            request.readings[1],
            request.readings[2].model_copy(
                update={"estimate": None if change == "missing" else 5.0, "valid": None}
            ),
        )
        request = request.model_copy(update={"readings": values})
    result = totalize_measurements(request, context)
    assert result["status"] == "needs_data"
    assert result["values"]["complete_net_amount"] is None
    assert result["values"]["known_interval_net_amount"] == 2.0
    assert result["skipped_interval_indices"] == [1]
    assert not result["missing_intervals_filled"]


def test_explicit_invalid_reading_is_not_unknown_or_zero():
    request, context = MAKE(reviewed=True)
    readings = (
        request.readings[0].model_copy(update={"valid": False, "estimate": -10.0}),
        *request.readings[1:],
    )
    result = totalize_measurements(request.model_copy(update={"readings": readings}), context)
    assert result["status"] == "out_of_scope" and result["values"] is None
    assert result["request"]["readings"][0]["estimate"] == -10.0


def test_counter_decrease_requires_signed_flow_and_does_not_infer_gross_excursions():
    request, context = MAKE(meaning="cumulative_counter", reviewed=True)
    readings = tuple(
        v.model_copy(update={"estimate": p})
        for v, p in zip(request.readings, (10.0, 12.0, 11.0), strict=True)
    )
    request = request.model_copy(update={"readings": readings, "uncertainty_required": False})
    result = totalize_measurements(request, context)
    assert result["values"]["complete_net_amount"] == 1.0
    assert result["values"]["positive_model_amount"] == 2.0
    assert result["values"]["negative_model_amount"] == 1.0
    assert "not_unobserved_gross_flow" in result["values"]["directional_semantics"]
    assert (
        totalize_measurements(request.model_copy(update={"sign_convention": "nonnegative"}), context)[
            "status"
        ]
        == "out_of_scope"
    )


@pytest.mark.parametrize("change", ["ports", "basis", "clock", "reference", "source_context", "nan"])
def test_invalid_mapping_basis_and_cloned_nonfinite_are_rejected(change):
    request, context = MAKE(reviewed=True)
    if change == "ports":
        request = request.model_copy(update={"nominal_positive_target_port": "inlet"})
    elif change == "basis":
        request = request.model_copy(
            update={"output_basis": request.output_basis.model_copy(update={"unit": "m^3"})}
        )
    elif change == "clock":
        request = request.model_copy(update={"clock_offset_s": -1.0})
    elif change == "reference":
        request = request.model_copy(
            update={
                "readings": (
                    request.readings[0].model_copy(
                        update={
                            "basis": request.readings[0].basis.model_copy(
                                update={"definition_id": "other-fluid-basis"}
                            )
                        }
                    ),
                    *request.readings[1:],
                )
            }
        )
    elif change == "source_context":
        context = context.model_copy(update={"clock_id": "other"})
    else:
        request = request.model_copy(
            update={
                "readings": (
                    request.readings[0].model_copy(update={"estimate": math.nan}),
                    *request.readings[1:],
                )
            }
        )
    with pytest.raises(ValueError):
        totalize_measurements(request, context)


@pytest.mark.parametrize("field", ["integral", "interval", "uncertainty", "clock", "source", "certification"])
def test_rehashed_record_mutations_are_reconstructed(field):
    request, context = MAKE(reviewed=True)
    result = copy.deepcopy(totalize_measurements(request, context))
    if field == "integral":
        result["values"]["complete_net_amount"] += 1
    elif field == "interval":
        result["intervals"][0]["positive_model_amount"] += 1
    elif field == "uncertainty":
        result["uncertainty"]["outputs"]["net_amount"]["standard_uncertainty"] *= 0.5
    elif field == "clock":
        result["intervals"][0]["start_s"] += 1
    elif field == "source":
        result["quadrature_sha256"] = "a" * 64
    else:
        result["custody_transfer_certified"] = True
    result["record_digest"] = digest({k: v for k, v in result.items() if k != "record_digest"})
    assert not verify_totalization_record(result, request, context)["valid"]


@pytest.mark.parametrize(
    "first,last,duration,positive,negative",
    [
        (2.0, -2.0, 2.0, 1.0, 1.0),
        (-2.0, 2.0, 2.0, 1.0, 1.0),
        (1e308, 1e308, 1e-308, 1.0, 0.0),
        (5e-324, 5e-324, 1e308, 5e-324 * 1e308, 0.0),
    ],
)
def test_shared_linear_quadrature_independent_geometry_and_scaling(first, last, duration, positive, negative):
    actual = integrate_linear_interval(first, last, duration)
    assert actual == pytest.approx((positive, negative))


def test_quadrature_nonzero_underflow_is_not_reported_as_exact_zero():
    with pytest.raises(ValueError, match="floating"):
        integrate_linear_interval(1e-320, 1e-320, 1e-20)


@pytest.mark.parametrize("first,last,expected", [(5e-324, 5e-324, 5e-324), (5e-324, 1.0, 0.5)])
def test_representable_interval_is_not_rejected_for_unrepresentable_endpoint_halves(first, last, expected):
    assert integrate_linear_interval(first, last, 1.0) == (expected, 0.0)


def test_offset_cannot_turn_nonzero_intervals_into_equal_common_clock_times():
    request, context = MAKE(reviewed=True)
    request = request.model_copy(update={"clock_offset_s": 1e20, "uncertainty_required": False})
    with pytest.raises(ValueError, match="resolution"):
        totalize_measurements(request, recontext(request, context))


def test_noncollapsed_large_clock_offset_requires_explicit_representation_tolerance():
    request, context = MAKE(reviewed=True)
    times = tuple(
        v.model_copy(update={"estimate": time})
        for v, time in zip(request.times, (0.0, 0.001, 0.002), strict=True)
    )
    request = request.model_copy(
        update={"times": times, "clock_offset_s": 1e9, "uncertainty_required": False}
    )
    with pytest.raises(ValueError, match="duration"):
        totalize_measurements(request, recontext(request, context))
    request = request.model_copy(update={"clock_representation_tolerance_s": 1e-6})
    result = totalize_measurements(request, recontext(request, context))
    assert result["status"] == "computed"
    assert result["values"]["complete_net_amount"] == pytest.approx(0.006)
    assert 0 < result["values"]["maximum_clock_representation_error_s"] <= 1e-6


def test_unreviewed_default_keeps_no_quantity_or_uncertainty():
    request, context = MAKE()
    result = totalize_measurements(request, context)
    assert result["status"] == "needs_data" and result["values"] is None
    assert verify_totalization_record(result)["valid"]


def test_totalization_jacobian_matches_central_difference_of_declared_measurement_model():
    request, context = MAKE(reviewed=True)
    result = totalize_measurements(request, context)
    jacobian = np.asarray(result["uncertainty"]["request"]["linearization"]["jacobian"])
    source = (*request.readings, *request.times)
    numerical = []
    for i in range(len(source)):
        columns = []
        for direction in (-1, 1):
            values = list(source)
            values[i] = values[i].model_copy(update={"estimate": values[i].estimate + direction * 1e-5})
            candidate = request.model_copy(
                update={
                    "readings": tuple(values[:3]),
                    "times": tuple(values[3:]),
                    "uncertainty_required": False,
                    "clock_offset_s": 21.0,
                }
            )
            ctx = recontext(candidate, context)
            if i == 3 and direction < 0:
                # Shift the entire coordinate origin so this remains an elapsed clock.
                candidate = candidate.model_copy(
                    update={
                        "times": tuple(
                            v.model_copy(update={"estimate": v.estimate + 1.0}) for v in candidate.times
                        )
                    }
                )
                ctx = recontext(candidate, context)
            output = totalize_measurements(candidate, ctx)["values"]
            columns.append((output["complete_net_amount"], output["complete_window_duration_s"]))
        numerical.append((np.asarray(columns[1]) - np.asarray(columns[0])) / (2e-5))
    np.testing.assert_allclose(jacobian, np.asarray(numerical).T, atol=1e-9)
