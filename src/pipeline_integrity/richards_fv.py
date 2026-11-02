"""Mixed-form single-liquid Darcy/Richards flow on orthogonal finite volumes.

Cells and face metrics are caller inputs in SI. A scalar isotropic hydraulic
conductivity is harmonically averaged across orthogonal two-point faces. This
operator works in 1D, 2D or 3D; it does not construct a mesh, infer a retention
law, estimate soil coefficients or model displacement of a second liquid.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Literal, Protocol
import warnings
import numpy as np
from pydantic import model_validator, Field
from scipy.sparse import coo_matrix
from scipy.sparse.linalg import MatrixRankWarning, spsolve
from .contracts import Contract, Positive, Nonnegative


@dataclass(frozen=True)
class HydraulicState:
    liquid_content: float
    capacity_per_m: float
    conductivity_m_s: float


class HydraulicConstitutiveLaw(Protocol):
    """Caller-specified theta(h), its derivative and isotropic K(h).

    Cell index permits heterogeneous laws. The same cell material evaluates
    its adjacent imposed pressure-head boundary. All values must be finite;
    theta is a volumetric liquid content in [0,1], dtheta/dh and K nonnegative.
    Consistency of the supplied derivative remains a caller material review.
    """
    def evaluate(self, head_m: float, cell_index: int) -> HydraulicState: ...


@dataclass(frozen=True)
class LinearHydraulicLaw:
    """Explicit reference material, valid only where its linear content is [0,1]."""
    reference_content: float
    capacity_per_m: float
    conductivity_m_s: float
    reference_head_m: float = 0.0

    def evaluate(self, head_m: float, cell_index: int) -> HydraulicState:
        return HydraulicState(self.reference_content+self.capacity_per_m*(head_m-self.reference_head_m),
            self.capacity_per_m,self.conductivity_m_s)


class InteriorDarcyFace(Contract):
    cell_i: int = Field(ge=0)
    cell_j: int = Field(ge=0)
    area_m2: Positive
    center_distance_m: Positive

    @model_validator(mode='after')
    def different(self):
        if self.cell_i==self.cell_j: raise ValueError('interior face must connect distinct cells')
        return self


class DarcyBoundaryFace(Contract):
    cell: int = Field(ge=0)
    area_m2: Positive
    center_to_face_m: Positive
    elevation_m: float = 0.0
    kind: Literal['pressure_head','inward_flux']
    value: float


class RichardsMesh(Contract):
    cell_volumes_m3: tuple[Positive,...]
    cell_elevations_m: tuple[float,...]
    interior_faces: tuple[InteriorDarcyFace,...] = ()
    boundary_faces: tuple[DarcyBoundaryFace,...] = ()
    orthogonal_two_point_metrics_reviewed: bool = False

    @model_validator(mode='after')
    def aligned(self):
        n=len(self.cell_volumes_m3)
        if not n or len(self.cell_elevations_m)!=n: raise ValueError('nonempty aligned cell volume/elevation arrays required')
        if any(max(f.cell_i,f.cell_j)>=n for f in self.interior_faces) or any(f.cell>=n for f in self.boundary_faces):
            raise ValueError('face cell index outside mesh')
        pairs=[tuple(sorted((f.cell_i,f.cell_j))) for f in self.interior_faces]
        if len(set(pairs))!=len(pairs): raise ValueError('duplicate cell pair: combine parallel-face metrics explicitly')
        return self


class RichardsSettings(Contract):
    duration_s: Positive
    initial_step_s: Positive
    maximum_step_s: Positive
    minimum_step_s: Positive = 1e-10
    nonlinear_volume_tolerance_m3: Positive = 1e-11
    nonlinear_relative_tolerance: Positive = 1e-9
    max_nonlinear_iterations: int = Field(default=50,ge=1)
    max_accepted_steps: int = Field(default=100000,ge=1)
    observation_times_s: tuple[Nonnegative,...] = ()

    @model_validator(mode='after')
    def clocks(self):
        if self.minimum_step_s>min(self.initial_step_s,self.maximum_step_s): raise ValueError('minimum step exceeds selected initial/maximum step')
        ts=self.observation_times_s
        if any(a>=b for a,b in zip(ts,ts[1:])) or any(t>self.duration_s for t in ts):
            raise ValueError('observation clocks must strictly increase within run interval')
        return self


@dataclass(frozen=True)
class RichardsSnapshot:
    time_s: float
    head_m: tuple[float,...]
    liquid_content: tuple[float,...]
    conductivity_m_s: tuple[float,...]


@dataclass(frozen=True)
class RichardsResult:
    final_head_m: tuple[float,...]
    final_liquid_content: tuple[float,...]
    initial_liquid_volume_m3: float
    final_liquid_volume_m3: float
    accumulated_boundary_inflow_m3: tuple[float,...]
    accumulated_cell_source_m3: tuple[float,...]
    global_volume_residual_m3: float
    accepted_step_times_s: tuple[float,...]
    rejected_steps: int
    maximum_nonlinear_cell_residual_m3: float
    snapshots: tuple[RichardsSnapshot,...]
    coordinate_convention: str = 'elevation positive upward; hydraulic head = pressure head + elevation'
    time_scheme: str = 'backward Euler mixed liquid-content residual; modified Picard with residual line search'


def _material(law,head):
    states=[law.evaluate(float(h),i) for i,h in enumerate(head)]
    a=np.array([[s.liquid_content,s.capacity_per_m,s.conductivity_m_s] for s in states],dtype=float)
    if a.shape!=(len(head),3) or not np.isfinite(a).all(): raise ValueError('constitutive law must supply finite theta, capacity and conductivity')
    if ((a[:,0]<0)|(a[:,0]>1)|(a[:,1]<0)|(a[:,2]<0)).any():
        raise ValueError('constitutive state outside theta[0,1], capacity>=0, conductivity>=0 domain')
    return a[:,0],a[:,1],a[:,2]


def _harmonic(a,b):
    return 0.0 if min(a,b)==0 else 2*min(a,b)/(1+min(a,b)/max(a,b))


def solve_richards_fv(mesh: RichardsMesh, settings: RichardsSettings,
    initial_head_m: tuple[float,...], law: HydraulicConstitutiveLaw,
    cell_source_m3_s: tuple[float,...] | None = None) -> RichardsResult:
    """Integrate supplied mixed-form constitutive physics with a volume ledger.

    Positive prescribed face flux enters the modeled domain. Fixed head is the
    pressure head at the face elevation. Internal faces cancel exactly in the
    assembled volume balance. A rejected nonlinear solve halves its time step;
    no content/head clipping or arbitrary saturated-Neumann gauge is applied.
    Sources and boundary data are constant during this run. Time-varying events
    can be explicit consecutive runs; no schedule interpolation is inferred.
    """
    m=RichardsMesh.model_validate(mesh.model_dump());q=RichardsSettings.model_validate(settings.model_dump())
    if not m.orthogonal_two_point_metrics_reviewed: raise ValueError('orthogonal scalar-isotropic face metrics require caller review')
    v=np.array(m.cell_volumes_m3);z=np.array(m.cell_elevations_m);h=np.array(initial_head_m,dtype=float)
    n=len(v)
    if h.shape!=(n,) or not np.isfinite(h).all(): raise ValueError('finite initial head for every cell required')
    source=np.zeros(n) if cell_source_m3_s is None else np.array(cell_source_m3_s,dtype=float)
    if source.shape!=(n,) or not np.isfinite(source).all(): raise ValueError('finite source for every cell required')
    theta,capacity,k=_material(law,h)
    parent=list(range(n))
    def representative(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]];i=parent[i]
        return i
    for f in m.interior_faces:
        if min(k[f.cell_i],k[f.cell_j])>0:
            parent[representative(f.cell_i)]=representative(f.cell_j)
    anchors={representative(i) for i in range(n) if capacity[i]>0}
    for b in m.boundary_faces:
        if b.kind=='pressure_head' and min(k[b.cell],law.evaluate(b.value,b.cell).conductivity_m_s)>0:
            anchors.add(representative(b.cell))
    if any(representative(i) not in anchors for i in range(n)):
        raise ValueError('zero-storage all-Neumann pressure component is undetermined; no automatic pressure pin')
    initial_volume=float(v@theta);bc_budget=np.zeros(len(m.boundary_faces));source_budget=np.zeros(n)
    snapshots=[];accepted=[0.0];rejected=0;max_residual=0.0;t=0.0
    targets=list(q.observation_times_s)

    def observe(time):
        th,_,kk=_material(law,h)
        snapshots.append(RichardsSnapshot(time,tuple(h),tuple(th),tuple(kk)))

    if targets and targets[0]==0: observe(0.0);targets.pop(0)

    def assembly(head,old_content,dt):
        th,cap,kk=_material(law,head);H=head+z
        residual=v*(th-old_content)-dt*source
        diagonal=v*cap/dt;row=[];col=[];data=[]
        for f in m.interior_faces:
            i,j=f.cell_i,f.cell_j
            g=_harmonic(kk[i],kk[j])*f.area_m2/f.center_distance_m
            outward=g*(H[i]-H[j]);residual[i]+=dt*outward;residual[j]-=dt*outward
            diagonal[i]+=g;diagonal[j]+=g
            row.extend((i,j));col.extend((j,i));data.extend((-g,-g))
        inflow=np.zeros(len(m.boundary_faces))
        for index,b in enumerate(m.boundary_faces):
            i=b.cell
            if b.kind=='inward_flux': inflow[index]=b.value*b.area_m2
            else:
                s=law.evaluate(b.value,i)
                if not all(math.isfinite(x) for x in (s.liquid_content,s.capacity_per_m,s.conductivity_m_s)) or not 0<=s.liquid_content<=1 or min(s.capacity_per_m,s.conductivity_m_s)<0:
                    raise ValueError('boundary constitutive state outside declared domain')
                g=_harmonic(kk[i],s.conductivity_m_s)*b.area_m2/b.center_to_face_m
                inflow[index]=g*(b.value+b.elevation_m-H[i]);diagonal[i]+=g
            residual[i]-=dt*inflow[index]
        row.extend(range(n));col.extend(range(n));data.extend(diagonal)
        matrix=coo_matrix((data,(row,col)),shape=(n,n)).tocsr()
        return residual,matrix,inflow,th

    dt=min(q.initial_step_s,q.maximum_step_s)
    while t<q.duration_s:
        if len(accepted)>q.max_accepted_steps: raise RuntimeError('accepted-step limit reached; partial result not credited')
        end=min(t+dt,q.duration_s,targets[0] if targets else q.duration_s)
        step=end-t
        if step<=0: raise RuntimeError('time-step clock failed to advance')
        old=theta.copy();candidate=h.copy();converged=False
        # Tolerance uses liquid-volume units; the actual theta difference is never
        # replaced by capacity times the total head increment.
        tol=q.nonlinear_volume_tolerance_m3+q.nonlinear_relative_tolerance*max(float(np.max(np.abs(v*old))),step*float(np.max(np.abs(source))))
        try:
            for _ in range(q.max_nonlinear_iterations):
                residual,matrix,inflow,newtheta=assembly(candidate,old,step)
                norm=float(np.max(np.abs(residual)))
                if norm<=tol:
                    converged=True;break
                with warnings.catch_warnings():
                    warnings.simplefilter('error',MatrixRankWarning)
                    delta=spsolve(matrix,-residual/step)
                if not np.isfinite(delta).all(): raise ValueError('singular or nonfinite pressure increment')
                scale=1.0;improved=False
                for _ in range(22):
                    trial=candidate+scale*delta
                    try:
                        trial_residual,_,_,_=assembly(trial,old,step)
                        if np.max(np.abs(trial_residual))<norm:
                            candidate=trial;improved=True;break
                    except ValueError: pass
                    scale*=.5
                if not improved: break
        except (ValueError,MatrixRankWarning,RuntimeError,FloatingPointError):
            converged=False
        if not converged:
            rejected+=1;dt=step*.5
            if dt<q.minimum_step_s: raise RuntimeError('nonlinear/constitutive/domain or pressure-gauge failure at minimum step; no clipping or gauge pin applied')
            continue
        h=candidate;theta=newtheta;t=end;accepted.append(t)
        bc_budget+=step*inflow;source_budget+=step*source
        max_residual=max(max_residual,float(np.max(np.abs(residual))))
        if targets and t==targets[0]: observe(t);targets.pop(0)
        dt=min(q.maximum_step_s,step*1.5)
    final_volume=float(v@theta)
    return RichardsResult(tuple(h),tuple(theta),initial_volume,final_volume,tuple(bc_budget),tuple(source_budget),
        final_volume-initial_volume-float(bc_budget.sum())-float(source_budget.sum()),tuple(accepted),rejected,max_residual,tuple(snapshots))
