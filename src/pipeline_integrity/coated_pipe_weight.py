"""Exact annular coating extension of the existing sealed bare-pipe ledger."""
from __future__ import annotations
import math
from typing import Literal
from pydantic import model_validator
from .contracts import Contract, Nonnegative, Positive
from .covered_pipe_uplift import CoveredPipeUpliftRequest, evaluate_covered_pipe_uplift


class CoatedPipeWeightRequest(Contract):
    outer_diameter_m: Positive
    inner_diameter_m: Nonnegative
    coating_thickness_m: Nonnegative | None
    pipe_unit_weight_N_m3: Positive | None
    coating_unit_weight_N_m3: Nonnegative | None
    surrounding_fluid_unit_weight_N_m3: Positive | None
    fully_submerged_uniform_empty_sealed_pipe_and_continuous_coating_reviewed: bool = False

    @model_validator(mode='after')
    def section(self):
        if self.inner_diameter_m>=self.outer_diameter_m:
            raise ValueError('inner diameter must be smaller than outside diameter')
        return self


class CoatedPipeWeightResult(Contract):
    status: Literal['computed', 'needs_data', 'out_of_scope']
    bare_pipe_weight_N_m: Nonnegative | None = None
    coating_weight_N_m: Nonnegative | None = None
    buoyant_force_N_m: Nonnegative | None = None
    downward_submerged_weight_N_m: float | None = None
    coating_area_m2: Nonnegative | None = None
    coating_thickness_m: Nonnegative | None = None
    issues: tuple[str, ...] = ()
    design_acceptance_inferred: Literal[False] = False


def evaluate_coated_pipe_weight(q: CoatedPipeWeightRequest) -> CoatedPipeWeightResult:
    """Positive net weight is downward; no soil, contents or restraint is inferred.

    Actual coaxial outer coating diameter determines displaced fluid. The source's
    thin-coating shortcut is replaced with full annular geometry; water/mud density
    and coating weight are caller inputs, without fixed reference-fluid constants.
    """
    if not isinstance(q,CoatedPipeWeightRequest):raise TypeError('CoatedPipeWeightRequest required')
    q=CoatedPipeWeightRequest.model_validate(q.model_dump(mode='python'))
    if (None in (q.coating_thickness_m,q.pipe_unit_weight_N_m3,q.coating_unit_weight_N_m3,q.surrounding_fluid_unit_weight_N_m3)
        or not q.fully_submerged_uniform_empty_sealed_pipe_and_continuous_coating_reviewed):
        return CoatedPipeWeightResult(status='needs_data',issues=('complete reviewed empty sealed coated section and unit weights required',))
    bare=evaluate_covered_pipe_uplift(CoveredPipeUpliftRequest(outer_diameter_m=q.outer_diameter_m,
        inner_diameter_m=q.inner_diameter_m,pipe_material_unit_weight_n_m3=q.pipe_unit_weight_N_m3,
        water_unit_weight_n_m3=q.surrounding_fluid_unit_weight_N_m3,cover_depth_m=0,
        supplied_uplift_load_factor=1.,fully_submerged_bare_pipe_and_cover_prism_reviewed=True))
    c=q.coating_thickness_m
    area=math.pi*c*(q.outer_diameter_m+c)
    weight=area*q.coating_unit_weight_N_m3
    buoyancy=math.fsum((bare.buoyant_force_n_m,area*q.surrounding_fluid_unit_weight_N_m3))
    net=math.fsum((bare.pipe_weight_n_m,weight,-buoyancy))
    if not all(math.isfinite(v) for v in (area,weight,buoyancy,net)):
        raise ValueError('coated pipe load exceeds finite numerical support')
    if c>0 and area<=0:raise ValueError('coating section is below finite numerical support')
    return CoatedPipeWeightResult(status='computed',bare_pipe_weight_N_m=bare.pipe_weight_n_m,
        coating_weight_N_m=weight,buoyant_force_N_m=buoyancy,downward_submerged_weight_N_m=net,
        coating_area_m2=area,coating_thickness_m=c)


def size_dense_coating_for_submerged_weight(q: CoatedPipeWeightRequest,
    *, required_downward_submerged_weight_N_m: float | None) -> CoatedPipeWeightResult:
    """Find minimum nonnegative thickness for a caller-declared downward load.

    This inverse requires known material heavier than the displaced fluid when
    added ballast is needed. An already satisfied target returns zero coating;
    a neutral/light coating is not silently made into a dense ballast material.
    No code factor or material-strength criterion is selected here.
    """
    if not isinstance(q,CoatedPipeWeightRequest):raise TypeError('CoatedPipeWeightRequest required')
    q=CoatedPipeWeightRequest.model_validate(q.model_dump(mode='python'))
    if required_downward_submerged_weight_N_m is None:
        return CoatedPipeWeightResult(status='needs_data',issues=('required downward submerged load must be supplied',))
    if not math.isfinite(required_downward_submerged_weight_N_m):raise ValueError('finite target required')
    base=evaluate_coated_pipe_weight(q.model_copy(update={'coating_thickness_m':0.}))
    if base.status!='computed':return base
    additional=required_downward_submerged_weight_N_m-base.downward_submerged_weight_N_m
    if additional<=0:return base
    difference=q.coating_unit_weight_N_m3-q.surrounding_fluid_unit_weight_N_m3
    if difference<=0:
        return CoatedPipeWeightResult(status='out_of_scope',issues=('added downward ballast requires coating denser than surrounding fluid',))
    area=additional/difference
    s=area/math.pi
    # Rationalized positive quadratic root avoids subtracting close diameters.
    thickness=2*s/(math.hypot(q.outer_diameter_m,2*math.sqrt(s))+q.outer_diameter_m)
    if not math.isfinite(thickness) or thickness<=0:raise ValueError('required thickness exceeds finite numerical support')
    return evaluate_coated_pipe_weight(q.model_copy(update={'coating_thickness_m':thickness}))
