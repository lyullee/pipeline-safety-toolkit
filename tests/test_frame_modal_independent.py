"""Existing consistent-mass FEM versus independent beam eigenvalue boundaries."""
import math,runpy
import numpy as np
import pytest
from scipy.optimize import brentq
from pipeline_integrity.structural import FrameSupport
from pipeline_integrity.frame_modal import FrameModalRequest,evaluate_frame_modes

EXAMPLE=runpy.run_path('examples/pipe_frame.py')

def request(kind='simple',elements=8,**kw):
    frame=EXAMPLE['make_case']();length=4.
    nodes=tuple(frame.nodes[0].model_copy(update={'id':'anchor' if i==0 else 'tip' if i==elements else f'n{i}',
        'position_m':(length*i/elements,0.,0.)}) for i in range(elements+1))
    members=tuple(frame.members[0].model_copy(update={'id':f'e{i}','i_node':nodes[i].id,'j_node':nodes[i+1].id}) for i in range(elements))
    supports=[]
    for i,node in enumerate(nodes):
        fixed=['DX','DZ','RX','RY']
        if i==0:fixed+=['DY']+([] if kind=='simple' else ['RZ'])
        if i==elements and kind!='cantilever':fixed+=['DY']+(['RZ'] if kind=='clamped' else [])
        supports.append(FrameSupport(node_id=node.id,fixed_dofs=tuple(fixed),evidence_id='reference'))
    frame=frame.model_copy(update={'nodes':nodes,'members':members,'supports':tuple(supports),'node_loads':(),'line_loads':()})
    data=dict(frame=frame,density_by_material_kg_m3={frame.materials[0].id:7800.},gravity_for_native_weight_to_mass_m_s2=9.81,
        requested_modes=3,unloaded_linear_frame_and_consistent_mass_reviewed=True)
    return FrameModalRequest(**(data|kw)),EXAMPLE['context']()

def roots(kind):
    if kind=='simple':return np.arange(1,4)*math.pi
    target=-1 if kind=='cantilever' else 1
    brackets=[(1.,2.),(4.,5.),(7.,9.)] if kind=='cantilever' else [(4.,5.),(7.,9.),(10.,12.)]
    return np.array([brentq(lambda x:math.cosh(x)*math.cos(x)-target,a,b,xtol=1e-13) for a,b in brackets])

@pytest.mark.parametrize('kind',['simple','clamped','cantilever'])
def test_modal_frequencies_converge_to_independent_beam_boundary_roots(kind):
    errors=[]
    for elements in (4,8,16):
        q,c=request(kind,elements)
        result=evaluate_frame_modes(q,c)
        assert result.status=='computed',result.issues
        material=q.frame.materials[0];section=q.frame.sections[0]
        exact=roots(kind)**2/(2*math.pi*4**2)*math.sqrt(material.young_modulus_pa*section.iz_m4/(7800*section.area_m2))
        computed=np.array([m.frequency_hz for m in result.modes])
        errors.append(float(np.max(np.abs(computed/exact-1))))
        assert result.maximum_mass_orthogonality_error<1e-10
        assert max(m.relative_eigen_residual for m in result.modes)<1e-9
        if kind=='simple':
            mode=result.modes[0].nodal_displacements_and_rotations
            y=np.array([mode['anchor' if i==0 else 'tip' if i==elements else f'n{i}'][1] for i in range(elements+1)])
            y/=y[elements//2]
            assert y==pytest.approx(np.sin(np.linspace(0,math.pi,elements+1)),abs=3e-8)
    assert errors[0]>errors[1]>errors[2]
    assert errors[-1]<2e-4

def test_elastic_modulus_and_density_similarity():
    q,c=request();base=evaluate_frame_modes(q,c)
    scaled_material=q.frame.materials[0].model_copy(update={'young_modulus_pa':4*q.frame.materials[0].young_modulus_pa})
    frame=q.frame.model_copy(update={'materials':(scaled_material,)})
    twice=evaluate_frame_modes(q.model_copy(update={'frame':frame}),c)
    half=evaluate_frame_modes(q.model_copy(update={'density_by_material_kg_m3':{scaled_material.id:4*7800.}}),c)
    assert [m.frequency_hz for m in twice.modes]==pytest.approx([2*m.frequency_hz for m in base.modes],rel=1e-10)
    assert [m.frequency_hz for m in half.modes]==pytest.approx([m.frequency_hz/2 for m in base.modes],rel=1e-10)

def test_native_weight_to_mass_gravity_cancels_without_changing_modal_mass():
    q,c=request();one=evaluate_frame_modes(q,c)
    other=evaluate_frame_modes(q.model_copy(update={'gravity_for_native_weight_to_mass_m_s2':1.}),c)
    assert [m.frequency_hz for m in other.modes]==pytest.approx([m.frequency_hz for m in one.modes],rel=1e-10)

def test_unreviewed_modal_mass_does_not_run_the_native_model():
    q,c=request(unloaded_linear_frame_and_consistent_mass_reviewed=False)
    assert evaluate_frame_modes(q,c).status=='needs_data'

def test_missing_material_density_is_not_inferred_from_a_material_name():
    with pytest.raises(ValueError):request(density_by_material_kg_m3={})

@pytest.mark.parametrize('kind,point_stiffness_factor',[('simple',48.),('clamped',192.),('cantilever',3.)])
def test_declared_point_mass_converges_to_independent_massless_beam_compliance_limit(kind,point_stiffness_factor):
    errors=[];point_mass=5.
    for density in (1.,.1,.01,.001):
        q,c=request(kind,8)
        node='tip' if kind=='cantilever' else 'n4'
        q=q.model_copy(update={'requested_modes':1,'density_by_material_kg_m3':{q.frame.materials[0].id:density},
            'nodal_concentrated_mass_kg':{node:point_mass}})
        result=evaluate_frame_modes(q,c)
        assert result.status=='computed',result.issues
        rigidity=q.frame.materials[0].young_modulus_pa*q.frame.sections[0].iz_m4
        exact=math.sqrt(point_stiffness_factor*rigidity/(4**3*point_mass))/(2*math.pi)
        errors.append(abs(result.modes[0].frequency_hz/exact-1))
    assert errors[0]>errors[1]>errors[2]
    assert errors[-1]<1e-5
