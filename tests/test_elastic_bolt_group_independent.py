"""Independent circular angular sums, joint energy and total resultant balance."""
import math
import numpy as np
import pytest
from pipeline_integrity.contracts import EvidenceRef,RunContext
from pipeline_integrity.elastic_bolt_group import ElasticBolt as Bolt,ElasticBoltGroupRequest as Request,evaluate_elastic_bolt_group as evaluate
C=RunContext(asset_id='joint',scenario_id='external-load',clock_id='static',origin='synthetic',evidence=(EvidenceRef(id='authored',citation='Authored joint stiffness and geometry',origin='synthetic',license_id='MIT'),))
def request(count=8,force=(2000.,3000.,-1500.),moment=(400.,-500.,600.)):
    radius=.25
    bolts=tuple(Bolt(id=f'b{i}',position_yz_m=(radius*math.cos(2*math.pi*i/count),radius*math.sin(2*math.pi*i/count)),
        tensile_area_m2=3e-4,shear_area_m2=3.5e-4,normal_bolt_stiffness_n_m=2e8,normal_clamped_member_stiffness_n_m=8e8,
        tangential_bolt_stiffness_n_m=1e8,initial_bolt_tension_and_clamp_compression_n=40000.) for i in range(count))
    return Request(asset_id=C.asset_id,clock_id=C.clock_id,location_id='flange',bolts=bolts,external_force_xyz_n=force,external_moment_xyz_nm=moment,
        geometry_stiffness_preload_evidence_id='authored',rigid_faces_bilateral_shear_and_preloaded_contact_reviewed=True)

@pytest.mark.parametrize('count',[4,8,12])
def test_circular_bolt_group_matches_independent_angular_resultant_solution(count):
    q=request(count);r=evaluate(q,C);assert r['status']=='computed'
    radius=.25;fx,fy,fz=q.external_force_xyz_n;mx,my,mz=q.external_moment_xyz_nm;phi=.2
    for i,row in enumerate(r['bolts']):
        angle=2*math.pi*i/count;y=radius*math.cos(angle);z=radius*math.sin(angle)
        normal_external=fx/count+2*(my*z-mz*y)/(count*radius**2)
        normal_bolt=40000+phi*normal_external
        shear_y=fy/count-mx*z/(count*radius**2);shear_z=fz/count+mx*y/(count*radius**2)
        assert row['bolt_tension_n']==pytest.approx(normal_bolt,rel=2e-13)
        assert row['clamped_member_compression_n']==pytest.approx(40000-(1-phi)*normal_external,rel=2e-13)
        assert row['bolt_shear_yz_n']==pytest.approx((shear_y,shear_z),rel=2e-13,abs=1e-10)
        normalstress=normal_bolt/3e-4
        assert row['von_mises_stress_pa']==pytest.approx(math.sqrt(normalstress**2+3*(shear_y**2+shear_z**2)/(3.5e-4)**2),rel=2e-13)
    assert r['resultant_force_residual_n']==pytest.approx((0.,0.,0.),abs=1e-9)
    assert r['resultant_moment_residual_nm']==pytest.approx((0.,0.,0.),abs=1e-9)

def test_irregular_heterogeneous_joint_virtual_work_and_equilibrium():
    q=request(5);points=[(.3,0.),(.1,.2),(-.25,.15),(-.2,-.3),(.15,-.15)]
    bolts=tuple(b.model_copy(update={'position_yz_m':point,'normal_bolt_stiffness_n_m':b.normal_bolt_stiffness_n_m*(i+1),
        'normal_clamped_member_stiffness_n_m':b.normal_clamped_member_stiffness_n_m/(i+1),'tangential_bolt_stiffness_n_m':b.tangential_bolt_stiffness_n_m*(i+1)}) for i,(b,point) in enumerate(zip(q.bolts,points)))
    q=q.model_copy(update={'bolts':bolts});r=evaluate(q,C);assert r['status']=='computed'
    fx,fy,fz=q.external_force_xyz_n;mx,my,mz=q.external_moment_xyz_nm
    work=.5*(np.dot((fx,my,mz),r['normal_joint_generalized_displacements'])+np.dot((fy,fz,mx),r['tangential_joint_generalized_displacements']))
    assert r['incremental_elastic_energy_j']==pytest.approx(work,rel=2e-13)
    assert r['resultant_force_residual_n']==pytest.approx((0.,0.,0.),abs=2e-9)
    assert r['resultant_moment_residual_nm']==pytest.approx((0.,0.,0.),abs=2e-9)

def test_contact_separation_is_not_continued_as_linear_safe_state():
    assert evaluate(request(force=(1e7,0.,0.)),C)['status']=='out_of_scope'

def test_unknown_external_force_not_zero():
    assert evaluate(request().model_copy(update={'external_force_xyz_n':None}),C)['status']=='needs_data'

def test_load_reversal_preserves_incremental_energy_and_reverses_increment():
    q=request();a=evaluate(q,C);b=evaluate(q.model_copy(update={'external_force_xyz_n':tuple(-v for v in q.external_force_xyz_n),'external_moment_xyz_nm':tuple(-v for v in q.external_moment_xyz_nm)}),C)
    assert a['incremental_elastic_energy_j']==pytest.approx(b['incremental_elastic_energy_j'])
    for first,second in zip(a['bolts'],b['bolts']):assert first['bolt_tension_n']+second['bolt_tension_n']==pytest.approx(80000.)
