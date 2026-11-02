"""Supplied-parameter Karman mean-profile approximation in a circular pipe.

This empirical eddy-viscosity construction is not a resolved viscous-wall or
axis-regular RANS solution. Its finite centerline slope and singular centerline
curvature are exposed instead of being silently replaced by regular limits.
"""
from __future__ import annotations

import math

from pydantic import Field,model_validator

from .contracts import Calculation,Contract,Name,Nonnegative,Positive


class KarmanRadialProfileRequest(Contract):
    common_state_basis_id: Name
    radius_m: Positive
    density_kg_m3: Positive
    kinematic_viscosity_m2_s: Positive
    pressure_drop_per_length_Pa_m: Positive
    mixing_constant: Positive
    wall_interaction_parameter: Positive
    roughness_wall_constant: Nonnegative
    roughness_m: Nonnegative
    radial_positions_m: tuple[Nonnegative,...] = Field(min_length=1,max_length=10000)
    flow_direction: int = Field(default=1,ge=-1,le=1)
    normalized_integral_absolute_tolerance: Positive = 1e-10
    normalized_integral_relative_tolerance: Positive = Field(default=1e-9,le=1e-3)
    maximum_quadrature_subintervals: int = Field(default=400,ge=10,le=10000)
    empirical_profile_and_wall_parameter_domain_reviewed: bool = False

    @model_validator(mode="after")
    def support(self):
        if self.flow_direction not in (-1,1):
            raise ValueError("profile flow direction must be +1 or -1")
        if any(r>self.radius_m for r in self.radial_positions_m):
            raise ValueError("profile observations must lie within the supplied radius")
        if any(b<=a for a,b in zip(self.radial_positions_m,self.radial_positions_m[1:])):
            raise ValueError("profile observation radii must strictly increase")
        return self


def _shape_gap(h,offset):
    if h==0:return 0.0
    y=h/offset
    if y<1e-3:
        remainder=math.fsum((-1)**(k+1)*y**k/k for k in range(2,11))
        return math.log1p(y)+offset*remainder
    logarithm=math.log1p(y) if math.isfinite(y) else math.log(h+offset)-math.log(offset)
    return (1+offset)*logarithm-h


def _shape(s,offset):
    return _shape_gap(1-s,offset)


def evaluate_karman_radial_profile(request:KarmanRadialProfileRequest)->Calculation:
    from scipy.integrate import quad
    q=KarmanRadialProfileRequest.model_validate(request.model_dump(mode="python"))
    if not q.empirical_profile_and_wall_parameter_domain_reviewed:
        raise ValueError("empirical mean-profile and wall-parameter applicability require review")
    try:
        star=math.sqrt(q.pressure_drop_per_length_Pa_m*q.radius_m/(2*q.density_kg_m3))
        offset=math.fsum((q.kinematic_viscosity_m2_s,q.roughness_wall_constant*star*q.roughness_m))/(
            2*q.wall_interaction_parameter*q.mixing_constant*q.radius_m*star)
        if not math.isfinite(offset) or offset<=0 or not math.isfinite(star) or star<=0:
            raise ValueError("Karman profile wall coordinate exceeds finite positive support")
        center_shape=_shape(0.0,offset)
        maximum=star/q.mixing_constant*center_shape
        if not math.isfinite(maximum) or maximum<=0:
            raise ValueError("Karman profile velocity exceeds finite positive support")
        moments=[]
        errors=[]
        for power in (1,2,3):
            result=quad(lambda s:4*s**3*(_shape(s,offset)/center_shape)**power,0,1,
                epsabs=q.normalized_integral_absolute_tolerance,epsrel=q.normalized_integral_relative_tolerance,
                limit=q.maximum_quadrature_subintervals,full_output=1)
            if len(result)!=3:
                raise ValueError("Karman radial moment quadrature did not converge")
            value,error,_=result
            if value<=0 or not math.isfinite(value) or error>max(q.normalized_integral_absolute_tolerance,
                    q.normalized_integral_relative_tolerance*abs(value)):
                raise ValueError("Karman radial moment fails its declared integration tolerance")
            moments.append(value)
            errors.append(error)
        mean=maximum*moments[0]
        rows=[]
        for r in q.radial_positions_m:
            s=math.sqrt(r/q.radius_m)
            h=(q.radius_m-r)/(q.radius_m*(1+s))
            denominator=offset+h
            velocity=q.flow_direction*star/q.mixing_constant*_shape_gap(h,offset)
            gradient=-q.flow_direction*star/(2*q.mixing_constant*q.radius_m*denominator)
            curvature=None if r==0 else -q.flow_direction*star/(4*q.mixing_constant*q.radius_m**2*s*denominator**2)
            eddy_viscosity=2*q.mixing_constant*star*r*denominator
            rows.append(dict(radius_m=r,velocity_m_s=velocity,velocity_gradient_per_s=gradient,
                velocity_curvature_per_m_s=curvature,modeled_effective_eddy_viscosity_m2_s=eddy_viscosity,
                shear_stress_Pa=-q.flow_direction*q.pressure_drop_per_length_Pa_m*r/2))
        values=dict(radial_profile=rows,wall_coordinate_offset=offset,
            friction_velocity_m_s=star,wall_shear_magnitude_Pa=q.density_kg_m3*star**2,
            centerline_velocity_m_s=q.flow_direction*maximum,mean_velocity_m_s=q.flow_direction*mean,
            volume_flow_m3_s=q.flow_direction*math.pi*q.radius_m**2*mean,
            reynolds_number=2*q.radius_m*mean/q.kinematic_viscosity_m2_s,
            darcy_factor_from_exact_profile_mean=8*(star/mean)**2,
            mean_square_velocity_m2_s2=maximum**2*moments[1],
            signed_mean_cube_velocity_m3_s3=q.flow_direction*maximum**3*moments[2],
            mean_square_over_mean_squared=moments[1]/moments[0]**2,
            kinetic_energy_flux_correction=moments[2]/moments[0]**3,
            normalized_moment_quadrature_errors=errors,
            regular_axis_boundary_satisfied=False,molecular_viscous_wall_resolved=False,
            centerline_curvature_status="singular_in_this_approximation")
        numeric=[v for v in values.values() if isinstance(v,(int,float)) and not isinstance(v,bool)]
        numeric.extend(v for row in rows for v in row.values() if v is not None)
        if not all(math.isfinite(v) for v in numeric):
            raise ValueError("Karman profile outputs exceed finite arithmetic")
    except (ArithmeticError,ValueError) as error:
        return Calculation(values={},status="out_of_scope",notes=(str(error),))
    return Calculation(values=values,notes=(
        "Conditional empirical mean-profile approximation, with supplied calibration parameters and a pressure-driven wall stress; no turbulent-regime identification or measured calibration.",
        "The centerline slope is nonzero and curvature singular. Molecular wall viscosity is not resolved; the modeled eddy viscosity must not be interpreted as a universal near-wall total viscosity.",
        "Exact profile moments differ from rounded asymptotic bulk-friction formulas. Mean-square and kinetic-energy-flux correction factors are distinct observations.",))
