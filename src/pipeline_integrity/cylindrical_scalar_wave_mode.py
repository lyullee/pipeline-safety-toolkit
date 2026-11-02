"""Regular axisymmetric scalar-wave modes through SciPy Bessel functions."""
import math
from pydantic import Field
from .contracts import Contract,Positive,Nonnegative,Name

class CylindricalScalarWaveModeRequest(Contract):
    outer_zero_displacement_radius_m: Positive
    wave_speed_m_s: Positive
    mode_number: int = Field(ge=1,le=100)
    initial_axis_velocity_m_s: float
    query_radii_m: tuple[Nonnegative,...] = Field(min_length=1,max_length=10000)
    query_times_s: tuple[Nonnegative,...] = Field(min_length=1,max_length=10000)
    maximum_output_points: int = Field(default=100000,ge=1,le=1000000)
    scalar_wave_geometry_and_boundary_basis_id: Name
    regular_axis_scalar_wave_and_outer_zero_boundary_reviewed: bool = False

def evaluate_cylindrical_scalar_wave_mode(request: CylindricalScalarWaveModeRequest):
    """Return u_tt=c²(u_rr+u_r/r) for one regular zero-boundary radial mode.

    The initial velocity profile is a Bessel eigenfunction, not a point impulse
    or uniform source. This scalar field does not infer actual blast pressure,
    nonlinear shocks, radiation, target reflection or material displacement.
    """
    from scipy.special import j0,j1,jn_zeros
    q=CylindricalScalarWaveModeRequest.model_validate(request.model_dump())
    R=q.outer_zero_displacement_radius_m
    if any(r>R for r in q.query_radii_m):raise ValueError('query radius outside scalar-wave boundary')
    if len(q.query_radii_m)*len(q.query_times_s)>q.maximum_output_points:raise ValueError('scalar-wave output budget exceeded')
    base=dict(basis_id=q.scalar_wave_geometry_and_boundary_basis_id,actual_blast_pressure_or_reflection_qualified=False)
    if not q.regular_axis_scalar_wave_and_outer_zero_boundary_reviewed:return dict(status='needs_data',**base)
    alpha=float(jn_zeros(0,q.mode_number)[-1]);omega=q.wave_speed_m_s*alpha/R
    rows=[]
    for t in q.query_times_s:
        for r in q.query_radii_m:
            phi=float(j0(alpha*r/R))
            rows.append(dict(time_s=t,radius_m=r,scalar_displacement_m=q.initial_axis_velocity_m_s*phi*math.sin(omega*t)/omega,
                scalar_velocity_m_s=q.initial_axis_velocity_m_s*phi*math.cos(omega*t),
                scalar_radial_displacement_gradient=-q.initial_axis_velocity_m_s*float(j1(alpha*r/R))*math.sin(omega*t)/q.wave_speed_m_s))
    return dict(status='computed',**base,angular_frequency_rad_s=omega,dimensionless_radial_eigenvalue=alpha,rows=rows)
