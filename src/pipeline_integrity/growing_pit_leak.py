"""Prescribed through-wall opening growth and outward liquid inventory loss.

Geometry and its time law are inputs, rather than a corrosion prediction. Head
and discharge coefficient can vary on a caller supplied time grid. A spherical
intersection and an independently calibrated area polynomial remain explicit
choices because their quadratic coefficients need not represent the same shape.
"""
from __future__ import annotations

import math
import warnings
from decimal import Decimal, localcontext
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Fraction, Nonnegative, Positive
from .quadrature import _product_ratio


class LeakHistoryPoint(Contract):
    time_s: Nonnegative
    liquid_head_m: Nonnegative
    discharge_coefficient: Fraction


class GrowingPitLeakRequest(Contract):
    geometry: Literal['spherical_intersection', 'supplied_area_polynomial']
    history: tuple[LeakHistoryPoint, ...] = Field(min_length=2)
    density_kg_m3: Positive
    gravity_m_s2: Positive = 9.80665
    wall_thickness_m: Positive | None = None
    initial_pit_radius_m: Nonnegative | None = None
    radial_growth_m_s: Nonnegative | None = None
    area_coefficients_m2_m2_s_m2_s2: tuple[Nonnegative, Nonnegative, Nonnegative] | None = None
    absolute_volume_tolerance_m3: Positive = 1e-10
    relative_tolerance: float = Field(default=1e-8, ge=1e-13, lt=1)
    maximum_subintervals: int = Field(default=100, ge=2, le=10000)

    @model_validator(mode='after')
    def defined_geometry_and_clock(self):
        if self.history[0].time_s != 0 or any(b.time_s <= a.time_s for a, b in zip(self.history, self.history[1:])):
            raise ValueError('history starts at zero and has strictly increasing times')
        sphere = (self.wall_thickness_m, self.initial_pit_radius_m, self.radial_growth_m_s)
        if self.geometry == 'spherical_intersection':
            if any(v is None for v in sphere) or self.area_coefficients_m2_m2_s_m2_s2 is not None:
                raise ValueError('spherical geometry requires wall, initial radius and radial growth only')
        elif self.area_coefficients_m2_m2_s_m2_s2 is None or any(v is not None for v in sphere):
            raise ValueError('polynomial geometry requires three supplied area coefficients only')
        return self


class GrowingPitLeakResult(Contract):
    status: Literal['computed', 'quadrature_not_converged']
    times_s: tuple[float, ...]
    opening_areas_m2: tuple[float, ...]
    outward_rates_m3_s: tuple[float, ...]
    cumulative_volume_m3: tuple[float, ...]
    cumulative_mass_kg: tuple[float, ...]
    volume_error_estimate_m3: Nonnegative
    through_wall_time_s: Nonnegative | None
    through_wall_time_outside_representation: bool = False
    corrosion_rate_inferred: Literal[False] = False
    head_feedback_solved: Literal[False] = False


