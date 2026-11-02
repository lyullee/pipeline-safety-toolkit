"""Common-basis volume-weighted liquid reference-property blend relations."""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive
from .volume_weighting import normalize_component_volumes, normalized_weighted_sum


class LiquidBlendComponent(Contract):
    id: Name
    volume_m3: Nonnegative | None = None
    property_value: Positive | None = None


class LiquidBlendRequest(Contract):
    method: Literal["specific_gravity", "inverse_root_SUS_efflux_time"]
    components: tuple[LiquidBlendComponent,...] = Field(min_length=1)
    reference_basis: Name
    common_property_basis_and_additive_volumes_reviewed: bool = False

    @model_validator(mode="after")
    def unique_components(self):
        if len({c.id for c in self.components})!=len(self.components):
            raise ValueError("component identities must be unique")
        return self


class LiquidBlendResult(Contract):
    status: Literal["computed","needs_data"]
    reference_basis: Name
    total_volume_m3: Positive | None = None
    volume_fractions: dict[Name,float] | None = None
    blended_specific_gravity: Positive | None = None
    blended_SUS_efflux_time_s: Positive | None = None
    issues: tuple[str,...] = ()


def evaluate_liquid_blend(request: LiquidBlendRequest) -> LiquidBlendResult:
    """Evaluate one explicitly selected common-reference blend coordinate.

    Specific gravity is volume-linear only under additive volumes and common
    reference-fluid/state definitions. The SUS option is an empirical mixing
    relation for positive Saybolt Universal efflux times in seconds. It is not
    an EOS or an SI dynamic/kinematic-viscosity prediction and carries no
    calibration range without caller evidence.
    """
    if not isinstance(request,LiquidBlendRequest):raise TypeError("LiquidBlendRequest required")
    q=LiquidBlendRequest.model_validate(request.model_dump(mode="python"))
    issues=[]
    for c in q.components:
        if c.volume_m3 is None:issues.append("missing volume for "+c.id)
        if c.property_value is None:issues.append("missing property coordinate for "+c.id)
    if not q.common_property_basis_and_additive_volumes_reviewed:
        issues.append("common property basis and additive volumes require review")
    if issues:return LiquidBlendResult(status="needs_data",reference_basis=q.reference_basis,issues=tuple(issues))
    if not any(c.volume_m3>0 for c in q.components):
        return LiquidBlendResult(status="needs_data",reference_basis=q.reference_basis,issues=("positive total component volume required",))
    total,weights=normalize_component_volumes({c.id:c.volume_m3 for c in q.components})
    if q.method=="specific_gravity":
        value=normalized_weighted_sum({c.id:c.property_value for c in q.components},weights)
        outputs=dict(blended_specific_gravity=value)
    else:
        inverse_root=normalized_weighted_sum({c.id:1/math.sqrt(c.property_value) for c in q.components},weights)
        if inverse_root==0:raise ValueError("inverse-root blend exceeds finite support")
        value=(1/inverse_root)**2
        outputs=dict(blended_SUS_efflux_time_s=value)
    if not math.isfinite(value) or value<=0:raise ValueError("blend coordinate exceeds finite positive support")
    return LiquidBlendResult(status="computed",reference_basis=q.reference_basis,total_volume_m3=total,
        volume_fractions=weights,**outputs)
