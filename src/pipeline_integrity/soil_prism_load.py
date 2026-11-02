"""Local backfill-prism weight and water-head load at a declared plane."""
from __future__ import annotations

import math
from typing import Literal

from pydantic import model_validator

from .contracts import Contract, Fraction, Nonnegative, Positive


class SoilPrismLoadRequest(Contract):
    burial_depth_m: Nonnegative | None = None
    dry_bulk_unit_weight_n_m3: Positive | None = None
    water_head_m: Nonnegative | None = None
    submerged_soil_weight_reduction_fraction: Fraction | None = None
    water_unit_weight_n_m3: Positive | None = None
    loaded_width_m: Positive | None = None
    uniform_prism_and_water_basis_reviewed: bool = False

    @model_validator(mode='after')
    def water_position(self):
        if self.burial_depth_m is not None and self.water_head_m is not None:
            if self.water_head_m>self.burial_depth_m:
                raise ValueError('water level above the ground surface is outside this prism model')
        return self


class SoilPrismLoadResult(Contract):
    status: Literal['computed','needs_data']
    reduced_soil_weight_pressure_pa: Nonnegative | None = None
    pore_water_pressure_pa: Nonnegative | None = None
    total_vertical_pressure_pa: Nonnegative | None = None
    vertical_line_load_n_m: Nonnegative | None = None
    issues: tuple[str,...] = ()


def evaluate_soil_prism_load(request: SoilPrismLoadRequest) -> SoilPrismLoadResult:
    """Sum a declared soil prism's reduced weight and hydrostatic water load.

    Depth is measured from ground to the caller's load plane. Water head is
    measured from the water level to that same plane, and must lie inside the
    cover depth. The soil-weight reduction fraction is supplied independently
    of water unit weight; no fixed soil-density or buoyancy shortcut is assumed.
    An optional width converts pressure to force per unit axial length.
    This is a prescribed load, without arching, earth-pressure redistribution,
    structural response, consolidation or code acceptance.
    """
    if not isinstance(request,SoilPrismLoadRequest):
        raise TypeError('SoilPrismLoadRequest required')
    q=SoilPrismLoadRequest.model_validate(request.model_dump(mode='python'))
    needed=['burial_depth_m','dry_bulk_unit_weight_n_m3','water_head_m']
    if q.water_head_m is not None and q.water_head_m>0:
        needed+=['submerged_soil_weight_reduction_fraction','water_unit_weight_n_m3']
    issues=['missing '+name for name in needed if getattr(q,name) is None]
    if not q.uniform_prism_and_water_basis_reviewed:
        issues.append('uniform backfill prism and water-load basis require review')
    if issues:return SoilPrismLoadResult(status='needs_data',issues=tuple(issues))
    h=q.water_head_m;gamma=q.dry_bulk_unit_weight_n_m3
    reduction=0. if h==0 else q.submerged_soil_weight_reduction_fraction
    soil=math.fsum((gamma*(q.burial_depth_m-h),gamma*(1-reduction)*h))
    water=0. if h==0 else q.water_unit_weight_n_m3*h
    total=math.fsum((soil,water))
    line=None if q.loaded_width_m is None else total*q.loaded_width_m
    if not all(math.isfinite(x) for x in (soil,water,total,*(() if line is None else (line,)))):
        raise ValueError('prism load exceeds finite numerical support')
    return SoilPrismLoadResult(status='computed',reduced_soil_weight_pressure_pa=soil,
        pore_water_pressure_pa=water,total_vertical_pressure_pa=total,vertical_line_load_n_m=line)
