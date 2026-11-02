"""Shared scalar integration primitives for explicitly selected sample models."""

import math


def _product_ratio(numerators, denominators):
    if any(v == 0 for v in numerators):
        return 0.0
    mantissa, exponent = 1.0, 0
    for value in numerators:
        part, power = math.frexp(value)
        mantissa *= part
        exponent += power
    for value in denominators:
        part, power = math.frexp(value)
        mantissa /= part
        exponent -= power
    try:
        result = math.ldexp(mantissa, exponent)
    except OverflowError as exc:
        raise ValueError("integrated quantity exceeds floating representation") from exc
    if not math.isfinite(result) or result == 0:
        raise ValueError("nonzero integrated quantity is outside floating representation")
    return result


def integrate_linear_interval(first, last, duration_s):
    """Return positive and negative magnitudes of the declared linear interval."""
    if not all(math.isfinite(v) for v in (first, last, duration_s)) or duration_s <= 0:
        raise ValueError("finite samples and a positive interval duration are required")
    if (first < 0 < last) or (last < 0 < first):
        scale = max(abs(first), abs(last))
        normalized_sum = abs(first) / scale + abs(last) / scale
        areas = tuple(
            _product_ratio((abs(v), abs(v), duration_s), (2.0, scale, normalized_sum)) for v in (first, last)
        )
        return areas if first > 0 else areas[::-1]
    try:
        scale = max(abs(first), abs(last))
        normalized_sum = 0.0 if scale == 0 else abs(first) / scale + abs(last) / scale
        area = _product_ratio((scale, normalized_sum, duration_s), (2.0,))
    except OverflowError as exc:
        raise ValueError("integrated quantity exceeds floating representation") from exc
    if not math.isfinite(area):
        raise ValueError("integrated quantity exceeds floating representation")
    return (area, 0.0) if first >= 0 and last >= 0 else (0.0, area)
