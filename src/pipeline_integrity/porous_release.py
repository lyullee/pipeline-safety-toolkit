"""Bounded constant-feed pool and pressure-driven spherical porous-front models.

These are idealized geometrical reductions, not a soil/site contamination solver.
No material properties, fitted site coefficients or detector performance are inferred.
"""
from __future__ import annotations

import math
from typing import Literal
from pydantic import model_validator
from .contracts import Contract, Positive, Nonnegative, Fraction


class SurfaceViscousSpillRequest(Contract):
    feed_m3_s: Positive
    kinematic_viscosity_m2_s: Positive
    times_s: tuple[Nonnegative, ...]
    gravity_m_s2: Positive = 9.80665
    soil_permeability_m2: Positive | None = None
    flat_axisymmetric_viscous_regime_reviewed: bool = False


class SurfaceViscousSpillResult(Contract):
    status: Literal['computed', 'out_of_scope']
    times_s: tuple[Nonnegative, ...]
    radius_m: tuple[Nonnegative, ...] = ()
    drainage_transition_s: Positive | None = None
    maximum_radius_m: Positive | None = None
    feed_volume_m3: tuple[Nonnegative, ...] = ()
    issues: tuple[str, ...] = ()
    evaporation_included: Literal[False] = False
    post_transition_storage_partition_resolved: Literal[False] = False


def evaluate_surface_viscous_spill(request: SurfaceViscousSpillRequest) -> SurfaceViscousSpillResult:
    """Constant-flux, horizontal viscous similarity radius with a drainage cap.

    The cap estimates the radius where drainage balances supply. Feed volume is
    a cumulative input ledger, never asserted to equal retained surface volume.
    The regime must be reviewed separately: this law has no inertial, turbulent,
    terrain, capillary, wind, evaporation or changing-feed branch.
    """
    q=SurfaceViscousSpillRequest.model_validate(request.model_dump())
    if not q.flat_axisymmetric_viscous_regime_reviewed:
        return SurfaceViscousSpillResult(status='out_of_scope',times_s=q.times_s,
            issues=('flat constant-feed axisymmetric viscous regime requires review',))
    rate=q.feed_m3_s; nu=q.kinematic_viscosity_m2_s; g=q.gravity_m_s2
    # Log scaling avoids intermediate powers of SI inputs overflowing needlessly.
    factor=math.exp(math.log(.715)+(math.log(g)-math.log(3*nu)+3*math.log(rate))/8)
    transition=None
    if q.soil_permeability_m2 is not None:
        transition=math.exp(math.log(.697)+(math.log(6/math.pi)+math.log(rate)+5*math.log(nu)
            -5*math.log(g)-4*math.log(q.soil_permeability_m2))/4)
    radius=tuple(factor*math.sqrt(t if transition is None else min(t,transition)) for t in q.times_s)
    return SurfaceViscousSpillResult(status='computed',times_s=q.times_s,radius_m=radius,
        drainage_transition_s=transition,maximum_radius_m=None if transition is None else factor*math.sqrt(transition),
        feed_volume_m3=tuple(rate*t for t in q.times_s))


class SphericalPorousFrontRequest(Contract):
    source_radius_m: Positive
    permeability_m2: Positive
    dynamic_viscosity_pa_s: Positive
    porosity: Fraction
    driving_pressure_pa: Positive
    times_s: tuple[Nonnegative,...]
    source_center_depth_m: Positive | None = None
    homogeneous_gravity_free_sharp_front_reviewed: bool = False

    @model_validator(mode='after')
    def domain(self):
        if self.porosity == 0:
            raise ValueError('porosity must be positive')
        if self.source_center_depth_m is not None and self.source_center_depth_m <= self.source_radius_m:
            raise ValueError('buried source center must be deeper than its radius')
        return self


class SphericalPorousFrontResult(Contract):
    status: Literal['computed','out_of_scope']
    times_s: tuple[Nonnegative,...]
    radius_m: tuple[Positive,...] = ()
    cumulative_intruded_liquid_m3: tuple[Nonnegative,...] = ()
    instantaneous_feed_m3_s: tuple[Positive | None,...] = ()
    asymptotic_feed_m3_s: Positive | None = None
    exact_breakthrough_s: Positive | None = None
    asymptotic_breakthrough_s: Positive | None = None
    unperturbed_sphere_surface_intersection_m: tuple[Nonnegative,...] | None = None
    issues: tuple[str,...] = ()
    post_breakthrough_flow_field_resolved: Literal[False] = False


def _growth_offset(time_scale: float) -> float:
    """Invert y²/2+y³/3 without subtracting almost equal source radii."""
    if time_scale == 0:
        return 0.0
    upper=min(math.sqrt(time_scale)*math.sqrt(2),math.exp((math.log(time_scale)+math.log(3))/3))
    lower=0.0
    for _ in range(110):
        y=(lower+upper)/2
        # Both positive terms are individually below the target near the root.
        value=y*y*(.5+y/3)
        if value < time_scale:
            lower=y
        else:
            upper=y
        if upper-lower <= 4e-16*upper:
            break
    return (lower+upper)/2


def evaluate_spherical_porous_front(request: SphericalPorousFrontRequest) -> SphericalPorousFrontResult:
    """Finite-radius, constant-pressure radial Darcy sharp-front solution.

    The initially wetted source sphere is excluded from reported new pore volume.
    At t=0 the ideal pressure step has infinite instantaneous flow, represented
    explicitly by None; its time-integrated inflow remains finite. The sphere
    intersection after reaching the surface is only an unperturbed geometrical
    diagnostic, not a continued free-boundary infiltration solution.
    """
    q=SphericalPorousFrontRequest.model_validate(request.model_dump())
    if not q.homogeneous_gravity_free_sharp_front_reviewed:
        return SphericalPorousFrontResult(status='out_of_scope',times_s=q.times_s,
            issues=('homogeneous dry sharp-front, gravity-free fixed-pressure assumptions require review',))
    rs=q.source_radius_m
    mobility=q.permeability_m2*q.driving_pressure_pa/q.dynamic_viscosity_pa_s
    scale=mobility/q.porosity/rs**2
    asymptotic=4*math.pi*mobility*rs
    ys=tuple(_growth_offset(scale*t) for t in q.times_s)
    radius=tuple(rs*(1+y) for y in ys)
    volume=tuple(4*math.pi*q.porosity*rs**3*y*(1+y+y*y/3) for y in ys)
    flow=tuple(None if y==0 else asymptotic*(1+1/y) for y in ys)
    exact=approx=None; footprint=None
    if q.source_center_depth_m is not None:
        d=q.source_center_depth_m; y=(d-rs)/rs
        exact=y*y*(.5+y/3)/scale
        approx=4*math.pi*q.porosity*d**3/(3*asymptotic)
        footprint=tuple(0.0 if t<=exact else math.sqrt(max(0,(r-d)*(r+d))) for t,r in zip(q.times_s,radius))
    return SphericalPorousFrontResult(status='computed',times_s=q.times_s,radius_m=radius,
        cumulative_intruded_liquid_m3=volume,instantaneous_feed_m3_s=flow,
        asymptotic_feed_m3_s=asymptotic,exact_breakthrough_s=exact,asymptotic_breakthrough_s=approx,
        unperturbed_sphere_surface_intersection_m=footprint)
