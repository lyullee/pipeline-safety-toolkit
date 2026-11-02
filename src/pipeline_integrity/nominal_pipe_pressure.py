"""Explicit diameter-basis nominal pressure-stress relations and algebraic inverses.

The reported nominal stress is a specified diameter convention, not a thick-wall
stress field, buckling solution, material limit or design-code acceptance check.
"""

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Positive = Annotated[float, Field(gt=0)]
Nonnegative = Annotated[float, Field(ge=0)]


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class NominalHoopStressInput(_Input):
    differential_pressure_Pa: Nonnegative
    outside_diameter_m: Positive
    wall_thickness_m: Positive
    diameter_basis: Literal["outside", "mean_from_outside"] = "outside"

    @model_validator(mode="after")
    def hollow_section(self):
        if self.wall_thickness_m >= self.outside_diameter_m / 2.0:
            raise ValueError("wall thickness must leave a positive internal diameter")
        return self


class NominalThicknessInput(_Input):
    differential_pressure_Pa: Nonnegative
    outside_diameter_m: Positive
    supplied_nominal_stress_Pa: Positive
    diameter_basis: Literal["outside", "mean_from_outside"] = "outside"


def nominal_hoop_stress_Pa(request: NominalHoopStressInput) -> float:
    """Return nominal stress with the explicitly selected diameter convention.

    Both modes accept the actual outside diameter and wall thickness. The mean
    convention uses outside diameter minus wall thickness; no geometric input
    is relabelled, and neither mode is an actual through-wall stress solution.
    """
    if not isinstance(request, NominalHoopStressInput):
        raise TypeError("NominalHoopStressInput is required")
    x = NominalHoopStressInput.model_validate(request.model_dump(mode="python"))
    diameter = x.outside_diameter_m if x.diameter_basis == "outside" else x.outside_diameter_m - x.wall_thickness_m
    stress = x.differential_pressure_Pa * (diameter / x.wall_thickness_m) / 2.0
    if not math.isfinite(stress):
        raise ValueError("nominal stress exceeds finite numerical support")
    return stress


def nominal_wall_thickness_m(request: NominalThicknessInput) -> float:
    """Invert the nominal relation for a caller-supplied positive stress value.

    Zero pressure gives zero algebraic thickness; minimum manufacture, corrosion,
    mechanical loads and required design factors must be assessed separately.
    """
    if not isinstance(request, NominalThicknessInput):
        raise TypeError("NominalThicknessInput is required")
    x = NominalThicknessInput.model_validate(request.model_dump(mode="python"))
    if x.diameter_basis == "outside":
        thickness = x.differential_pressure_Pa / x.supplied_nominal_stress_Pa * x.outside_diameter_m / 2.0
    else:
        scale = max(x.differential_pressure_Pa, x.supplied_nominal_stress_Pa)
        pressure = x.differential_pressure_Pa / scale
        stress = x.supplied_nominal_stress_Pa / scale
        thickness = pressure / (2.0 * stress + pressure) * x.outside_diameter_m
        if x.differential_pressure_Pa > 0 and thickness == 0:
            raise ValueError("positive mean-basis thickness is below numerical support")
    if not math.isfinite(thickness):
        raise ValueError("nominal thickness exceeds finite numerical support")
    if thickness >= x.outside_diameter_m / 2.0:
        raise ValueError("inverted thickness does not leave a positive internal diameter")
    return thickness
