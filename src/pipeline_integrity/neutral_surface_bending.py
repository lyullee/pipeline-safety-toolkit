"""Circular-bend kinematics about an explicitly located neutral surface."""
from __future__ import annotations

import math

from pydantic import model_validator

from .contracts import Contract, Name, Nonnegative, Positive, digest


class NeutralSurfaceBendingRequest(Contract):
    basis_id: Name
    neutral_radius_m: Positive
    thickness_m: Positive
    neutral_distance_from_inner_face_m: Nonnegative
    reference_arc_length_m: Nonnegative
    signed_curvature_direction: int = 1
    supplied_tensile_strain_ceiling: Positive | None = None
    supplied_compressive_strain_ceiling: Positive | None = None
    plane_sections_and_circular_neutral_surface_reviewed: bool = False

    @model_validator(mode="after")
    def geometry(self):
        if self.neutral_distance_from_inner_face_m > self.thickness_m:
            raise ValueError("neutral surface must lie inside the declared thickness")
        if self.neutral_radius_m <= self.neutral_distance_from_inner_face_m:
            raise ValueError("inside-face radius must be positive")
        if self.signed_curvature_direction not in (-1, 1):
            raise ValueError("curvature direction must be -1 or +1")
        return self


def evaluate_neutral_surface_bending(request: NeutralSurfaceBendingRequest):
    """Map a neutral-radius observation to face radii, signed strains and angle.

    The neutral location is supplied. Plastic redistribution, springback and
    material strain limits are not inferred from a geometric bend observation.
    Face strains use the neutral reference arc length, not an inside-face arc.
    Curvature direction sets the signed bend angle in the observation frame;
    it does not exchange the physical inner and outer faces.
    """
    q = NeutralSurfaceBendingRequest.model_validate(request.model_dump())
    base = dict(request_digest=digest(q), basis_id=q.basis_id,
                material_or_coating_acceptance_qualified=False)
    if not q.plane_sections_and_circular_neutral_surface_reviewed:
        return dict(status="needs_data", **base)
    inner_offset = -q.neutral_distance_from_inner_face_m
    outer_offset = q.thickness_m - q.neutral_distance_from_inner_face_m
    curvature = q.signed_curvature_direction / q.neutral_radius_m
    inner_strain = inner_offset / q.neutral_radius_m
    outer_strain = outer_offset / q.neutral_radius_m
    minimum_radius = 0.0
    for offset in (inner_offset, outer_offset):
        signed_offset = offset
        ceiling = (q.supplied_tensile_strain_ceiling if signed_offset > 0
                   else q.supplied_compressive_strain_ceiling)
        if ceiling is not None:
            minimum_radius = max(minimum_radius, abs(offset) / ceiling)
    values = dict(
        signed_curvature_per_m=curvature,
        inside_face_radius_m=q.neutral_radius_m + inner_offset,
        outside_face_radius_m=q.neutral_radius_m + outer_offset,
        signed_inner_face_engineering_strain=inner_strain,
        signed_outer_face_engineering_strain=outer_strain,
        signed_bend_angle_rad=q.reference_arc_length_m * curvature,
        conditional_minimum_neutral_radius_m=minimum_radius if minimum_radius else None,
    )
    if any(v is not None and not math.isfinite(v) for v in values.values()):
        raise ValueError("bend geometry exceeds finite support")
    return dict(status="computed", **base, values=values)
