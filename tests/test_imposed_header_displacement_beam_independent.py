"""Prescribed terminal translation through the existing frame solver, not a new beam law."""
import runpy
import numpy as np
import pytest
from pipeline_integrity.structural import FrameSupport,solve_frame

EXAMPLE=runpy.run_path('examples/pipe_frame.py')
P=np.polynomial.Polynomial

def boundary_solution(length,displacement,kind,prop):
    pieces=2 if kind=='interior_prop' else 1
    basis=[P([0.]*i+[1.]) for i in range(4)]
    rows=[];rhs=[]
    def equation(terms,value=0.):
        row=np.zeros(4*pieces)
        for piece,x,order,sign in terms:
            row[4*piece:4*piece+4]+=[sign*b.deriv(order)(x) for b in basis]
        rows.append(row);rhs.append(value)
    equation([(0,0.,0,1)])
    equation([(0,0.,1,1)])
    if pieces==2:
        equation([(0,prop,0,1)])
        equation([(1,0.,0,1)])
        equation([(0,prop,1,1),(1,0.,1,-1)])
        equation([(0,prop,2,1),(1,0.,2,-1)])
    right=length-prop if pieces==2 else length
    equation([(pieces-1,right,0,1)],displacement)
    equation([(pieces-1,right,2 if kind=='fixed_pinned' else 1,1)])
    coefficients=np.linalg.solve(rows,rhs)
    return [P(coefficients[4*i:4*i+4]) for i in range(pieces)]

@pytest.mark.parametrize('kind',['fixed_fixed','fixed_pinned','interior_prop'])
@pytest.mark.parametrize('displacement',[.0001,-.0002])
def test_header_movement_matches_piecewise_boundary_solution_and_reactions(kind,displacement):
    pytest.importorskip('Pynite')
    length=4.;prop=1.3
    request=EXAMPLE['make_case']()
    tip=request.nodes[1].model_copy(update={'position_m':(length,0.,0.)})
    fixed=('DX','DY','DZ','RX','RY') if kind=='fixed_pinned' else ('DX','DY','DZ','RX','RY','RZ')
    terminal=FrameSupport(node_id='tip',fixed_dofs=fixed,imposed_translations_m={'DY':displacement},evidence_id='reference')
    nodes=(request.nodes[0],tip)
    samples=tuple(float(x) for x in np.linspace(0.,1.,11))
    members=(request.members[0].model_copy(update={'sample_fractions':samples}),)
    supports=request.supports+(terminal,)
    if kind=='interior_prop':
        middle=request.nodes[1].model_copy(update={'id':'middle','position_m':(prop,0.,0.)})
        nodes=(request.nodes[0],middle,tip)
        members=(members[0].model_copy(update={'j_node':'middle'}),
            members[0].model_copy(update={'id':'continuation','i_node':'middle','j_node':'tip'}))
        supports+=(FrameSupport(node_id='middle',fixed_dofs=('DY',),evidence_id='reference'),)
    request=request.model_copy(update={'nodes':nodes,'members':members,'supports':supports,'node_loads':(),'line_loads':()})
    result=solve_frame(request,EXAMPLE['context']())
    assert result['status']=='computed',result.get('issues')
    output=result['combinations']['service']
    rigidity=request.materials[0].young_modulus_pa*request.sections[0].iz_m4
    references=boundary_solution(length,displacement,kind,prop)
    lengths=[prop,length-prop] if len(references)==2 else [length]
    for member,reference,span in zip(members,references,lengths,strict=True):
        values=[r['local_translation_m'][1] for r in output['members'][member.id]['samples']]
        assert values==pytest.approx(reference(np.asarray(samples)*span),rel=1e-8,abs=2e-12)
    root=output['nodes']['anchor'];end=output['nodes']['tip']
    assert root['reaction_force_n'][1]==pytest.approx(rigidity*references[0].deriv(3)(0.),rel=2e-9,abs=1e-8)
    assert root['reaction_moment_nm'][2]==pytest.approx(-rigidity*references[0].deriv(2)(0.),rel=2e-9,abs=1e-8)
    assert end['reaction_force_n'][1]==pytest.approx(-rigidity*references[-1].deriv(3)(lengths[-1]),rel=2e-9,abs=1e-8)
    assert end['reaction_moment_nm'][2]==pytest.approx(rigidity*references[-1].deriv(2)(lengths[-1]),rel=2e-9,abs=1e-8)
    if kind=='interior_prop':
        shear_jump=rigidity*(references[1].deriv(3)(0)-references[0].deriv(3)(prop))
        assert output['nodes']['middle']['reaction_force_n'][1]==pytest.approx(shear_jump,rel=2e-9)
    assert end['translation_m'][1]==pytest.approx(displacement,abs=1e-15)
    assert output['static_balance']['passed']
