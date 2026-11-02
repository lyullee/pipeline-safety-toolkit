"""Nonlinear wet free-surface Saint-Venant flow in one rigid circular pipe.

Area and discharge are transported conservatively. Circular geometry, rather
than a rectangular-channel depth shortcut, supplies the hydrostatic force.
Dry and full/pressurized states are outside this wet free-surface model; no
area clipping or hidden pressurization slot is used to continue through them.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal

import numpy as np
from pydantic import Field, model_validator

from .contracts import Contract, Positive


class FreeSurfaceDomainError(ValueError):
    """Geometry/state is outside the strictly wet, partially filled domain."""


class FreeSurfaceEvolutionError(RuntimeError):
    """No admissible wet free-surface continuation was resolved by retries."""


@dataclass(frozen=True)
class CircularWetSection:
    area_m2: np.ndarray
    depth_m: np.ndarray
    wetted_perimeter_m: np.ndarray
    top_width_m: np.ndarray
    hydraulic_radius_m: np.ndarray
    hydrostatic_integral_m3: np.ndarray
    first_height_moment_m3: np.ndarray


def _angular_area(theta):
    # theta is the half central angle. Taylor evaluation removes cancellation.
    series=np.zeros_like(theta)
    for k in range(8,0,-1):
        series=series*theta*theta+(-1)**(k+1)*2.**(2*k)/math.factorial(2*k+1)
    return np.where(theta<.15,theta**3*series,theta-np.sin(theta)*np.cos(theta))


def _angular_pressure_integral(theta):
    # Integral of [t-sin(t)cos(t)]sin(t) dt, generated from its two Taylor series.
    series=np.zeros_like(theta)
    for m in range(8,0,-1):
        coefficient=sum((-1)**(k+1)*2.**(2*k)/math.factorial(2*k+1)
            *(-1)**(m-k)/math.factorial(2*(m-k)+1) for k in range(1,m+1))/(2*m+3)
        series=series*theta*theta+coefficient
    direct=-np.cos(theta)*_angular_area(theta)+(2/3)*np.sin(theta)**3
    return np.where(theta<.15,theta**5*series,direct)


def _radius(diameter):
    if not math.isfinite(diameter) or diameter<=0:
        raise FreeSurfaceDomainError("finite positive circular-pipe diameter required")
    radius=diameter/2
    full_area=math.pi*radius*radius
    if not math.isfinite(full_area) or full_area<=0:
        raise FreeSurfaceDomainError("circular geometry exceeds finite numerical support")
    return radius,full_area


def _section(theta,diameter,*,supplied_area=None,supplied_depth=None):
    r,_=_radius(diameter)
    area=r*r*_angular_area(theta) if supplied_area is None else np.array(supplied_area,copy=True)
    depth=2*r*np.sin(theta/2)**2 if supplied_depth is None else np.array(supplied_depth,copy=True)
    perimeter=2*r*theta
    width=2*r*np.sin(theta) if supplied_depth is None else 2*np.sqrt(depth*(diameter-depth))
    integral=r**3*_angular_pressure_integral(theta)
    moment=depth*area-integral
    radius=area/perimeter
    if any(not np.all(np.isfinite(a)) or np.any(a<=0) for a in (area,depth,perimeter,width,integral,moment,radius)):
        raise FreeSurfaceDomainError("wet section is outside positive finite numerical support")
    return CircularWetSection(area,depth,perimeter,width,radius,integral,moment)


def circular_section_from_depth(depth_m,inner_diameter_m:float) -> CircularWetSection:
    """Strictly 0<h<D, with depth measured from the bottom in the cross-section."""
    _radius(inner_diameter_m)
    depth=np.asarray(depth_m,dtype=float)
    if not np.all(np.isfinite(depth)) or np.any(depth<=0) or np.any(depth>=inner_diameter_m):
        raise FreeSurfaceDomainError("strict wet free surface requires 0<depth<diameter")
    cap=np.minimum(depth,inner_diameter_m-depth)/inner_diameter_m
    theta=2*np.arcsin(np.sqrt(cap))
    theta=np.where(depth>inner_diameter_m/2,math.pi-theta,theta)
    return _section(theta,inner_diameter_m,supplied_depth=depth)


def circular_section_from_area(area_m2,inner_diameter_m:float) -> CircularWetSection:
    """Invert the circular wet area without altering its transported mass value."""
    r,full=_radius(inner_diameter_m)
    area=np.asarray(area_m2,dtype=float)
    if not np.all(np.isfinite(area)) or np.any(area<=0) or np.any(area>=full):
        raise FreeSurfaceDomainError("dry/full/pressurized area is outside the selected model")
    upper=area>full/2
    target=np.where(upper,full-area,area)/(r*r)
    if np.any(target<=0) or not np.all(np.isfinite(target)):
        raise FreeSurfaceDomainError("circular area inversion exceeds numerical support")
    theta=np.cbrt(1.5*target)
    low=np.zeros_like(theta);high=np.full_like(theta,math.pi/2)
    tolerance=16*np.finfo(float).eps*target
    theta=np.where(np.abs(target-math.pi/2)<=tolerance,math.pi/2,theta)
    for _ in range(45):
        residual=_angular_area(theta)-target
        active=np.abs(residual)>tolerance
        if not np.any(active):break
        low=np.where(active & (residual<0),theta,low)
        high=np.where(active & (residual>0),theta,high)
        trial=theta-residual/(2*np.sin(theta)**2)
        trial=np.where((trial<=low)|(trial>=high)|~np.isfinite(trial),.5*(low+high),trial)
        theta=np.where(active,trial,theta)
    else:raise FreeSurfaceDomainError("circular area inversion failed its relative residual bound")
    theta=np.where(upper,math.pi-theta,theta)
    return _section(theta,inner_diameter_m,supplied_area=area)


class FreeSurfaceBoundary(Contract):
    kind: Literal["periodic","reflective","extrapolated","prescribed"]
    prescribed_area_discharge: tuple[float,float] | None = None

    @model_validator(mode="after")
    def prescribed_state(self):
        if (self.kind=="prescribed") != (self.prescribed_area_discharge is not None):
            raise ValueError("only prescribed boundaries require an (area,discharge) ghost state")
        return self


class FreeSurfaceBoundaryStage(Contract):
    start_time_s: float
    left: FreeSurfaceBoundary
    right: FreeSurfaceBoundary

    @model_validator(mode="after")
    def periodic_pair(self):
        if (self.left.kind=="periodic") != (self.right.kind=="periodic"):
            raise ValueError("periodicity requires both pipe ends")
        return self


class CircularFreeSurfaceSettings(Contract):
    length_m: Positive
    inner_diameter_m: Positive
    duration_s: Positive
    density_kg_m3: Positive = 1000.0
    gravity_m_s2: Positive = 9.80665
    signed_downhill_angle_rad: float = Field(default=0.0,gt=-math.pi/2,lt=math.pi/2)
    friction_model: Literal["none","chezy"] = "none"
    chezy_coefficient_m_half_per_s: Positive | tuple[Positive,...] | None = None
    start_time_s: float = 0.0
    cfl: Positive = Field(default=.4,le=.5)
    source_fraction_per_step: Positive = Field(default=.2,le=.5)
    maximum_step_s: Positive = 1e100
    minimum_retry_step_s: Positive = 1e-13
    maximum_retries: int = Field(default=30,ge=0,le=100)
    maximum_steps: int = Field(default=200000,ge=1,le=10000000)
    observation_times_s: tuple[float,...] = Field(default=(),max_length=1000000)

    @model_validator(mode="after")
    def clocks_and_closure(self):
        finish=self.start_time_s+self.duration_s
        if not math.isfinite(finish) or finish<=self.start_time_s:
            raise ValueError("duration must advance a finite clock")
        if (self.friction_model=="chezy") != (self.chezy_coefficient_m_half_per_s is not None):
            raise ValueError("Chezy resistance requires a supplied dimensional coefficient; inviscid mode omits it explicitly")
        if any(b<=a for a,b in zip(self.observation_times_s,self.observation_times_s[1:])):
            raise ValueError("observation times must increase strictly")
        if self.observation_times_s and (self.observation_times_s[0]<self.start_time_s or self.observation_times_s[-1]>finish):
            raise ValueError("observation times must lie within the solve window")
        return self


@dataclass(frozen=True)
class FreeSurfaceObservation:
    time_s: float
    area_discharge: np.ndarray
    depth_m: np.ndarray
    velocity_m_s: np.ndarray


@dataclass(frozen=True)
class FreeSurfaceLedger:
    initial_mass_momentum: np.ndarray
    final_mass_momentum: np.ndarray
    integrated_boundary_inflow: np.ndarray
    integrated_distributed_sources: np.ndarray
    residual: np.ndarray


@dataclass(frozen=True)
class FreeSurfaceMechanicalEnergy:
    initial_J: float
    final_J: float
    integrated_boundary_flux_J: float
    integrated_gravity_work_J: float
    integrated_chezy_dissipation_J: float
    numerical_and_shock_balance_indicator_J: float
    exact_energy_conservation_claimed: bool = False


@dataclass(frozen=True)
class CircularFreeSurfaceResult:
    cell_centers_m: np.ndarray
    area_discharge: np.ndarray
    depth_m: np.ndarray
    velocity_m_s: np.ndarray
    wave_speed_m_s: np.ndarray
    step_times_s: tuple[float,...]
    event_times_s: tuple[float,...]
    observations: tuple[FreeSurfaceObservation,...]
    ledger: FreeSurfaceLedger
    mechanical_energy: FreeSurfaceMechanicalEnergy
    accepted_steps: int
    rejected_steps: int
    scheme: str = "circular_barotropic_Rusanov_SSPRK2_strict_wet_free_surface"


def free_surface_area_discharge(depth_m,velocity_m_s,inner_diameter_m):
    geometry=circular_section_from_depth(depth_m,inner_diameter_m)
    a,v=np.broadcast_arrays(geometry.area_m2,np.asarray(velocity_m_s,dtype=float))
    u=np.stack((a,a*v),axis=-1)
    if not np.all(np.isfinite(u)):raise FreeSurfaceDomainError("state exceeds finite numerical support")
    return u


def _recover(u,q,closure=None):
    if not np.all(np.isfinite(u)):raise FreeSurfaceDomainError("nonfinite area/discharge")
    section=circular_section_from_area(u[:,0],q.inner_diameter_m) if closure is None else closure.from_area(u[:,0])
    velocity=u[:,1]/u[:,0]
    if closure is not None and hasattr(closure,'maximum_velocity_m_s') and np.any(abs(velocity)>closure.maximum_velocity_m_s):
        raise FreeSurfaceDomainError('mixed liquid velocity exceeds reviewed acoustic-Mach support')
    speed=(np.sqrt(q.gravity_m_s2*math.cos(q.signed_downhill_angle_rad)*section.area_m2/section.top_width_m)
           if closure is None else section.wave_speed_m_s)
    if not np.all(np.isfinite(velocity)) or not np.all(np.isfinite(speed)) or np.any(speed<=0):
        raise FreeSurfaceDomainError("wave/velocity state exceeds finite wet support")
    return section,velocity,speed


def _snapshot(time,u,q,closure=None):
    section,v,_=_recover(u,q,closure)
    arrays=[np.array(x,copy=True) for x in (u,section.depth_m,v)]
    for a in arrays:a.setflags(write=False)
    return FreeSurfaceObservation(time,*arrays)


def solve_circular_free_surface_pipe(initial_area_discharge,settings:CircularFreeSurfaceSettings,*,
                                     boundary_stages:tuple[FreeSurfaceBoundaryStage,...] | None=None):
    """Advance fully nonlinear hydrostatic area/discharge equations on a uniform grid.

    Density is constant. The Chezy coefficient has units sqrt(m)/s and is
    supplied rather than fitted. Positive angle is downhill along increasing x.
    In the source terms normal gravity is g cos(angle) and tangential gravity
    is g sin(angle). Only mass and momentum have exact discrete flux/source
    ledgers. Mechanical energy also changes through numerical diffusion and
    shocks; its diagnostic residual is never called exact energy conservation.

    Ghost-state boundary data are approximate numerical boundary conditions,
    not full characteristic control relations. Explicit observation times clip
    steps; snapshots at a boundary event contain the continuous cell state
    before the new ghost condition evolves it. No dry or pressurized continuation
    is manufactured by clipping area after a failed step.
    """
    if not isinstance(settings,CircularFreeSurfaceSettings):raise TypeError("CircularFreeSurfaceSettings required")
    q=CircularFreeSurfaceSettings.model_validate(settings.model_dump(mode="python"))
    return _solve_circular_pipe_fv(initial_area_discharge,q,boundary_stages=boundary_stages)


def _solve_circular_pipe_fv(initial_area_discharge,q,*,boundary_stages=None,closure=None,bed_elevations_m=None):
    """Shared hydrostatic FV kernel; alternate closures retain the same budgets.

    Optional bed reconstruction acts on interface states, never clips conserved
    cell inventory. A nonpositive reconstructed wet head is unsupported.
    """
    u=np.array(initial_area_discharge,dtype=float,copy=True)
    if u.ndim!=2 or u.shape[1]!=2 or not 2<=len(u)<=1000000:
        raise ValueError("initial state requires cell rows (wet area,discharge)")
    section,_,_=_recover(u,q,closure)
    n=len(u);dx=q.length_m/n;density=q.density_kg_m3
    if not math.isfinite(dx) or dx<=0:raise ValueError("finite positive spatial step required")
    normal_g=q.gravity_m_s2*math.cos(q.signed_downhill_angle_rad)
    axial_g=q.gravity_m_s2*math.sin(q.signed_downhill_angle_rad)
    bed=None if bed_elevations_m is None else np.asarray(bed_elevations_m,dtype=float)
    if bed is not None and (bed.shape!=(n,) or not np.isfinite(bed).all()):
        raise ValueError('bed elevations must be finite and aligned with cells')
    chezy=None
    if q.chezy_coefficient_m_half_per_s is not None:
        chezy=np.asarray(q.chezy_coefficient_m_half_per_s,dtype=float)
        if chezy.ndim==0:chezy=np.full(n,float(chezy))
        if chezy.shape!=(n,) or not np.all(np.isfinite(chezy)) or np.any(chezy<=0):
            raise ValueError("one positive finite Chezy coefficient per cell or one scalar required")
    finish=q.start_time_s+q.duration_s
    if boundary_stages is None:
        b=FreeSurfaceBoundary(kind="extrapolated")
        stages=(FreeSurfaceBoundaryStage(start_time_s=q.start_time_s,left=b,right=b),)
    else:stages=tuple(FreeSurfaceBoundaryStage.model_validate(s.model_dump(mode="python")) for s in boundary_stages)
    if not stages or stages[0].start_time_s!=q.start_time_s or stages[-1].start_time_s>finish:
        raise ValueError("boundary schedule must start with the solver and end within its window")
    if any(b.start_time_s<=a.start_time_s for a,b in zip(stages,stages[1:])):
        raise ValueError("boundary times must increase strictly")
    for stage in stages:
        for b in (stage.left,stage.right):
            if b.kind=="prescribed":_recover(np.asarray([b.prescribed_area_discharge]),q,closure)

    def rhs(state,stage):
        ghosts=[]
        for side,b in ((0,stage.left),(-1,stage.right)):
            if b.kind=="periodic":ghost=state[-1 if side==0 else 0].copy()
            elif b.kind=="prescribed":ghost=np.asarray(b.prescribed_area_discharge,dtype=float)
            else:
                ghost=state[side].copy()
                if b.kind=="reflective":ghost[1]*=-1
            ghosts.append(ghost)
        padded=np.vstack((ghosts[0],state,ghosts[1]))
        geometry,v,c=_recover(padded,q,closure)
        bed_source=np.zeros(n)
        if bed is None:
            flux=np.column_stack((padded[:,1],padded[:,1]*v+normal_g*geometry.hydrostatic_integral_m3))
            alpha=np.maximum(np.abs(v[:-1])+c[:-1],np.abs(v[1:])+c[1:])
            interfaces=.5*(flux[:-1]+flux[1:])-.5*alpha[:,None]*(padded[1:]-padded[:-1])
            energy_density=.5*padded[:,1]*v+normal_g*geometry.first_height_moment_m3
            energy_flux=padded[:,1]*(.5*v*v+normal_g*geometry.depth_m)
            numerical_energy_flux=.5*(energy_flux[:-1]+energy_flux[1:])-.5*alpha*(energy_density[1:]-energy_density[:-1])
        else:
            padded_bed=np.r_[bed[-1] if stage.left.kind=='periodic' else bed[0],bed,
                              bed[0] if stage.right.kind=='periodic' else bed[-1]]
            face_bed=np.maximum(padded_bed[:-1],padded_bed[1:])
            left_head=geometry.depth_m[:-1]+padded_bed[:-1]-face_bed
            right_head=geometry.depth_m[1:]+padded_bed[1:]-face_bed
            if np.any(left_head<=0) or np.any(right_head<=0):
                raise FreeSurfaceDomainError('bed reconstruction requires a wet positive interface; dry fronts are unsupported')
            if closure is None:
                left_geometry=circular_section_from_depth(left_head,q.inner_diameter_m)
                right_geometry=circular_section_from_depth(right_head,q.inner_diameter_m)
                lc=np.sqrt(normal_g*left_geometry.area_m2/left_geometry.top_width_m)
                rc=np.sqrt(normal_g*right_geometry.area_m2/right_geometry.top_width_m)
            else:
                left_geometry=closure.at_head(left_head);right_geometry=closure.at_head(right_head)
                lc=left_geometry.wave_speed_m_s;rc=right_geometry.wave_speed_m_s
            la,ra=left_geometry.area_m2,right_geometry.area_m2;lv,rv=v[:-1],v[1:]
            left_state=np.column_stack((la,la*lv));right_state=np.column_stack((ra,ra*rv))
            lp=normal_g*left_geometry.hydrostatic_integral_m3;rp=normal_g*right_geometry.hydrostatic_integral_m3
            left_flux=np.column_stack((la*lv,la*lv*lv+lp));right_flux=np.column_stack((ra*rv,ra*rv*rv+rp))
            alpha=np.maximum(abs(lv)+lc,abs(rv)+rc)
            interfaces=.5*(left_flux+right_flux)-.5*alpha[:,None]*(right_state-left_state)
            bed_source=(lp[1:]-rp[:-1])/dx
            le=.5*la*lv*lv+normal_g*(left_geometry.first_height_moment_m3+face_bed*la)
            re=.5*ra*rv*rv+normal_g*(right_geometry.first_height_moment_m3+face_bed*ra)
            lf=la*lv*(.5*lv*lv+normal_g*(left_geometry.depth_m+face_bed))
            rf=ra*rv*(.5*rv*rv+normal_g*(right_geometry.depth_m+face_bed))
            numerical_energy_flux=.5*(lf+rf)-.5*alpha*(re-le)
        a=state[:,0];velocity=v[1:-1];radius=geometry.hydraulic_radius_m[1:-1]
        source=np.zeros_like(state);source[:,1]=axial_g*a+bed_source
        resistance=np.zeros(n)
        if chezy is not None:
            resistance=normal_g*a*velocity*np.abs(velocity)/(chezy**2*radius)
            source[:,1]-=resistance
        derivative=-(interfaces[1:]-interfaces[:-1])/dx+source
        if not np.all(np.isfinite(derivative)) or not np.all(np.isfinite(numerical_energy_flux)):
            raise FreeSurfaceDomainError("flux/source exceeds finite numerical support")
        return (derivative,density*(interfaces[0]-interfaces[-1]),density*dx*np.sum(source,axis=0),
            float(np.max(alpha)),density*(numerical_energy_flux[0]-numerical_energy_flux[-1]),
            density*dx*float(np.sum(axial_g*state[:,1])),density*dx*float(np.sum(resistance*velocity)))

    def energy(state):
        geometry,v,_=_recover(state,q,closure)
        return density*dx*float(np.sum(.5*state[:,1]*v+normal_g*(geometry.first_height_moment_m3+(0 if bed is None else bed*state[:,0]))))

    initial=density*dx*np.sum(u,axis=0);initial_energy=energy(u)
    boundary_budget=np.zeros(2);source_budget=np.zeros(2)
    energy_boundary=gravity_work=friction_loss=0.
    time=q.start_time_s;times=[time];events=[];observations=[];oi=0;si=0;rejected=0
    if q.observation_times_s and q.observation_times_s[0]==time:
        observations.append(_snapshot(time,u,q,closure));oi=1
    while time<finish:
        if len(times)-1>=q.maximum_steps:raise FreeSurfaceEvolutionError("maximum accepted wet-flow steps exceeded")
        while si+1<len(stages) and stages[si+1].start_time_s<=time:
            si+=1;events.append(stages[si].start_time_s)
        stage=stages[si]
        event=stages[si+1].start_time_s if si+1<len(stages) else finish
        observe=q.observation_times_s[oi] if oi<len(q.observation_times_s) else finish
        target=min(finish,event,observe)
        geometry,v,c=_recover(u,q,closure)
        first=rhs(u,stage)
        dt=min(q.maximum_step_s,q.cfl*dx/first[3],target-time)
        rate=abs(axial_g)/np.maximum(c,np.abs(v))
        if chezy is not None:rate+=normal_g*np.abs(v)/(chezy**2*geometry.hydraulic_radius_m)
        if np.max(rate)>0:dt=min(dt,q.source_fraction_per_step/float(np.max(rate)))
        if dt<=0 or time+dt<=time:raise FreeSurfaceEvolutionError("time cannot advance within wet-flow domain")
        for retry in range(q.maximum_retries+1):
            try:
                trial=u+dt*first[0]
                _recover(trial,q,closure)
                second=rhs(trial,stage)
                if dt*second[3]/dx>q.cfl*(1+1e-12):raise FreeSurfaceDomainError("stage CFL increased")
                candidate=.5*u+.5*(trial+dt*second[0])
                _recover(candidate,q,closure)
                break
            except (FreeSurfaceDomainError,OverflowError,FloatingPointError) as exc:
                if retry==q.maximum_retries or dt/2<q.minimum_retry_step_s:
                    raise FreeSurfaceEvolutionError("no admissible wet partial-fill step; dry/full continuation is unsupported") from exc
                rejected+=1;dt*=.5
        boundary_budget+=.5*dt*(first[1]+second[1]);source_budget+=.5*dt*(first[2]+second[2])
        energy_boundary+=.5*dt*(first[4]+second[4]);gravity_work+=.5*dt*(first[5]+second[5])
        friction_loss+=.5*dt*(first[6]+second[6])
        u=candidate;new_time=time+dt
        if dt==target-time:new_time=target
        time=new_time;times.append(time)
        if oi<len(q.observation_times_s) and time==q.observation_times_s[oi]:
            observations.append(_snapshot(time,u,q,closure));oi+=1
    while si+1<len(stages) and stages[si+1].start_time_s==finish:
        si+=1;events.append(finish)
    final=density*dx*np.sum(u,axis=0);final_energy=energy(u)
    geometry,v,c=_recover(u,q,closure)
    ledger=FreeSurfaceLedger(initial,final,boundary_budget,source_budget,final-initial-boundary_budget-source_budget)
    mechanical=FreeSurfaceMechanicalEnergy(initial_energy,final_energy,energy_boundary,gravity_work,friction_loss,
        final_energy-initial_energy-energy_boundary-gravity_work+friction_loss)
    return CircularFreeSurfaceResult((np.arange(n)+.5)*dx,u,geometry.depth_m,v,c,tuple(times),tuple(events),
        tuple(observations),ledger,mechanical,len(times)-1,rejected,
        scheme=('circular_barotropic_Rusanov_SSPRK2_strict_wet_free_surface' if closure is None else
                'shared_hydrostatic_barotropic_Rusanov_SSPRK2_wet_and_pressurized'))
