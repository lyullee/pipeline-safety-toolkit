"""Declared currency and energy budgets without hidden financial or emissions factors."""

import math

from pydantic import Field, model_validator

from ..contracts import Calculation, Contract, Nonnegative, Positive


class DiscountedCashflow(Contract):
    cashflows: tuple[float, ...] = Field(min_length=1)
    discount_rate_per_period: float = Field(gt=-1)
    period_duration_s: Positive
    currency: str = Field(min_length=1)
    price_year: int = Field(ge=1900)
    times_s: tuple[Nonnegative, ...] | None = None

    @model_validator(mode="after")
    def timing(self):
        if self.times_s is not None and len(self.times_s) != len(self.cashflows):
            raise ValueError("explicit cashflow times must align with all amounts")
        return self


def discounted_cashflow(p: DiscountedCashflow):
    periods = range(len(p.cashflows)) if p.times_s is None else (t / p.period_duration_s for t in p.times_s)
    discounted = []
    try:
        for value, period in zip(p.cashflows, periods, strict=True):
            if value == 0 or p.discount_rate_per_period == 0:
                result = value
            else:
                exponent = -math.log1p(p.discount_rate_per_period) * period
                result = value * math.exp(exponent)
            if not math.isfinite(result) or (result == 0 and value != 0):
                raise ArithmeticError("discounted amount exceeds floating-point range")
            discounted.append(result)
        total, undiscounted = math.fsum(discounted), math.fsum(p.cashflows)
        if not math.isfinite(total) or not math.isfinite(undiscounted):
            raise ArithmeticError("cashflow sum exceeds floating-point range")
    except (ArithmeticError, OverflowError):
        return Calculation(
            values={},
            status="out_of_scope",
            notes=("Cashflow arithmetic exceeded finite floating-point range.",),
        )
    return Calculation(
        values={
            "net_present_value": total,
            "discounted_cashflows": discounted,
            "undiscounted_sum": undiscounted,
        },
        notes=(
            "Absent explicit times, the first cashflow is at time zero in uniform periods; no currency conversion or escalation is inferred.",
        ),
    )


class EnergyBudget(Contract):
    electrical_power_w: Nonnegative
    operating_duration_s: Nonnegative
    price_per_kwh: float | None
    emission_kg_co2e_per_kwh: Nonnegative | None
    currency: str = Field(min_length=1)
    factor_source: str = Field(min_length=1)


def energy_budget(p: EnergyBudget):
    energy = p.electrical_power_w * p.operating_duration_s / 3.6e6
    cost = None if p.price_per_kwh is None else energy * p.price_per_kwh
    emissions = None if p.emission_kg_co2e_per_kwh is None else energy * p.emission_kg_co2e_per_kwh
    if any(value is not None and not math.isfinite(value) for value in (energy, cost, emissions)):
        return Calculation(
            values={}, status="out_of_scope", notes=("Energy valuation exceeded finite arithmetic.",)
        )
    return Calculation(
        values={
            "energy_kwh": energy,
            "energy_cost": cost,
            "declared_emissions_kg_co2e": emissions,
        },
        status="needs_data" if p.price_per_kwh is None or p.emission_kg_co2e_per_kwh is None else "computed",
        notes=("A declared operational-energy inventory, not a complete lifecycle assessment.",),
    )
