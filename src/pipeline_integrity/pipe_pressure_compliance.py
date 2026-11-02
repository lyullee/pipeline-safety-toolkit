"""Incremental thin-wall diameter compliance under declared axial restraint."""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import Field

from .contracts import Contract, Positive

PoissonRatio=Annotated[float,Field(gt=-1,lt=.5)]
StrainSupport=Annotated[float,Field(gt=0,lt=1)]


class PressureDiameterRequest(Contract):
    reference_internal_diameter_m: Positive
    wall_thickness_m: Positive
    young_modulus_pa: Positive
    differential_pressure_increment_pa: float
    axial_condition: Literal["zero_axial_stress", "zero_axial_strain"]
    poisson_ratio: PoissonRatio | None = None
    maximum_absolute_hoop_strain: StrainSupport | None = None
    thin_wall_linear_elastic_and_operating_stability_reviewed: bool = False


class PressureDiameterResult(Contract):
    status: Literal["computed", "needs_data", "out_of_scope"]
    diameter_increment_m: float | None = None
    hoop_strain_increment: float | None = None
    linear_area_fraction_increment: float | None = None
    diameter_compliance_m_per_pa: Positive | None = None
    issues: tuple[str, ...] = ()


def evaluate_pressure_diameter_compliance(request: PressureDiameterRequest) -> PressureDiameterResult:
    """Return linear increments about the supplied internal-diameter reference.

    Pressure input is an increment of internal-minus-external pressure. Negative
    increments can describe unloading; no absolute operating pressure, initial
    prestress, buckling margin or closed-end axial condition is inferred. The
    area output is first order in strain, not finite-strain geometry.
    """
    if not isinstance(request,PressureDiameterRequest):
        raise TypeError("PressureDiameterRequest required")
    q=PressureDiameterRequest.model_validate(request.model_dump(mode="python"))
    issues=[]
    if not q.thin_wall_linear_elastic_and_operating_stability_reviewed:
        issues.append("thin-wall elasticity, axial constraint and operating stability require review")
    if q.maximum_absolute_hoop_strain is None:
        issues.append("missing reviewed strain support")
    if q.axial_condition=="zero_axial_strain" and q.poisson_ratio is None:
        issues.append("missing Poisson ratio for the axial-strain constraint")
    if issues:
        return PressureDiameterResult(status="needs_data",issues=tuple(issues))
    factor=1. if q.axial_condition=="zero_axial_stress" else 1-q.poisson_ratio**2
    compliance=(q.reference_internal_diameter_m/q.wall_thickness_m
                *q.reference_internal_diameter_m/q.young_modulus_pa*factor/2)
    increment=compliance*q.differential_pressure_increment_pa
    strain=increment/q.reference_internal_diameter_m
    if not math.isfinite(compliance) or compliance<=0 or not math.isfinite(strain) or not math.isfinite(increment):
        raise ValueError("compliance or response exceeds finite numerical support")
    if abs(strain)>q.maximum_absolute_hoop_strain:
        return PressureDiameterResult(status="out_of_scope",issues=("reviewed hoop-strain support exceeded",))
    return PressureDiameterResult(status="computed",diameter_increment_m=increment,
        hoop_strain_increment=strain,linear_area_fraction_increment=2*strain,
        diameter_compliance_m_per_pa=compliance)
