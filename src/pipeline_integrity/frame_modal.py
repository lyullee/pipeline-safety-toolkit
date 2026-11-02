"""Mass-normalized elastic frame modes from the existing native frame owner."""
from __future__ import annotations
import math
from typing import Literal
import numpy as np
from pydantic import Field,model_validator
from .contracts import Contract,Name,Positive,Nonnegative,RunContext,digest
from .structural import FrameRequest,DOFS,build_native_frame


class FrameModalRequest(Contract):
    frame: FrameRequest
    density_by_material_kg_m3: dict[Name,Positive]
    nodal_concentrated_mass_kg: dict[Name,Nonnegative] = Field(default_factory=dict)
    gravity_for_native_weight_to_mass_m_s2: Positive
    requested_modes: int = Field(default=3,ge=1,le=30)
    maximum_full_dofs: int = Field(default=600,ge=12,le=6000)
    relative_eigen_residual_tolerance: Positive = 1e-8
    unloaded_linear_frame_and_consistent_mass_reviewed: bool = False

    @model_validator(mode='after')
    def frame_domain(self):
        f=self.frame
        if f.node_loads or f.line_loads or f.initial_strains or f.initial_curvatures:
            raise ValueError('modal assembly requires an unloaded reference frame without initial strain/curvature')
        if any(m.rotation_releases for m in f.members):raise ValueError('released-member mass condensation is outside this adapter')
        if any(s.one_way_translation_directions or any(v!=0 for v in (*s.imposed_translations_m.values(),*s.imposed_rotations_rad.values())) for s in f.supports):
            raise ValueError('modal supports must be homogeneous and bilateral')
        if set(self.density_by_material_kg_m3)!={m.id for m in f.materials}:
            raise ValueError('one explicit density is required for each frame material')
        if not set(self.nodal_concentrated_mass_kg)<={n.id for n in f.nodes}:
            raise ValueError('concentrated mass node is absent from the frame')
        if len(f.nodes)*6>self.maximum_full_dofs:raise ValueError('modal frame exceeds full-DOF budget')
        return self


class FrameMode(Contract):
    frequency_hz: Positive
    nodal_displacements_and_rotations: dict[Name,tuple[float,float,float,float,float,float]]
    relative_eigen_residual: float


class FrameModalResult(Contract):
    status: Literal['computed','needs_data','solver_withheld']
    request_digest: str
    context_digest: str
    modes: tuple[FrameMode,...] = ()
    maximum_mass_orthogonality_error: float | None = None
    issues: tuple[str,...] = ()
    nonlinear_elbow_shell_contact_or_fluid_added_mass_inferred: Literal[False] = False


def evaluate_frame_modes(request: FrameModalRequest,context: RunContext) -> FrameModalResult:
    """Supply density to the existing native consistent-mass/eigen solver.

    Native self-weight loads are mass-assembly markers only. Their density is
    converted with the same explicit gravity supplied to the native mass builder;
    gravity therefore cancels and does not alter the physical modal mass. No
    static gravity response or automatically inferred fluid/coating mass is added.
    Homogeneous supported DOFs are removed when auditing eigen residuals.
    """
    if not isinstance(request,FrameModalRequest):raise TypeError('FrameModalRequest required')
    q=FrameModalRequest.model_validate(request.model_dump(mode='python'))
    c=RunContext.model_validate(context.model_dump(mode='python'))
    base=dict(request_digest=digest(q),context_digest=digest(c))
    if not q.unloaded_linear_frame_and_consistent_mass_reviewed:
        return FrameModalResult(status='needs_data',issues=('linear unloaded frame and explicit consistent mass require review',),**base)
    assembly=build_native_frame(q.frame,c)
    if assembly.record['status']!='ready':return FrameModalResult(status='needs_data',issues=('existing frame owner could not assemble reviewed inputs',),**base)
    native=assembly.native_model;gravity=q.gravity_for_native_weight_to_mass_m_s2
    for mid,density in q.density_by_material_kg_m3.items():native.materials[mid].rho=density*gravity
    mass_case='explicit_modal_mass';mass_combo='explicit_modal_mass_combination'
    native.add_member_self_weight('FY',-1.,case=mass_case)
    for nid,mass_kg in q.nodal_concentrated_mass_kg.items():
        native.add_node_load(nid,'FY',-mass_kg*gravity,case=mass_case)
    native.add_load_combo(mass_combo,{mass_case:1.})
    fixed={(s.node_id,dof) for s in q.frame.supports for dof in s.fixed_dofs}
    free_count=len(q.frame.nodes)*6-len(fixed)
    if q.requested_modes>=free_count:
        return FrameModalResult(status='solver_withheld',issues=('requested mode count must be smaller than free-DOF count for native sparse solve',),**base)
    native.analyze_modal(num_modes=q.requested_modes,mass_combo_name=mass_combo,mass_direction='Y',gravity=gravity,log=False)
    nodes=sorted(native.nodes.values(),key=lambda n:n.ID)
    elastic=np.asarray(native.Ke(mass_combo,log=False,check_stability=True,sparse=True).toarray())
    mass=np.asarray(native.M(mass_combo,mass_direction='Y',gravity=gravity,log=False,sparse=True).toarray())
    indices=[6*i+j for i,node in enumerate(nodes) for j,dof in enumerate(DOFS) if (node.name,dof) not in fixed]
    modes=[];vectors=[]
    for i,frequency in enumerate(native.frequencies):
        frequency=float(frequency)
        if not math.isfinite(frequency) or frequency<=0:raise ValueError('native mode frequency is not positive finite')
        combo=f'Mode {i+1}'
        values={node.name:tuple(float(getattr(node,dof)[combo]) for dof in DOFS) for node in nodes}
        vector=np.array([x for node in nodes for x in values[node.name]])
        kforce=(elastic@vector)[indices];mforce=(mass@vector)[indices];eigenvalue=(2*math.pi*frequency)**2
        scale=np.linalg.norm(kforce)+eigenvalue*np.linalg.norm(mforce)
        residual=float(np.linalg.norm(kforce-eigenvalue*mforce)/scale) if scale else float('inf')
        if residual>q.relative_eigen_residual_tolerance:
            return FrameModalResult(status='solver_withheld',issues=('native mode failed independent assembled eigen residual',),**base)
        vectors.append(vector);modes.append(FrameMode(frequency_hz=frequency,nodal_displacements_and_rotations=values,relative_eigen_residual=residual))
    gram=np.asarray(vectors)@mass@np.asarray(vectors).T
    normalized=gram/np.sqrt(np.outer(np.diag(gram),np.diag(gram)))
    error=float(np.max(np.abs(normalized-np.eye(len(modes)))))
    return FrameModalResult(status='computed',**base,modes=tuple(modes),maximum_mass_orthogonality_error=error)
