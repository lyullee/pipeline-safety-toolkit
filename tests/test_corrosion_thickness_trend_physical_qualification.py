"""Bounded wall-thickness trend checks against independent regression identities."""

import math
import runpy

import pytest
from pydantic import ValidationError

from pipeline_integrity.adapters import MaterialClient


_REQUEST, _CONTEXT = runpy.run_path("examples/pressure_fatigue.py")["make_case"]()
_CLIENT = MaterialClient()
_LOCATION = _REQUEST.history.location_id
_YEAR_S = 365.25 * 24.0 * 3600.0


def _evaluate(time_years, thickness_mm, method):
    return _CLIENT.evaluate(
        "corrosion.thickness_trend",
        {
            "time_s": [value * _YEAR_S for value in time_years],
            "thickness_m": [value / 1000.0 for value in thickness_mm],
            "method": method,
        },
        _CONTEXT,
        location_id=_LOCATION,
    )


def test_ols_exact_linear_wall_loss_matches_closed_form():
    result = _evaluate([0.0, 1.0, 2.0, 3.0], [12.0, 11.0, 10.0, 9.0], "ols")

    assert result.status == "computed"
    values = result.payload["values"]
    assert values["rate_m_s"] == pytest.approx(1e-3 / _YEAR_S, rel=2e-14)
    assert values["thickness_at_first_time_m"] == pytest.approx(0.012, abs=1e-15)
    assert values["residual_rmse_m"] == pytest.approx(0.0, abs=1e-15)
    assert values["rate_interval_m_s"] is None


def test_ols_matches_independent_centered_least_squares_with_offset_time():
    years = [4.0, 5.5, 9.0, 11.0]
    mm = [15.0, 14.0, 11.5, 10.0]
    result = _evaluate(years, mm, "ols")

    x = [year - years[0] for year in years]
    y = mm
    xbar = sum(x) / len(x)
    ybar = sum(y) / len(y)
    slope_mm_year = sum((a - xbar) * (b - ybar) for a, b in zip(x, y)) / sum(
        (a - xbar) ** 2 for a in x
    )
    intercept_mm = ybar - slope_mm_year * xbar
    residuals_mm = [b - (intercept_mm + slope_mm_year * a) for a, b in zip(x, y)]
    expected_rmse_m = math.sqrt(sum(r * r for r in residuals_mm) / len(x)) / 1000.0
    values = result.payload["values"]

    assert result.status == "computed"
    assert values["rate_m_s"] == pytest.approx(-slope_mm_year / 1000.0 / _YEAR_S, rel=2e-13)
    assert values["thickness_at_first_time_m"] == pytest.approx(intercept_mm / 1000.0, rel=2e-13)
    assert values["residual_rmse_m"] == pytest.approx(expected_rmse_m, rel=2e-12, abs=1e-15)


def test_theil_sen_is_resistant_to_a_single_high_outlier():
    result = _evaluate([0.0, 1.0, 2.0, 3.0], [10.0, 9.0, 100.0, 7.0], "theil_sen")

    values = result.payload["values"]
    assert result.status == "computed"
    assert values["rate_m_s"] == pytest.approx(1e-3 / _YEAR_S, rel=2e-14)
    assert values["thickness_at_first_time_m"] == pytest.approx(0.010, abs=1e-15)
    assert len(values["rate_interval_m_s"]) == 2
    assert values["rate_interval_m_s"][0] <= values["rate_m_s"]
    assert values["rate_m_s"] <= values["rate_interval_m_s"][1]


def test_increasing_thickness_reports_zero_loss_and_a_note():
    result = _evaluate([0.0, 1.0, 2.0], [10.0, 10.5, 11.0], "ols")

    assert result.status == "computed"
    assert result.payload["values"]["rate_m_s"] == pytest.approx(-0.5e-3 / _YEAR_S, rel=2e-14)
    assert any("increase" in note.lower() for note in result.payload["notes"])


def test_nonincreasing_time_and_misaligned_observations_are_rejected():
    for parameters in (
        {"time_s": [0.0, 1.0, 1.0], "thickness_m": [0.01, 0.009, 0.008], "method": "ols"},
        {"time_s": [0.0, 1.0, 2.0], "thickness_m": [0.01, 0.009], "method": "ols"},
    ):
        with pytest.raises(ValidationError):
            _CLIENT.evaluate("corrosion.thickness_trend", parameters, _CONTEXT, location_id=_LOCATION)
