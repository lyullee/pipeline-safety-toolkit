"""Prescribed coplanar guy-load projection and conditional load-share observation.

This is a rigid point-force demand ledger. It neither solves cable stiffness or
sag nor selects rope strengths, erection tension or load-distribution factors.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field,model_validator

from .contracts import Contract,Name,Nonnegative,Positive,digest

Vector=tuple[float,float,float]


def _dot(a: Vector,b: Vector) -> float:
    return math.fsum(x*y for x,y in zip(a,b))


def _sub(a: Vector,b: Vector) -> Vector:
    return tuple(x-y for x,y in zip(a,b))


def _cross(a: Vector,b: Vector) -> Vector:
    return (a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0])


def xz_force_from_magnitude_and_elevation(magnitude_n: float,elevation_rad: float,
                                        horizontal_sign: Literal[-1,1]=1) -> Vector:
    """Resolve a supplied point force; elevation is positive above horizontal."""
    if any(isinstance(x,bool) or not math.isfinite(x) for x in (magnitude_n,elevation_rad)):
        raise ValueError('finite force magnitude and angle required')
    if magnitude_n<0 or not -math.pi/2<=elevation_rad<=math.pi/2 or isinstance(horizontal_sign,bool) or horizontal_sign not in (-1,1):
        raise ValueError('nonnegative magnitude, reviewed half-plane angle and signed horizontal axis required')
    return (horizontal_sign*magnitude_n*math.cos(elevation_rad),0.,magnitude_n*math.sin(elevation_rad))


class GuyLoadDistribution(Contract):
    mode: Literal['single_coplanar_guy','reviewed_factor']
    guy_count: int = Field(ge=1)
    basis_id: Name
    maximum_individual_increment_to_coplanar_pull_factor: Positive | None = None
    uniform_spacing_equal_erection_tension_and_factor_domain_reviewed: bool = False
    supplied_initial_individual_tension_n: Nonnegative | None = None

    @model_validator(mode='after')
    def domain(self):
        if self.mode=='single_coplanar_guy':
            if self.guy_count!=1 or self.maximum_individual_increment_to_coplanar_pull_factor not in (None,1.):
                raise ValueError('one actual coplanar guy has unit incremental share')
        elif self.guy_count<2:
            raise ValueError('reviewed multi-guy factor requires at least two guys')
        return self


class GuyProjectionRequest(Contract):
    id: Name
    coordinate_frame_id: Name
    attachment_point_m: Vector | None = None
    anchorage_point_m: Vector | None = None
    external_force_n: Vector | None = None
    horizontal_axis_in_load_plane: Vector = (1.,0.,0.)
    upward_vertical_axis: Vector = (0.,0.,1.)
    external_force_application_point_m: Vector | None = None
    reaction_reference_point_m: Vector | None = None
    external_applied_couple_nm: Vector = (0.,0.,0.)
    reaction_model: Literal['required_reaction','pin_without_couple'] = 'required_reaction'
    distribution: GuyLoadDistribution | None = None
    coplanar_incremental_load_and_selected_reaction_direction_reviewed: bool = False
    absolute_out_of_plane_distance_tolerance_m: Nonnegative = 1e-10
    absolute_out_of_plane_force_tolerance_n: Nonnegative = 1e-9
    minimum_absolute_horizontal_direction_cosine: Positive = Field(default=1e-8,le=1.)
    absolute_pin_couple_tolerance_nm: Nonnegative = 1e-7

    @model_validator(mode='after')
    def axes(self):
        for axis in (self.horizontal_axis_in_load_plane,self.upward_vertical_axis):
            if not math.isclose(math.hypot(*axis),1.,rel_tol=0.,abs_tol=1e-12):
                raise ValueError('explicit orthonormal horizontal/vertical unit axes required')
        if abs(_dot(self.horizontal_axis_in_load_plane,self.upward_vertical_axis))>1e-12:
            raise ValueError('load-plane axes must be orthogonal')
        return self

    @classmethod
    def from_xz_elevations(cls,*,attachment_elevation_m: float,anchorage_elevation_m: float,
                           signed_horizontal_anchor_offset_m: float,external_force_n: Vector,**options):
        """Convenience construction in an explicitly declared Cartesian X-Z frame.

        The force is applied at the attachment; the reaction reference is the
        origin. Use the main constructor when either point or frame differs.
        """
        return cls(attachment_point_m=(0.,0.,attachment_elevation_m),
            anchorage_point_m=(signed_horizontal_anchor_offset_m,0.,anchorage_elevation_m),
            external_force_n=external_force_n,external_force_application_point_m=(0.,0.,attachment_elevation_m),
            reaction_reference_point_m=(0.,0.,0.),**options)


class GuyProjectionResult(Contract):
    status: Literal['computed','needs_data','out_of_scope','underdetermined']
    coordinate_frame_id: Name
    request_digest: str
    unit_guy_from_attachment_to_anchor: Vector | None = None
    guy_inclination_magnitude_from_horizontal_rad: Nonnegative | None = None
    required_signed_coplanar_increment_n: float | None = None
    coplanar_load_induced_pull_n: Nonnegative | None = None
    aggregate_guy_force_n: Vector | None = None
    required_base_reaction_force_n: Vector | None = None
    required_base_reaction_couple_nm: Vector | None = None
    force_balance_residual_n: Vector | None = None
    moment_balance_residual_nm: Vector | None = None
    distribution_status: Literal['not_requested','needs_data','computed'] = 'not_requested'
    distribution_basis_id: Name | None = None
    maximum_individual_extra_tension_n: Nonnegative | None = None
    conditional_individual_total_tension_n: Nonnegative | None = None
    issues: tuple[str,...] = ()
    actual_multi_guy_stiffness_or_sag_solved: Literal[False] = False
    rope_or_support_capacity_or_code_acceptance_assessed: Literal[False] = False


def evaluate_guy_projection(request: GuyProjectionRequest) -> GuyProjectionResult:
    """Balance the horizontal load with a declared coplanar tensile resultant.

    A negative solved increment would require an unloading/pretension model or
    a different resisting direction; it is not reported as compressive rope.
    Optional base force/couple values are required reactions, not their capacity.
    A multi-guy factor is supplied data and is never inferred from guy count.
    """
    if not isinstance(request,GuyProjectionRequest):raise TypeError('GuyProjectionRequest required')
    q=GuyProjectionRequest.model_validate(request.model_dump(mode='python'))
    base={'coordinate_frame_id':q.coordinate_frame_id,'request_digest':digest(q)}
    issues=[]
    for field in ('attachment_point_m','anchorage_point_m','external_force_n'):
        if getattr(q,field) is None:issues.append('missing '+field)
    if not q.coplanar_incremental_load_and_selected_reaction_direction_reviewed:
        issues.append('coplanar incremental load and selected reaction direction require review')
    if issues:return GuyProjectionResult(status='needs_data',issues=tuple(issues),**base)
    horizontal=q.horizontal_axis_in_load_plane;vertical=q.upward_vertical_axis
    normal=_cross(horizontal,vertical);span=_sub(q.anchorage_point_m,q.attachment_point_m)
    length=math.hypot(*span)
    if not math.isfinite(length) or length<=0:raise ValueError('finite nonzero actual guy span required')
    if abs(_dot(span,normal))>q.absolute_out_of_plane_distance_tolerance_m or abs(_dot(q.external_force_n,normal))>q.absolute_out_of_plane_force_tolerance_n:
        return GuyProjectionResult(status='out_of_scope',issues=('guy geometry and force must lie in the declared common vertical plane',),**base)
    direction=tuple(x/length for x in span)
    horizontal_cosine=_dot(direction,horizontal)
    vertical_cosine=_dot(direction,vertical)
    base.update(unit_guy_from_attachment_to_anchor=direction,
        guy_inclination_magnitude_from_horizontal_rad=math.atan2(abs(vertical_cosine),abs(horizontal_cosine)))
    force_horizontal=_dot(q.external_force_n,horizontal)
    if horizontal_cosine==0:
        return GuyProjectionResult(status='underdetermined' if force_horizontal==0 else 'out_of_scope',
            issues=('vertical guy does not determine a horizontal resisting tension',),**base)
    if abs(horizontal_cosine)<q.minimum_absolute_horizontal_direction_cosine:
        return GuyProjectionResult(status='out_of_scope',issues=('horizontal projection is below caller-selected conditioning support',),**base)
    pull=-force_horizontal/horizontal_cosine
    if not math.isfinite(pull):raise ValueError('required projected load exceeds finite support')
    base['required_signed_coplanar_increment_n']=pull
    if pull<0:return GuyProjectionResult(status='out_of_scope',issues=('selected guy direction would require compression or a reviewed unloading/pretension model',),**base)
    guy=tuple(pull*x for x in direction)
    reaction=tuple(-math.fsum((a,b)) for a,b in zip(q.external_force_n,guy))
    residual=tuple(math.fsum((a,b,c)) for a,b,c in zip(q.external_force_n,guy,reaction))
    base.update(coplanar_load_induced_pull_n=pull,aggregate_guy_force_n=guy,
        required_base_reaction_force_n=reaction,force_balance_residual_n=residual)
    if q.external_force_application_point_m is not None and q.reaction_reference_point_m is not None:
        applied=_cross(_sub(q.external_force_application_point_m,q.reaction_reference_point_m),q.external_force_n)
        guy_moment=_cross(_sub(q.attachment_point_m,q.reaction_reference_point_m),guy)
        couple=tuple(-math.fsum((a,b,c)) for a,b,c in zip(applied,guy_moment,q.external_applied_couple_nm))
        moment_residual=tuple(math.fsum((a,b,c,d)) for a,b,c,d in zip(applied,guy_moment,q.external_applied_couple_nm,couple))
        base.update(required_base_reaction_couple_nm=couple,moment_balance_residual_nm=moment_residual)
        if q.reaction_model=='pin_without_couple' and math.hypot(*couple)>q.absolute_pin_couple_tolerance_nm:
            return GuyProjectionResult(status='out_of_scope',issues=('a pin cannot supply the required nonzero reaction couple',),**base)
    elif q.reaction_model=='pin_without_couple':
        return GuyProjectionResult(status='needs_data',issues=('force application and reaction-reference points are needed to check a pin moment',),**base)
    else:issues.append('reaction moment remains unknown without force application and reference points')
    distribution=q.distribution
    if distribution is not None:
        base['distribution_basis_id']=distribution.basis_id
        if distribution.mode=='single_coplanar_guy':factor=1.
        elif not distribution.uniform_spacing_equal_erection_tension_and_factor_domain_reviewed or distribution.maximum_individual_increment_to_coplanar_pull_factor is None:
            factor=None
        else:factor=distribution.maximum_individual_increment_to_coplanar_pull_factor
        if factor is None:
            base['distribution_status']='needs_data';issues.append('individual-guy factor and its spacing/initial-tension domain require review')
        else:
            extra=factor*pull
            total=None if distribution.supplied_initial_individual_tension_n is None else extra+distribution.supplied_initial_individual_tension_n
            if not all(math.isfinite(x) for x in (extra,extra if total is None else total)):
                raise ValueError('conditional individual load exceeds finite support')
            base.update(distribution_status='computed',maximum_individual_extra_tension_n=extra,
                conditional_individual_total_tension_n=total)
    return GuyProjectionResult(status='computed',issues=tuple(issues),**base)
