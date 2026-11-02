"""Bounded physical/accounting qualification for cashflow and operational energy."""

import math

import pytest

from pipeline_integrity import evaluate
from pipeline_integrity.contracts import RunContext


CTX = RunContext(asset_id="economic-reference", scenario_id="synthetic", clock_id="declared", origin="synthetic")


@pytest.mark.parametrize("flows,rate,period,expected", [
    ((-100.0, 60.0, 60.0), 0.1, 1.0, -100.0 + 60 / 1.1 + 60 / 1.21),
    ((0.0, 25.0), 0.0, 86400.0, 25.0),
    ((-5.0, 5.0), 0.25, 10.0, -5.0 + 4.0),
])
def test_cashflow_matches_independent_discrete_discount_sum(flows, rate, period, expected):
    result = evaluate("economics.cashflow", {
        "cashflows": flows, "discount_rate_per_period": rate,
        "period_duration_s": period, "currency": "USD", "price_year": 2025,
    }, CTX)
    assert result.status == "computed"
    assert result.values["net_present_value"] == pytest.approx(expected, rel=1e-14, abs=1e-14)
    assert result.values["undiscounted_sum"] == pytest.approx(math.fsum(flows))
    assert result.values["discounted_cashflows"][0] == flows[0]


def test_cashflow_explicit_times_are_invariant_to_time_unit_scale():
    common = {"cashflows": (-100.0, 121.0), "discount_rate_per_period": 0.1,
              "currency": "USD", "price_year": 2025}
    seconds = evaluate("economics.cashflow", {**common, "period_duration_s": 86400.0,
                       "times_s": (0.0, 172800.0)}, CTX)
    hours = evaluate("economics.cashflow", {**common, "period_duration_s": 24.0,
                     "times_s": (0.0, 48.0)}, CTX)
    assert seconds.values["net_present_value"] == pytest.approx(0.0, abs=1e-14)
    assert hours.values["net_present_value"] == pytest.approx(seconds.values["net_present_value"], abs=1e-14)


@pytest.mark.parametrize("power,duration,price,factor", [
    (1000.0, 3600.0, 0.2, 0.4), (0.0, 900.0, 7.0, 1.2), (2500.0, 7200.0, 0.0, 0.0),
])
def test_energy_cost_emissions_and_unit_conversion(power, duration, price, factor):
    result = evaluate("economics.energy", {
        "electrical_power_w": power, "operating_duration_s": duration,
        "price_per_kwh": price, "emission_kg_co2e_per_kwh": factor,
        "currency": "USD", "factor_source": "declared synthetic factor",
    }, CTX)
    energy = power * duration / 3.6e6
    assert result.status == "computed"
    assert result.values["energy_kwh"] == pytest.approx(energy)
    assert result.values["energy_cost"] == pytest.approx(energy * price)
    assert result.values["declared_emissions_kg_co2e"] == pytest.approx(energy * factor)


def test_energy_missing_factors_withholds_valuation_without_losing_inventory():
    result = evaluate("economics.energy", {
        "electrical_power_w": 500.0, "operating_duration_s": 7200.0,
        "price_per_kwh": None, "emission_kg_co2e_per_kwh": None,
        "currency": "USD", "factor_source": "not supplied",
    }, CTX)
    assert result.status == "needs_data"
    assert result.values["energy_kwh"] == pytest.approx(1.0)
    assert result.values["energy_cost"] is None
    assert result.values["declared_emissions_kg_co2e"] is None
