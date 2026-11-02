"""Instantaneous fluid loads per metre of a stationary circular cylinder.

The flow is transverse to the cylinder axis. Users supply all coefficients,
velocity and acceleration. The common total-inertia convention is Cm=1+Ca;
the displaced-fluid and added-mass contributions are reported separately.
No waves, seabed coefficients, body movement, structural response or design
acceptance are inferred. Lift coefficient sign selects the upward force axis.

Primary equation conventions:
https://www.orcina.com/webhelp/OrcaFlex/Content/html/Morison%27sequation.htm
https://www.orcina.com/webhelp/OrcaFlex/Content/html/Linetypes%2CDragliftdata.htm
"""
from __future__ import annotations

import math
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Positive = Annotated[float, Field(gt=0)]
Nonnegative = Annotated[float, Field(ge=0)]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class StationaryCylinderLoad(_Contract):
    fluid_density_kg_m3: Positive
    outside_diameter_m: Positive
    transverse_velocity_m_s: float
    transverse_acceleration_m_s2: float
    drag_coefficient: Nonnegative
    lift_coefficient_upward: float
    inertia_coefficient: Nonnegative
    inertia_coefficient_kind: Literal["total_inertia", "added_mass"]

    @model_validator(mode="after")
    def common_coefficient_domain(self):
        if self.inertia_coefficient_kind == "total_inertia" and self.inertia_coefficient < 1:
            raise ValueError("this Cm=1+Ca model requires total inertia coefficient >= 1")
        return self


class CylinderLoadPerLength(_Contract):
    drag_horizontal_n_m: float
    displaced_fluid_inertia_horizontal_n_m: float
    added_mass_inertia_horizontal_n_m: float
    total_inertia_horizontal_n_m: float
    total_horizontal_n_m: float
    lift_upward_n_m: float
    total_inertia_coefficient: Nonnegative
    added_mass_coefficient: Nonnegative
    displaced_fluid_mass_kg_m: Nonnegative
    body_motion_modelled: Literal[False] = False
    coefficient_selection_modelled: Literal[False] = False


def evaluate_stationary_cylinder_load(request: StationaryCylinderLoad) -> CylinderLoadPerLength:
    """Resolve signed drag, lift and explicitly separated inertia components.

    Drag follows the signed fluid velocity. Lift uses the square of velocity,
    so its direction follows the caller's signed lift coefficient, not flow
    reversal. Both loads use diameter as projected width per cylinder length.
    Inertia uses the circular displaced area per metre, including coating in
    the supplied outside diameter. All outputs are instantaneous loads per metre.
    """
    if not isinstance(request, StationaryCylinderLoad):
        raise TypeError("StationaryCylinderLoad is required")
    q = StationaryCylinderLoad.model_validate(request.model_dump(mode="python"))
    if q.inertia_coefficient_kind == "total_inertia":
        cm = q.inertia_coefficient
        ca = cm - 1.0
    else:
        ca = q.inertia_coefficient
        cm = 1.0 + ca
    radius = q.outside_diameter_m / 2.0
    area = math.pi * radius * radius
    mass = q.fluid_density_kg_m3 * area
    speed = q.transverse_velocity_m_s
    dynamic_width = 0.5 * q.fluid_density_kg_m3 * q.outside_diameter_m
    drag = dynamic_width * q.drag_coefficient * speed * abs(speed)
    lift = dynamic_width * q.lift_coefficient_upward * speed * speed
    displaced = mass * q.transverse_acceleration_m_s2
    added = ca * displaced
    inertia = cm * displaced
    horizontal = drag + inertia
    if not all(math.isfinite(v) for v in (area,mass,cm,ca,drag,lift,displaced,added,inertia,horizontal)):
        raise ValueError("cylinder load exceeds finite numerical support")
    if area == 0 or mass == 0:
        raise ValueError("cylinder displacement is unresolved at supplied numerical scales")
    return CylinderLoadPerLength(drag_horizontal_n_m=drag,
        displaced_fluid_inertia_horizontal_n_m=displaced,
        added_mass_inertia_horizontal_n_m=added,
        total_inertia_horizontal_n_m=inertia,total_horizontal_n_m=horizontal,
        lift_upward_n_m=lift,total_inertia_coefficient=cm,added_mass_coefficient=ca,
        displaced_fluid_mass_kg_m=mass)
