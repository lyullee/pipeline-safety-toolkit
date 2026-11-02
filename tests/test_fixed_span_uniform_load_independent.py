"""Clamped-span finite elements against an independently solved beam boundary problem."""
import runpy
import numpy as np
import pytest

from pipeline_integrity.structural import FrameLineLoad, FrameSupport, solve_frame

EXAMPLE=runpy.run_path('examples/pipe_frame.py')


@pytest.mark.parametrize('length,load',[(2.,100.),(4.,220.),(7.,15.)])
def test_fixed_uniform_span_matches_boundary_solution_reactions_and_end_moments(length,load):
    pytest.importorskip('Pynite')
    # Independently solve the normalized fourth-order bending equation with
    # zero transverse displacement and rotation at both ends.
    # The load is constant, so integrate it exactly and solve four independent
    # end constraints rather than asking an adaptive collocation oracle to
    # resolve a floating-point residual below its numerical floor.
    polynomial=np.polynomial.Polynomial
    particular=polynomial([0.,0.,0.,0.,-1/24])
    basis=[polynomial([0.]*i+[1.]) for i in range(4)]
    constraints=[(0.,0),(0.,1),(1.,0),(1.,1)]
    matrix=np.array([[b.deriv(order)(x) for b in basis] for x,order in constraints])
    rhs=-np.array([particular.deriv(order)(x) for x,order in constraints])
    coefficients=np.linalg.solve(matrix,rhs)
    reference=particular+sum((value*b for value,b in zip(coefficients,basis)),polynomial([0.]))
    assert reference.deriv(4)(.4)==pytest.approx(-1.,abs=2e-15)
    assert all(abs(reference.deriv(order)(x))<2e-16 for x,order in constraints)
    request=EXAMPLE['make_case']()
    node=request.nodes[1].model_copy(update={'position_m':(length,0.,0.)})
    fractions=tuple(np.linspace(0.,1.,11))
    member=request.members[0].model_copy(update={'sample_fractions':fractions})
    support=FrameSupport(node_id='tip',fixed_dofs=('DX','DY','DZ','RX','RY','RZ'),evidence_id='reference')
    line=FrameLineLoad(member_id='pipe',global_direction='FY',start_n_m=-load,end_n_m=-load,
                       case_id='point',evidence_id='reference')
    request=request.model_copy(update={'nodes':(request.nodes[0],node),'members':(member,),
        'supports':request.supports+(support,),'node_loads':(),'line_loads':(line,)})
    result=solve_frame(request,EXAMPLE['context']())
    assert result['status']=='computed'
    calculated=result['combinations']['service']
    stiffness=request.materials[0].young_modulus_pa*request.sections[0].iz_m4
    deflection=[r['local_translation_m'][1] for r in calculated['members']['pipe']['samples']]
    expected=reference(fractions)*load*length**4/stiffness
    assert deflection==pytest.approx(expected,rel=2e-9,abs=2e-13)
    assert deflection[5]==pytest.approx(-load*length**4/(384*stiffness),rel=1e-10)
    for name in ('anchor','tip'):
        output=calculated['nodes'][name]
        assert output['reaction_force_n'][1]==pytest.approx(load*length/2.,rel=1e-11)
        assert abs(output['reaction_moment_nm'][2])==pytest.approx(load*length**2/12.,rel=1e-11)
    assert calculated['nodes']['anchor']['reaction_moment_nm'][2]==pytest.approx(
        -calculated['nodes']['tip']['reaction_moment_nm'][2],rel=1e-11)
    assert calculated['static_balance']['passed']
