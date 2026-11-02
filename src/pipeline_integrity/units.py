"""Explicit finite scalar conversions; pressure references require their own contract."""

import math
import re
from functools import lru_cache


@lru_cache(maxsize=1)
def _registry():
    import pint

    return pint.UnitRegistry()


def convert_quantity(value: float, source_unit: str, target_unit: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError("convert a finite scalar quantity")
    for unit in (source_unit, target_unit):
        if (
            not isinstance(unit, str)
            or not unit.strip()
            or any(token in unit for token in ("_abs", "_gauge", "unspecified", "declared_", "Kv_"))
        ):
            raise ValueError("semantic or pressure-reference units need an explicit domain adapter")

    def normalize(unit):
        return re.sub(r"\b(m|cm|mm|km)([23])\b", r"\1**\2", unit)

    units = _registry()
    quantity = units.Quantity(value, normalize(source_unit)).to(normalize(target_unit))
    result = float(quantity.magnitude)
    if not math.isfinite(result):
        raise ValueError("conversion produced a nonfinite value")
    return result
