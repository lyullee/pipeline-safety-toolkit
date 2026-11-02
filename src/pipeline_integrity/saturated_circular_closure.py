"""Hydrostatic barotropic liquid with a massless constant-pressure vapor surface.

The caller supplies rho_sat, p_sat and c²=dp/drho. Integrating hydrostatic density
through the circular section gives one smooth mass/pressure law spanning a wet
free surface and a liquid-filled section. No fictitious slot, negative absolute
pressure, vapor mass, latent heat, air entrainment or metastable liquid is inferred.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np
from .gravity_free_surface_pipe import circular_section_from_depth,FreeSurfaceDomainError


@dataclass(frozen=True)
class SaturatedCircularSection:
    area_m2: np.ndarray
    depth_m: np.ndarray
    physical_depth_m: np.ndarray
    physical_wet_area_m2: np.ndarray
    top_width_m: np.ndarray
    hydraulic_radius_m: np.ndarray
    hydrostatic_integral_m3: np.ndarray
    first_height_moment_m3: np.ndarray
    pressure_potential_m4_s2: np.ndarray
    storage_derivative_width_m: np.ndarray
    wave_speed_m_s: np.ndarray
    crown_pressure_pa_abs: np.ndarray
    mean_liquid_pressure_pa_abs: np.ndarray
    free_surface: np.ndarray


class SaturatedCircularClosure:
    """Reference-density liquid area and its barotropic pressure potential.

    A_eff=integral(rho/rho_sat dy dz), rho(y)=rho_sat*exp(g_n*(h-y)/c²).
    h is wet depth measured above the invert below filling, and a pressure-head
    coordinate once the pipe fills. The free-surface limit c->infinity is the shared incompressible
    circular geometry. At filling, dP/dA remains finite and tends continuously to
    c². Saturation/constant sound speed are reviewed input approximations.
    """
    def __init__(self,inner_diameter_m,density_at_saturation_kg_m3,saturation_pressure_pa_abs,
        sound_speed_m_s,normal_gravity_m_s2,*,maximum_fractional_density_increase=.05):
        values=(inner_diameter_m,density_at_saturation_kg_m3,saturation_pressure_pa_abs,sound_speed_m_s,normal_gravity_m_s2)
        if not all(math.isfinite(x) and x>0 for x in values):raise ValueError('positive finite geometry and barotropic material inputs required')
        if not math.isfinite(maximum_fractional_density_increase) or not 0<maximum_fractional_density_increase<=.1:
            raise ValueError('explicit small-density-change support must be positive and no greater than0.1')
        self.diameter=inner_diameter_m;self.density=density_at_saturation_kg_m3;self.p_sat=saturation_pressure_pa_abs
        self.sound_speed=sound_speed_m_s;self.c2=sound_speed_m_s**2;self.g=normal_gravity_m_s2
        self.kappa=self.g/self.c2;self.maximum_fractional_density_increase=maximum_fractional_density_increase
        if not math.isfinite(self.c2) or self.kappa<=0:raise ValueError('sound-speed scaling exceeds finite support')
        self.maximum_head=math.log1p(maximum_fractional_density_increase)/self.kappa
        if self.maximum_head<=self.diameter:raise ValueError('hydrostatic density change at a full section exceeds reviewed support')
        nodes,weights=np.polynomial.legendre.leggauss(24)
        self._nodes=(nodes+1)/2;self._weights=weights/2
        self.full_physical_area=math.pi*inner_diameter_m**2/4
        self.full_pressure_potential=float(self._free_pressure(np.asarray(self.diameter)))
        self.full_effective_area=self.full_physical_area+self.full_pressure_potential/self.c2

    def _free_pressure(self,depth):
        h=np.asarray(depth,dtype=float);r=self.diameter/2
        theta=2*np.arcsin(np.sqrt(h/self.diameter))
        angle=theta[...,None]*self._nodes
        y=2*r*np.sin(angle/2)**2
        area_element=2*r*r*np.sin(angle)**2
        return self.c2*theta*np.sum(self._weights*np.expm1(self.kappa*(h[...,None]-y))*area_element,axis=-1)

    def at_head(self,head_m,*,supplied_area=None):
        h=np.asarray(head_m,dtype=float)
        if not np.isfinite(h).all() or np.any(h<=0) or np.any(h>self.maximum_head):
            raise FreeSurfaceDomainError('mixed-flow head leaves positive wet or reviewed density support')
        free=h<self.diameter;wet=np.minimum(h,self.diameter)
        shape=h.shape
        geometric=np.full(shape,self.full_physical_area);width=np.zeros(shape)
        radius=np.full(shape,self.diameter/4);pressure=np.empty(shape);area=np.empty(shape)
        if np.any(free):
            section=circular_section_from_depth(h[free],self.diameter)
            geometric[free]=section.area_m2;width[free]=section.top_width_m;radius[free]=section.hydraulic_radius_m
            pressure[free]=self._free_pressure(h[free]);area[free]=geometric[free]+pressure[free]/self.c2
        if np.any(~free):
            growth=np.expm1(self.kappa*(h[~free]-self.diameter))
            area[~free]=self.full_effective_area*(1+growth)
            pressure[~free]=self.full_pressure_potential+self.c2*self.full_effective_area*growth
        if supplied_area is not None:
            # Retain the conserved input: inversion tolerance never clips mass.
            area=np.array(supplied_area,dtype=float,copy=True)
        derivative=width+self.kappa*area
        speed=np.sqrt(self.g*area/derivative)
        crown=self.p_sat+np.where(free,0.,self.density*self.c2*np.expm1(self.kappa*(h-self.diameter)))
        mean=self.p_sat+self.density*pressure/geometric
        internal=self.g*h*area-pressure
        if not all(np.isfinite(x).all() for x in (area,pressure,derivative,speed,crown,mean,internal)) or np.any(area<=0) or np.any(derivative<=0):
            raise FreeSurfaceDomainError('mixed circular closure exceeds finite positive support')
        return SaturatedCircularSection(area,h,wet,geometric,width,radius,pressure/self.g,internal/self.g,
            pressure,derivative,speed,crown,mean,free)

    def from_area(self,effective_area_m2):
        target=np.asarray(effective_area_m2,dtype=float)
        if not np.isfinite(target).all() or np.any(target<=0):raise FreeSurfaceDomainError('positive finite effective liquid area required')
        free=target<self.full_effective_area
        h=np.empty_like(target)
        if np.any(~free):
            h[~free]=self.diameter+np.log1p((target[~free]-self.full_effective_area)/self.full_effective_area)/self.kappa
        if np.any(free):
            requested=target[free];low=np.zeros_like(requested);high=np.full_like(requested,self.diameter)
            guess=self.diameter*(requested/self.full_effective_area)**(2/3)
            tolerance=24*np.finfo(float).eps*requested
            for _ in range(70):
                state=self.at_head(guess);residual=state.area_m2-requested
                active=abs(residual)>tolerance
                if not np.any(active):break
                low=np.where(active&(residual<0),guess,low);high=np.where(active&(residual>0),guess,high)
                trial=guess-residual/state.storage_derivative_width_m
                trial=np.where((trial<=low)|(trial>=high)|~np.isfinite(trial),(low+high)/2,trial)
                guess=np.where(active,trial,guess)
            else:raise FreeSurfaceDomainError('mixed circular area inversion did not converge')
            h[free]=guess
        return self.at_head(h,supplied_area=target)
