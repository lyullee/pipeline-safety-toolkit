"""Conservative wet/filled circular flow with explicit saturation-pressure closure.

One shared circular FV operator advances reference-density liquid inventory and
mass-scaled discharge; full sections may have negative gauge pressure while
remaining above supplied vapor pressure. Finite partially filled sections have
a massless constant-pressure vapor surface. Thermal phase kinetics are omitted.
"""
from __future__ import annotations
from dataclasses import dataclass
import math
import numpy as np
from pydantic import Field,model_validator
from .contracts import Contract,Positive
from .gravity_free_surface_pipe import CircularFreeSurfaceSettings,CircularFreeSurfaceResult,FreeSurfaceBoundaryStage,_solve_circular_pipe_fv
from .saturated_circular_closure import SaturatedCircularClosure


class SaturatedMixedPipeSettings(Contract):
    numerics: CircularFreeSurfaceSettings
    saturation_pressure_pa_abs: Positive
    barotropic_sound_speed_m_s: Positive
    maximum_fractional_density_increase: Positive = Field(default=.05,le=.1)
    maximum_acoustic_mach: Positive = Field(default=.05,le=.1)
    bed_elevations_m: tuple[float,...] | None = None
    maximum_supported_bed_slope: Positive = Field(default=.1,le=.2)
    prismatic_barotropic_single_liquid_and_massless_saturation_reviewed: bool = False

    @model_validator(mode='after')
    def geometry(self):
        if self.bed_elevations_m is not None and self.numerics.signed_downhill_angle_rad!=0:
            raise ValueError('bed reconstruction uses a horizontal small-slope reference, not an additional uniform inclination')
        return self


@dataclass(frozen=True)
class SaturatedMixedObservation:
    time_s: float
    effective_area_mass_scaled_discharge: np.ndarray
    equivalent_head_m: np.ndarray
    physical_depth_m: np.ndarray
    physical_wet_area_m2: np.ndarray
    vapor_area_m2: np.ndarray
    velocity_m_s: np.ndarray
    crown_pressure_pa_abs: np.ndarray
    mean_liquid_pressure_pa_abs: np.ndarray
    free_surface: np.ndarray


@dataclass(frozen=True)
class SaturatedMixedPipeResult:
    core: CircularFreeSurfaceResult
    final: SaturatedMixedObservation
    observations: tuple[SaturatedMixedObservation,...]
    initial_liquid_mass_kg: float
    final_liquid_mass_kg: float
    final_geometric_vapor_volume_m3: float
    closure_identity: dict
    vapor_mass_solved: bool = False
    latent_or_thermal_energy_solved: bool = False


def mixed_pipe_closure(settings:SaturatedMixedPipeSettings):
    q=SaturatedMixedPipeSettings.model_validate(settings.model_dump(mode='python'));n=q.numerics
    c=SaturatedCircularClosure(n.inner_diameter_m,n.density_kg_m3,q.saturation_pressure_pa_abs,
        q.barotropic_sound_speed_m_s,n.gravity_m_s2*math.cos(n.signed_downhill_angle_rad),
        maximum_fractional_density_increase=q.maximum_fractional_density_increase)
    c.maximum_velocity_m_s=q.maximum_acoustic_mach*q.barotropic_sound_speed_m_s
    return c


def saturated_mixed_area_discharge(equivalent_head_m,velocity_m_s,settings:SaturatedMixedPipeSettings):
    """A_eff is liquid mass per length/rho_sat; Q is mass flow/rho_sat.

    Actual wet area and volume flow differ from these conserved coordinates
    under compression. No numerical state is clipped to the physical bore area.
    """
    c=mixed_pipe_closure(settings);section=c.at_head(equivalent_head_m)
    area,velocity=np.broadcast_arrays(section.area_m2,np.asarray(velocity_m_s,dtype=float))
    if not np.isfinite(velocity).all() or np.any(abs(velocity)>c.maximum_velocity_m_s):raise ValueError('finite reviewed liquid velocity required')
    return np.stack((area,area*velocity),axis=-1)


def solve_saturated_mixed_pipe(initial_effective_area_discharge,settings:SaturatedMixedPipeSettings,*,
    boundary_stages:tuple[FreeSurfaceBoundaryStage,...] | None=None):
    """Reuse the circular FV kernel with one smooth barotropic/saturation law.

    Head-based hydrostatic reconstruction preserves a resting surface/piezometric
    level on a supplied small-slope bed. Sources and boundary momentum budgets
    retain the actual reconstruction pressure corrections. Mechanical energy is
    a barotropic entropy diagnostic, not a latent/thermal-energy balance.
    """
    q=SaturatedMixedPipeSettings.model_validate(settings.model_dump(mode='python'))
    if not q.prismatic_barotropic_single_liquid_and_massless_saturation_reviewed:
        raise ValueError('barotropic EOS, prismatic geometry, small-slope and instantaneous massless saturation require review')
    state=np.asarray(initial_effective_area_discharge,dtype=float);c=mixed_pipe_closure(q);n=q.numerics
    if state.ndim!=2 or state.shape[1]!=2:raise ValueError('effective area/mass-scaled discharge cell pairs required')
    if q.bed_elevations_m is not None:
        bed=np.asarray(q.bed_elevations_m,dtype=float)
        if bed.shape!=(len(state),) or not np.isfinite(bed).all():raise ValueError('finite bed elevation for every cell required')
        if len(bed)>1 and np.max(abs(np.diff(bed)))/(n.length_m/len(state))>q.maximum_supported_bed_slope:
            raise ValueError('bed profile exceeds reviewed small-slope support')
    core=_solve_circular_pipe_fv(state,n,boundary_stages=boundary_stages,closure=c,bed_elevations_m=q.bed_elevations_m)
    def observe(time,u):
        sec=c.from_area(u[:,0]);velocity=u[:,1]/u[:,0]
        values=[u,sec.depth_m,sec.physical_depth_m,sec.physical_wet_area_m2,
            c.full_physical_area-sec.physical_wet_area_m2,velocity,sec.crown_pressure_pa_abs,
            sec.mean_liquid_pressure_pa_abs,sec.free_surface]
        arrays=[np.array(a,copy=True) for a in values]
        for a in arrays:a.setflags(write=False)
        return SaturatedMixedObservation(time,*arrays)
    snapshots=tuple(observe(s.time_s,s.area_discharge) for s in core.observations)
    final=observe(core.step_times_s[-1],core.area_discharge)
    return SaturatedMixedPipeResult(core,final,snapshots,float(core.ledger.initial_mass_momentum[0]),
        float(core.ledger.final_mass_momentum[0]),float(n.length_m/len(state)*np.sum(final.vapor_area_m2)),
        dict(saturation_pressure_pa_abs=c.p_sat,density_at_saturation_kg_m3=c.density,
            barotropic_sound_speed_m_s=c.sound_speed,full_saturated_effective_area_m2=c.full_effective_area,
            physical_bore_area_m2=c.full_physical_area,maximum_fractional_density_increase=c.maximum_fractional_density_increase,
            conserved_coordinates='liquid_mass_per_length/rho_sat and liquid_mass_flow/rho_sat',
            phase_rule='wet free surface at supplied vapor pressure, or liquid-filled crown pressure at least vapor pressure',
            scheme='shared circular finite volumes with barotropic hydrostatic closure; no artificial slot'))
