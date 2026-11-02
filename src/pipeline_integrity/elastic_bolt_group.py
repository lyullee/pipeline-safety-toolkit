"""Rigid joint faces, explicit elastic bolt/member sharing and retained contact."""
import numpy as np
from pydantic import Field
from .contracts import Contract,Name,Positive,Nonnegative,RunContext,digest
from .adapters import MaterialClient
from .material_exchange import prepare_material_job,evaluate_material_job

class ElasticBolt(Contract):
    id: Name
    position_yz_m: tuple[float,float]
    tensile_area_m2: Positive
    shear_area_m2: Positive
    normal_bolt_stiffness_n_m: Positive
    normal_clamped_member_stiffness_n_m: Positive
    tangential_bolt_stiffness_n_m: Positive
    initial_bolt_tension_and_clamp_compression_n: Nonnegative

class ElasticBoltGroupRequest(Contract):
    asset_id: Name
    location_id: Name
    clock_id: Name
    bolts: tuple[ElasticBolt,...] = Field(min_length=3,max_length=64)
    external_force_xyz_n: tuple[float,float,float] | None = None
    external_moment_xyz_nm: tuple[float,float,float] | None = None
    geometry_stiffness_preload_evidence_id: Name
    rigid_faces_bilateral_shear_and_preloaded_contact_reviewed: bool = False

def evaluate_elastic_bolt_group(request: ElasticBoltGroupRequest,context: RunContext,*,client=None):
    """Distribute six supplied external resultants with explicit stiffnesses.

    The x axis is normal to the joint; y/z positions and all moments refer to
    the same caller origin. Bolt plus clamped-member normal stiffness resists
    opening; bolt tension rises while clamped compression falls. Tangential
    springs carry shear/torsion without credited friction. Rigid-face and
    positive-contact assumptions are checked, not continued through separation.
    This is a modern elastic load-sharing model, not local flange flexibility,
    prying, a historical factor identity, gasket leakage or code acceptance.
    """
    q=ElasticBoltGroupRequest.model_validate(request.model_dump());c=RunContext.model_validate(context.model_dump())
    if (q.asset_id,q.clock_id)!=(c.asset_id,c.clock_id):raise ValueError('joint/context identity differs')
    eid=q.geometry_stiffness_preload_evidence_id
    if eid not in {e.id for e in c.evidence}:raise ValueError('joint evidence absent')
    if len({b.id for b in q.bolts})!=len(q.bolts):raise ValueError('bolt IDs must be unique')
    if len({b.position_yz_m for b in q.bolts})!=len(q.bolts):raise ValueError('coincident bolt positions require an explicit merged model')
    base=dict(request_digest=digest(q),context_digest=digest(c),friction_prying_local_flange_stress_or_acceptance_inferred=False)
    if q.external_force_xyz_n is None or q.external_moment_xyz_nm is None or not q.rigid_faces_bilateral_shear_and_preloaded_contact_reviewed:
        return dict(status='needs_data',**base)
    normal=[];tangent=[];kn=np.zeros((3,3));kt=np.zeros((3,3))
    for b in q.bolts:
        y,z=b.position_yz_m;n=np.array([1.,z,-y]);t=np.array([[1.,0.,-z],[0.,1.,y]])
        normal.append(n);tangent.append(t)
        kn+=(b.normal_bolt_stiffness_n_m+b.normal_clamped_member_stiffness_n_m)*np.outer(n,n)
        kt+=b.tangential_bolt_stiffness_n_m*t.T@t
    if np.linalg.matrix_rank(kn)<3 or np.linalg.matrix_rank(kt)<3:raise ValueError('joint support geometry/stiffness is singular')
    fx,fy,fz=q.external_force_xyz_n;mx,my,mz=q.external_moment_xyz_nm
    dn=np.linalg.solve(kn,np.array([fx,my,mz]));dt=np.linalg.solve(kt,np.array([fy,fz,mx]))
    rows=[];force_sum=np.zeros(3);moment_sum=np.zeros(3);energy=0.
    client=MaterialClient() if client is None else client
    for b,n,t in zip(q.bolts,normal,tangent):
        opening=float(n@dn);slip=t@dt
        bolt=b.initial_bolt_tension_and_clamp_compression_n+b.normal_bolt_stiffness_n_m*opening
        clamp=b.initial_bolt_tension_and_clamp_compression_n-b.normal_clamped_member_stiffness_n_m*opening
        shear=b.tangential_bolt_stiffness_n_m*slip
        if bolt<0 or clamp<0:
            return dict(status='out_of_scope',issues=('predicted bolt slackness or clamped-contact separation',),offending_bolt_id=b.id,**base)
        external_normal=bolt-clamp;vector=np.array([external_normal,*shear]);position=np.array([0.,*b.position_yz_m])
        force_sum+=vector;moment_sum+=np.cross(position,vector)
        energy+=.5*(b.normal_bolt_stiffness_n_m+b.normal_clamped_member_stiffness_n_m)*opening**2+.5*b.tangential_bolt_stiffness_n_m*float(slip@slip)
        normal_stress=bolt/b.tensile_area_m2;shear_stress=shear/b.shear_area_m2
        job=prepare_material_job('mechanics.plane_stress',dict(stress_x_Pa=normal_stress,stress_y_Pa=0.,shear_xy_Pa=float(np.linalg.norm(shear_stress))),
            c,location_id=q.location_id,evidence_ids=(eid,),client=client)
        receipt=evaluate_material_job(job,c,client=client)
        if receipt.native.status!='computed':return dict(status='solver_withheld',**base)
        rows.append(dict(bolt_id=b.id,normal_opening_m=opening,relative_tangential_displacement_yz_m=tuple(float(v) for v in slip),
            bolt_tension_n=bolt,clamped_member_compression_n=clamp,external_normal_load_share_n=external_normal,
            bolt_shear_yz_n=tuple(float(v) for v in shear),normal_stress_pa=normal_stress,
            shear_stress_xy_xz_pa=tuple(float(v) for v in shear_stress),von_mises_stress_pa=receipt.native.payload['values']['von_mises_Pa'],
            native_invariant_receipt=receipt.model_dump(mode='json')))
    return dict(status='computed',**base,bolts=tuple(rows),normal_joint_generalized_displacements=tuple(float(v) for v in dn),
        tangential_joint_generalized_displacements=tuple(float(v) for v in dt),incremental_elastic_energy_j=float(energy),
        resultant_force_residual_n=tuple(float(v) for v in force_sum-q.external_force_xyz_n),
        resultant_moment_residual_nm=tuple(float(v) for v in moment_sum-q.external_moment_xyz_nm))