def evaluate_growing_pit_leak(request: GrowingPitLeakRequest) -> GrowingPitLeakResult:
    """Integrate positive-head Bernoulli flow, splitting at first penetration."""
    from scipy.integrate import IntegrationWarning, quad

    if not isinstance(request, GrowingPitLeakRequest):
        raise TypeError('GrowingPitLeakRequest required')
    q = GrowingPitLeakRequest.model_validate(request.model_dump())
    breach = None
    breach_outside_representation = False
    if q.geometry == 'spherical_intersection':
        initial = Decimal.from_float(q.initial_pit_radius_m)
        wall = Decimal.from_float(q.wall_thickness_m)
        growth = Decimal.from_float(q.radial_growth_m_s)
        pi = Decimal.from_float(math.pi)
        if q.initial_pit_radius_m >= q.wall_thickness_m:
            breach = 0.0
        elif q.radial_growth_m_s > 0:
            with localcontext() as context:
                context.prec = 4000
                breach = float((wall-initial)/growth)
            if not math.isfinite(breach) or breach == 0:
                breach = None
                breach_outside_representation = True
        def area(t):
            # Keep the actual excess (r0-wall)+growth*t. Adding a tiny growth
            # increment to the base radius first can erase a real opening.
            # Extra exponent/significand range also retains a representable
            # area when its radius excess or radius sum alone is unrepresentable.
            with localcontext() as context:
                context.prec = 4000
                increment = growth*Decimal.from_float(t)
                excess = (initial-wall)+increment
                if excess <= 0:
                    return 0.0
                value = float(pi*excess*(initial+wall+increment))
            if not math.isfinite(value) or value <= 0:
                raise ValueError('positive opening area is outside floating representation')
            return value
    else:
        a, b, c = q.area_coefficients_m2_m2_s_m2_s2
        breach = 0.0 if any(v > 0 for v in (a, b, c)) else None
        def area(t):
            value = a + t * (b + t * c)
            positive = a > 0 or (t > 0 and (b > 0 or c > 0))
            if not math.isfinite(value) or (positive and value == 0):
                raise ValueError('positive opening area is outside floating representation')
            return value

    gravity_mantissa, gravity_exponent = math.frexp(q.gravity_m_s2)
    def outward_rate(cd, opening, head):
        if cd == 0 or opening == 0 or head == 0:
            return 0.0
        head_mantissa, head_exponent = math.frexp(head)
        radicand_mantissa = 2*gravity_mantissa*head_mantissa
        radicand_exponent = gravity_exponent+head_exponent
        if radicand_exponent % 2:
            radicand_mantissa *= 2
            radicand_exponent -= 1
        # Form Cd*A*sqrt(2*g*H) before the single final exponent conversion.
        # Neither the radicand nor a tiny rounded speed is an intermediate float.
        cd_mantissa, cd_exponent = math.frexp(cd)
        area_mantissa, area_exponent = math.frexp(opening)
        mantissa = cd_mantissa*area_mantissa*math.sqrt(radicand_mantissa)
        exponent = cd_exponent+area_exponent+radicand_exponent//2
        try:
            value = math.ldexp(mantissa, exponent)
        except OverflowError as exc:
            raise ValueError('positive outflow is outside floating representation') from exc
        if not math.isfinite(value) or value == 0:
            raise ValueError('positive outflow is outside floating representation')
        return value

    volumes = [0.0]
    cumulative = compensation = 0.0
    error = 0.0
    converged = True
    pieces = len(q.history) - 1
    for left, right in zip(q.history, q.history[1:]):
        dt = right.time_s - left.time_s
        def rate(t):
            f = (t - left.time_s) / dt
            head = left.liquid_head_m+f*(right.liquid_head_m-left.liquid_head_m)
            cd = left.discharge_coefficient+f*(right.discharge_coefficient-left.discharge_coefficient)
            return outward_rate(cd, area(t), head)
        points = [breach] if breach is not None and left.time_s < breach < right.time_s else None
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter('always', IntegrationWarning)
            volume, estimate = quad(rate, left.time_s, right.time_s, points=points,
                epsabs=q.absolute_volume_tolerance_m3 / pieces,
                epsrel=q.relative_tolerance, limit=q.maximum_subintervals)
        converged = converged and not any(issubclass(w.category, IntegrationWarning) for w in caught)
        if volume < 0 or not math.isfinite(volume):
            raise ValueError('outward volume quadrature must be finite and nonnegative')
        if (volume == 0 and area(right.time_s) > 0
            and max(left.liquid_head_m,right.liquid_head_m) > 0
            and max(left.discharge_coefficient,right.discharge_coefficient) > 0):
            raise ValueError('positive integrated leak volume is outside floating representation or time resolution')
        # Compensate repeated tiny increments instead of losing each one into a
        # large prior inventory before their combined contribution is visible.
        increment = volume-compensation
        updated = cumulative+increment
        if not math.isfinite(updated):
            raise ValueError('cumulative leak volume exceeds floating representation')
        compensation = (updated-cumulative)-increment
        cumulative = updated
        volumes.append(cumulative)
        error = math.fsum((error, estimate))
    areas = tuple(area(p.time_s) for p in q.history)
    rates = tuple(outward_rate(p.discharge_coefficient, a, p.liquid_head_m)
                  for p, a in zip(q.history, areas))
    masses = tuple(_product_ratio((v,q.density_kg_m3),()) for v in volumes)
    if not all(math.isfinite(v) for v in (*areas, *rates, *volumes, *masses, error)):
        raise ValueError('opening or inventory exceeds finite numerical support')
    converged = converged and error <= max(q.absolute_volume_tolerance_m3, q.relative_tolerance * volumes[-1])
    return GrowingPitLeakResult(status='computed' if converged else 'quadrature_not_converged',
        times_s=tuple(p.time_s for p in q.history), opening_areas_m2=areas,
        outward_rates_m3_s=rates, cumulative_volume_m3=tuple(volumes),
        cumulative_mass_kg=masses, volume_error_estimate_m3=error,
        through_wall_time_s=breach,
        through_wall_time_outside_representation=breach_outside_representation)
