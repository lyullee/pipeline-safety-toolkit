"""Existing frame FEM against separately integrated beam boundary problems."""
import math
import runpy

import numpy as np
import pytest

from pipeline_integrity.structural import (
    FrameLineLoad, FrameMember, FrameNode, FrameNodeLoad, FrameSupport, solve_frame,
)

EXAMPLE=runpy.run_path('examples/pipe_frame.py')
SAMPLES=tuple(np.linspace(0.,1.,11))


def solve(request):
    pytest.importorskip('Pynite')
    result=solve_frame(request,EXAMPLE['context']())
    assert result['status']=='computed'
    out=result['combinations']['service']
    assert out['static_balance']['passed']
    return out


def samples(out,member):
    return np.array([s['local_translation_m'][1] for s in out['members'][member]['samples']])


def polynomial_span_reference(count,ends):
    """Integrate v''''=-1 and solve stated end/continuity constraints."""
    poly=np.polynomial.Polynomial
    particular=poly([0.,0.,0.,0.,-1/24])
    basis=[poly([0.]*k+[1.]) for k in range(4)]
    rows=[];rhs=[]
    def add(terms):
        row=np.zeros(4*count);constant=0.
        for segment,x,order,factor in terms:
            for k,b in enumerate(basis): row[4*segment+k]+=factor*b.deriv(order)(x)
            constant+=factor*particular.deriv(order)(x)
        rows.append(row);rhs.append(-constant)
    if count==1 and ends=='propped':
        for x,order in ((0.,0),(0.,1),(1.,0),(1.,2)): add([(0,x,order,1.)])
    else:
        for i in range(count):
            add([(i,0.,0,1.)]);add([(i,1.,0,1.)])
        add([(0,0.,2,1.)]);add([(count-1,1.,2,1.)])
        for i in range(count-1):
            add([(i,1.,1,1.),(i+1,0.,1,-1.)])
            add([(i,1.,2,1.),(i+1,0.,2,-1.)])
    coefficients=np.linalg.solve(np.array(rows),np.array(rhs))
    assert np.max(np.abs(np.array(rows)@coefficients-rhs))<1e-14
    return [particular+sum((coefficients[4*i+k]*b for k,b in enumerate(basis)),poly([0.]))
            for i in range(count)]


@pytest.mark.parametrize('length,load',[(2.,100.),(4.,220.),(7.,15.)])
def test_propped_uniform_span_matches_boundary_solution_and_reactions(length,load):
    q=EXAMPLE['make_case']()
    tip=q.nodes[1].model_copy(update={'position_m':(length,0.,0.)})
    member=q.members[0].model_copy(update={'sample_fractions':SAMPLES})
    prop=FrameSupport(node_id='tip',fixed_dofs=('DX','DY','DZ','RX','RY'),evidence_id='reference')
    line=FrameLineLoad(member_id='pipe',global_direction='FY',start_n_m=-load,end_n_m=-load,
                       case_id='point',evidence_id='reference')
    q=q.model_copy(update={'nodes':(q.nodes[0],tip),'members':(member,),
        'supports':q.supports+(prop,),'node_loads':(),'line_loads':(line,)})
    out=solve(q);ei=q.materials[0].young_modulus_pa*q.sections[0].iz_m4
    reference=polynomial_span_reference(1,'propped')[0]
    assert samples(out,'pipe')==pytest.approx(reference(SAMPLES)*load*length**4/ei,rel=2e-9,abs=2e-13)
    assert out['nodes']['anchor']['reaction_force_n'][1]==pytest.approx(5*load*length/8,rel=1e-11)
    assert out['nodes']['tip']['reaction_force_n'][1]==pytest.approx(3*load*length/8,rel=1e-11)
    assert abs(out['nodes']['anchor']['reaction_moment_nm'][2])==pytest.approx(load*length**2/8,rel=1e-11)
    assert out['nodes']['tip']['reaction_moment_nm'][2]==pytest.approx(0.,abs=1e-10)


def point_layout(length,fraction,load,simply_supported):
    q=EXAMPLE['make_case']();a=length*fraction
    nodes=(q.nodes[0],FrameNode(id='load',position_m=(a,0.,0.)),
           q.nodes[1].model_copy(update={'position_m':(length,0.,0.)}))
    members=tuple(FrameMember(id=mid,i_node=i,j_node=j,section_id='annulus',
        material_id='declared-elastic',sample_fractions=SAMPLES)
        for mid,i,j in (('left','anchor','load'),('right','load','tip')))
    supports=q.supports
    if simply_supported:
        supports=(FrameSupport(node_id='anchor',fixed_dofs=('DX','DY','DZ','RX','RY'),evidence_id='reference'),
                  FrameSupport(node_id='tip',fixed_dofs=('DY','DZ','RX','RY'),evidence_id='reference'))
    return q.model_copy(update={'nodes':nodes,'members':members,'supports':supports,'line_loads':(),
        'node_loads':(FrameNodeLoad(node_id='load',direction='FY',value=-load,case_id='point',evidence_id='reference'),)})


