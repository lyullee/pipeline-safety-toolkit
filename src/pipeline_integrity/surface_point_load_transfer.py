"""Vertical stress transfer in a homogeneous elastic half-space."""
import math
from .contracts import Contract,Positive,Nonnegative,Name

class SurfacePointLoadTransferRequest(Contract):
    downward_surface_force_n: Nonnegative
    depth_below_plane_m: Positive
    horizontal_offset_m: Nonnegative
    elastic_halfspace_load_geometry_basis_id: Name
    homogeneous_isotropic_halfspace_and_point_load_reviewed: bool = False

def evaluate_surface_point_load_transfer(request: SurfacePointLoadTransferRequest):
    """Resolve continuum vertical compression; no pipe-contact stress is inferred.

    Boussinesq's point-force Green function applies to a level, traction-free
    homogeneous elastic half-space. Finite tire/track footprints may be
    integrated externally. Layering, trench arching, transient inertia and actual
    soil-pipe interface pressure/ovalization need their own constitutive models.
    """
    q=SurfacePointLoadTransferRequest.model_validate(request.model_dump())
    base=dict(basis_id=q.elastic_halfspace_load_geometry_basis_id,pipe_contact_or_ovalization_qualified=False)
    if not q.homogeneous_isotropic_halfspace_and_point_load_reviewed:return dict(status='needs_data',**base)
    z=q.depth_below_plane_m;r=q.horizontal_offset_m
    separation=math.hypot(z,r)
    stress=3*q.downward_surface_force_n/(2*math.pi*separation**2)*(z/separation)**3
    return dict(status='computed',**base,vertical_compressive_continuum_stress_pa=stress)
