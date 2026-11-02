"""Local submerged bare-pipe and cover-prism vertical force balance."""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Nonnegative, Positive


class CoveredPipeUpliftRequest(Contract):
    outer_diameter_m: Positive | None = None
    inner_diameter_m: Nonnegative | None = None
    pipe_material_unit_weight_n_m3: Positive | None = None
    water_unit_weight_n_m3: Positive | None = None
    cover_depth_m: Nonnegative | None = None
    dry_backfill_unit_weight_n_m3: Positive | None = None
    backfill_particle_relative_density: float | None = Field(default=None,gt=1.)
    supplied_uplift_load_factor: Positive | None = None
    fully_submerged_bare_pipe_and_cover_prism_reviewed: bool = False

    @model_validator(mode='after')
    def annular_geometry(self):
        if self.outer_diameter_m is not None and self.inner_diameter_m is not None:
            if self.inner_diameter_m>=self.outer_diameter_m:
                raise ValueError('inner diameter must be smaller than outer diameter')
        return self


class CoveredPipeUpliftResult(Contract):
    status: Literal['computed','needs_data']
    pipe_weight_n_m: Nonnegative | None = None
    submerged_cover_weight_n_m: Nonnegative | None = None
    buoyant_force_n_m: Nonnegative | None = None
    factored_uplift_n_m: Nonnegative | None = None
    vertical_margin_n_m: float | None = None
    supplied_criterion_satisfied: bool | None = None
    issues: tuple[str,...] = ()


def evaluate_covered_pipe_uplift(request: CoveredPipeUpliftRequest) -> CoveredPipeUpliftResult:
    """Compare pipe-plus-cover downward load with a supplied factored buoyancy.

    All forces are per unit pipe length. The cover prism has width equal to
    the bare pipe's outside diameter. Its submerged weight removes water
    displaced by the backfill solids, using the supplied particle/water density
    ratio. The bore is empty and sealed against water ingress; no content,
    coating, anchors, shear resistance or hydrodynamic loads are inferred.
    The supplied load factor is a caller criterion, not a selected code factor.
    """
    if not isinstance(request,CoveredPipeUpliftRequest):
        raise TypeError('CoveredPipeUpliftRequest required')
    q=CoveredPipeUpliftRequest.model_validate(request.model_dump(mode='python'))
    needed=['outer_diameter_m','inner_diameter_m','pipe_material_unit_weight_n_m3',
            'water_unit_weight_n_m3','cover_depth_m','supplied_uplift_load_factor']
    if q.cover_depth_m is not None and q.cover_depth_m>0:
        needed+=['dry_backfill_unit_weight_n_m3','backfill_particle_relative_density']
    issues=['missing '+name for name in needed if getattr(q,name) is None]
    if not q.fully_submerged_bare_pipe_and_cover_prism_reviewed:
        issues.append('fully submerged bare-pipe and cover-prism basis require review')
    if issues:return CoveredPipeUpliftResult(status='needs_data',issues=tuple(issues))
    d=q.outer_diameter_m;bore=q.inner_diameter_m
    metal_area=math.pi/4*(d-bore)*(d+bore)
    external_area=math.pi/4*d*d
    pipe_weight=metal_area*q.pipe_material_unit_weight_n_m3
    soil_weight=0. if q.cover_depth_m==0 else (
        q.cover_depth_m*d*q.dry_backfill_unit_weight_n_m3*(1-1/q.backfill_particle_relative_density))
    buoyancy=external_area*q.water_unit_weight_n_m3
    factored=q.supplied_uplift_load_factor*buoyancy
    margin=math.fsum((pipe_weight,soil_weight,-factored))
    if not all(math.isfinite(v) for v in (pipe_weight,soil_weight,buoyancy,factored,margin)):
        raise ValueError('vertical force ledger exceeds finite numerical support')
    return CoveredPipeUpliftResult(status='computed',pipe_weight_n_m=pipe_weight,
        submerged_cover_weight_n_m=soil_weight,buoyant_force_n_m=buoyancy,
        factored_uplift_n_m=factored,vertical_margin_n_m=margin,
        supplied_criterion_satisfied=margin>=0)