@pytest.mark.parametrize('fraction',[.2,.45,.8])
def test_offset_cantilever_point_load_preserves_unloaded_extension(fraction):
    length,load=4.,1000.;a=length*fraction
    q=point_layout(length,fraction,load,False);out=solve(q)
    ei=q.materials[0].young_modulus_pa*q.sections[0].iz_m4
    for mid,x in (('left',np.array(SAMPLES)*a),('right',a+np.array(SAMPLES)*(length-a))):
        reference=np.where(x<=a,-load*x*x*(3*a-x)/(6*ei),-load*a*a*(3*x-a)/(6*ei))
        assert samples(out,mid)==pytest.approx(reference,rel=2e-10,abs=2e-13)
    assert out['nodes']['anchor']['reaction_force_n'][1]==pytest.approx(load,rel=1e-11)
    assert out['nodes']['anchor']['reaction_moment_nm'][2]==pytest.approx(load*a,rel=1e-11)
    assert out['nodes']['tip']['translation_m'][1]==pytest.approx(-load*a*a*(3*length-a)/(6*ei),rel=2e-10)


@pytest.mark.parametrize('fraction',[.2,.45,.8])
def test_eccentric_simply_supported_point_load_matches_piecewise_solution(fraction):
    length,load=4.,1000.;a=length*fraction;b=length-a
    q=point_layout(length,fraction,load,True);out=solve(q)
    ei=q.materials[0].young_modulus_pa*q.sections[0].iz_m4
    for mid,x in (('left',np.array(SAMPLES)*a),('right',a+np.array(SAMPLES)*b)):
        reference=np.where(x<=a,-load*b*x*(length**2-b*b-x*x)/(6*length*ei),
            -load*a*(length-x)*(length**2-a*a-(length-x)**2)/(6*length*ei))
        assert samples(out,mid)==pytest.approx(reference,rel=2e-10,abs=2e-13)
    assert out['nodes']['anchor']['reaction_force_n'][1]==pytest.approx(load*b/length,rel=1e-11)
    assert out['nodes']['tip']['reaction_force_n'][1]==pytest.approx(load*a/length,rel=1e-11)
    for name in ('anchor','tip'):
        assert out['nodes'][name]['reaction_moment_nm'][2]==pytest.approx(0.,abs=1e-10)


@pytest.mark.parametrize('count',[4,8])
def test_continuous_equal_spans_match_exact_piecewise_boundary_solution(count):
    length,load=2.5,160.;q=EXAMPLE['make_case']()
    nodes=tuple(FrameNode(id=f'support_{i}',position_m=(i*length,0.,0.)) for i in range(count+1))
    members=tuple(FrameMember(id=f'span_{i}',i_node=f'support_{i}',j_node=f'support_{i+1}',
        section_id='annulus',material_id='declared-elastic',sample_fractions=SAMPLES) for i in range(count))
    supports=tuple(FrameSupport(node_id=n.id,fixed_dofs=(('DX',) if i==0 else ())+('DY','DZ','RX','RY'),
                                evidence_id='reference') for i,n in enumerate(nodes))
    lines=tuple(FrameLineLoad(member_id=m.id,global_direction='FY',start_n_m=-load,end_n_m=-load,
        case_id='point',evidence_id='reference') for m in members)
    q=q.model_copy(update={'nodes':nodes,'members':members,'supports':supports,'line_loads':lines,'node_loads':()})
    out=solve(q);ei=q.materials[0].young_modulus_pa*q.sections[0].iz_m4
    references=polynomial_span_reference(count,'continuous')
    for i,ref in enumerate(references):
        assert samples(out,f'span_{i}')==pytest.approx(ref(SAMPLES)*load*length**4/ei,rel=2e-9,abs=2e-13)
    reactions=[references[0].deriv(3)(0.)]
    reactions.extend(references[i].deriv(3)(0.)-references[i-1].deriv(3)(1.) for i in range(1,count))
    reactions.append(-references[-1].deriv(3)(1.))
    for i,reaction in enumerate(reactions):
        assert out['nodes'][f'support_{i}']['reaction_force_n'][1]==pytest.approx(reaction*load*length,rel=2e-10)
    assert math.fsum(reactions)==pytest.approx(count,rel=2e-14)
