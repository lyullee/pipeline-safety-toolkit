"""Uniform acoustic cavity modes using existing native axial FE stiffness/mass."""
import math
import sys
from typing import Literal
import numpy as np
from pydantic import Field, model_validator
from scipy.linalg import eigh
from .contracts import Contract,Name,Positive,RunContext,digest
from .structural import FrameRequest,FrameNode,FrameMember,FrameMaterial,FrameSection,FrameSupport,FrameCombination,build_native_frame

class AcousticCavityModeRequest(Contract):
    asset_id: Name
    clock_id: Name
    length_m: Positive
    sound_speed_m_s: Positive | None = None
    left_boundary: Literal['rigid_closed','pressure_release_open']
    right_boundary: Literal['rigid_closed','pressure_release_open']
    elements: int = Field(default=32,ge=4,le=256)
    requested_modes: int = Field(default=3,ge=1,le=20)
    property_geometry_boundary_evidence_id: Name
    uniform_quiescent_linear_acoustics_and_ideal_end_conditions_reviewed: bool = False

    @model_validator(mode='after')
    def available_positive_modes(self):
        fixed = int(self.left_boundary == 'pressure_release_open') + int(self.right_boundary == 'pressure_release_open')
        zero = int(self.left_boundary == self.right_boundary == 'rigid_closed')
        available = self.elements + 1 - fixed - zero
        if self.requested_modes > available:
            raise ValueError('requested positive acoustic modes exceed the mesh and boundary degrees of freedom')
        return self

def evaluate_acoustic_cavity_modes(request: AcousticCavityModeRequest,context: RunContext):
    """Reuse axial Ke/M for the equivalent pressure Sturm-Liouville problem.

    Virtual rod displacement represents pressure mode shape, never a structural
    displacement. Unit density, area and modulus define a geometric eigenproblem;
    its frequencies are scaled by sound speed after solving p_tt=a² p_xx.
    This keeps very small/large sound speeds out of the matrix products.
    Open pressure-release ends impose
    p=0, rigid ends impose its natural zero gradient. The all-closed constant
    pressure mode has zero frequency and is reported separately, not resonance.
    No radiation/end correction, mean flow, wall compliance or network is inferred.
    """
    q=AcousticCavityModeRequest.model_validate(request.model_dump());c=RunContext.model_validate(context.model_dump())
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):raise ValueError('cavity/context identity differs')
    eid=q.property_geometry_boundary_evidence_id
    if eid not in {e.id for e in c.evidence}:raise ValueError('acoustic property/boundary evidence absent')
    base=dict(request_digest=digest(q),context_digest=digest(c),structural_displacement_or_mass_predicted=False)
    if q.sound_speed_m_s is None or not q.uniform_quiescent_linear_acoustics_and_ideal_end_conditions_reviewed:
        return dict(status='needs_data',**base)
    nodes=tuple(FrameNode(id=f'n{i}',position_m=(q.length_m*i/q.elements,0.,0.)) for i in range(q.elements+1))
    supports=[];fixed_indices=[]
    for i,node in enumerate(nodes):
        dofs=('DY','DZ','RX','RY','RZ')
        if (i==0 and q.left_boundary=='pressure_release_open') or (i==q.elements and q.right_boundary=='pressure_release_open'):
            dofs+=('DX',);fixed_indices.append(i)
        supports.append(FrameSupport(node_id=node.id,fixed_dofs=dofs,evidence_id=eid))
    frame=FrameRequest(asset_id=q.asset_id,clock_id=q.clock_id,time_s=0.,nodes=nodes,
        members=tuple(FrameMember(id=f'e{i}',i_node=nodes[i].id,j_node=nodes[i+1].id,section_id='virtual',material_id='virtual') for i in range(q.elements)),
        sections=(FrameSection(id='virtual',area_m2=1.,iy_m4=1.,iz_m4=1.,torsion_constant_m4=1.,evidence_id=eid),),
        materials=(FrameMaterial(id='virtual',young_modulus_pa=1.,poisson_ratio=0.,evidence_id=eid),),
        supports=tuple(supports),load_cases=('mass',),combinations=(FrameCombination(id='mass',factors={'mass':1.},evidence_id=eid),),
        geometry_evidence_id=eid,geometry_and_connectivity_reviewed=True,stiffness_and_loads_reviewed=True,supports_reviewed=True,
        linear_small_displacement_applicability_reviewed=True)
    assembly=build_native_frame(frame,c)
    if assembly.record['status']!='ready':return dict(status='solver_withheld',issues=assembly.record,**base)
    native=assembly.native_model;native.materials['virtual'].rho=1.
    native.add_member_self_weight('FY',-1.,case='mass')
    from Pynite import Analysis
    Analysis._prepare_model(native)
    stiffness=native.Ke('mass',log=False,check_stability=True,sparse=True).toarray()
    mass=native.M('mass',mass_direction='Y',gravity=1.,log=False,sparse=True).toarray()
    free=[i for i in range(q.elements+1) if i not in fixed_indices];indices=[6*i for i in free]
    k=stiffness[np.ix_(indices,indices)];m=mass[np.ix_(indices,indices)]
    ks=float(np.max(np.abs(k)));ms=float(np.max(np.abs(m)))
    if not (math.isfinite(ks) and math.isfinite(ms) and ks>0 and ms>0):
        raise ValueError('acoustic matrix scales exceed finite positive numerical support')
    k=k/ks;m=m/ms
    eigenvalues,vectors=eigh(k,m)
    threshold=max(abs(eigenvalues))*1e-12;zero_count=int(np.sum(np.abs(eigenvalues)<=threshold))
    expected_zero=int(q.left_boundary==q.right_boundary=='rigid_closed')
    if zero_count!=expected_zero or np.any(eigenvalues < -threshold):raise ValueError('unexpected acoustic eigen nullspace or negative stiffness')
    positive=[i for i,v in enumerate(eigenvalues) if v>threshold]
    if len(positive)<q.requested_modes:raise ValueError('mesh has too few positive modes')
    modes=[]
    for index in positive[:q.requested_modes]:
        vector=vectors[:,index];lam=eigenvalues[index]
        denominator=np.linalg.norm(k@vector)+lam*np.linalg.norm(m@vector)
        residual=np.linalg.norm(k@vector-lam*m@vector)/denominator
        if not math.isfinite(float(residual)) or residual>1e-8:
            raise ValueError('acoustic FE eigen residual exceeded finite support')
        shape=np.zeros(q.elements+1);shape[free]=vector;shape/=max(abs(shape))
        first=next(v for v in shape if abs(v)>1e-8)
        if first<0:shape=-shape
        log_frequency=(math.log(q.sound_speed_m_s)+.5*(math.log(float(lam))+math.log(ks)-math.log(ms))
                       -math.log(2*math.pi))
        if not math.log(math.ulp(0.))<=log_frequency<=math.log(sys.float_info.max):
            raise ValueError('nonzero acoustic frequency exceeds finite floating-point support')
        frequency=math.exp(log_frequency)
        if not math.isfinite(frequency) or frequency<=0:
            raise ValueError('nonzero acoustic frequency is not numerically representable')
        modes.append(dict(frequency_hz=frequency,normalized_nodal_pressure_shape=tuple(float(v) for v in shape),relative_eigen_residual=float(residual)))
    return dict(status='computed',**base,node_position_m=tuple(n.position_m[0] for n in nodes),modes=tuple(modes),
        excluded_constant_pressure_zero_modes=zero_count,idealized_open_end_radiation_or_wall_compliance_inferred=False)
