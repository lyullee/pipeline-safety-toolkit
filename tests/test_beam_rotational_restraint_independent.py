"""Actual spring-restraint frame field versus a separately constrained beam BVP."""
import runpy
import numpy as np
import pytest
from scipy.integrate import quad
from pipeline_integrity.structural import FrameLineLoad,FrameSupport,solve_frame

EXAMPLE=runpy.run_path('examples/pipe_frame.py')


def solve(ratio,length=4.,load=160.):
    q=EXAMPLE['make_case']();stiffness=q.materials[0].young_modulus_pa*q.sections[0].iz_m4
    spring=ratio*stiffness/length
    supports=tuple(FrameSupport(node_id=n,fixed_dofs=('DX','DY','DZ','RX','RY'),
        rotation_springs_nm_rad={'RZ':spring} if spring else {},evidence_id='reference') for n in ('anchor','tip'))
    fractions=tuple(np.linspace(0.,1.,17));member=q.members[0].model_copy(update={'sample_fractions':fractions})
    q=q.model_copy(update={'nodes':(q.nodes[0],q.nodes[1].model_copy(update={'position_m':(length,0.,0.)})),
        'members':(member,),'supports':supports,'node_loads':(),
        'line_loads':(FrameLineLoad(member_id='pipe',global_direction='FY',start_n_m=-load,end_n_m=-load,case_id='point',evidence_id='reference'),)})
    result=solve_frame(q,EXAMPLE['context']());assert result['status']=='computed'
    p=np.polynomial.Polynomial;part=p([0.,0.,0.,0.,-load/(24*stiffness)]);basis=[p([0.]*i+[1.]) for i in range(4)]
    # At x=0, EI*y''=k*y'; at x=L, EI*y''=-k*y'.
    constraints=[lambda f:f(0),lambda f:f(length),lambda f:stiffness*f.deriv(2)(0)-spring*f.deriv()(0),lambda f:stiffness*f.deriv(2)(length)+spring*f.deriv()(length)]
    matrix=np.array([[c(b) for b in basis] for c in constraints]);rhs=-np.array([c(part) for c in constraints]);coeff=np.linalg.solve(matrix,rhs)
    reference=part+sum((a*b for a,b in zip(coeff,basis)),p([0.]))
    return q,result['combinations']['service'],reference,stiffness,spring,fractions


@pytest.mark.parametrize('ratio',[0.,.1,1.,10.,100.])
def test_actual_intermediate_rotational_spring_response_matches_independent_bvp(ratio):
    q,r,y,ei,k,fractions=solve(ratio);length=4.;load=160.
    actual=[s['local_translation_m'][1] for s in r['members']['pipe']['samples']]
    assert actual==pytest.approx(y(np.array(fractions)*length),rel=3e-10,abs=2e-13)
    for name,x in [('anchor',0.),('tip',length)]:
        assert r['nodes'][name]['reaction_force_n'][1]==pytest.approx(load*length/2,rel=3e-11)
        assert r['nodes'][name]['reaction_moment_nm'][2]==pytest.approx(-k*y.deriv()(x),rel=3e-10,abs=1e-9)
    assert r['static_balance']['passed']
    bending=quad(lambda x:.5*ei*y.deriv(2)(x)**2,0,length,epsabs=1e-12)[0]
    supports=.5*k*(y.deriv()(0)**2+y.deriv()(length)**2)
    work=quad(lambda x:-load*y(x),0,length,epsabs=1e-12)[0]
    assert 2*(bending+supports)==pytest.approx(work,rel=3e-11,abs=1e-12)
    # The finite-displacement field agrees, independently, with work density.
    native_work=np.trapezoid(-load*np.array(actual),np.array(fractions)*length)
    assert abs(native_work-work)<.006*abs(work)


def test_stiffness_sequence_preserves_coarse_errors_and_converges_toward_fixed_limit():
    errors=[]
    for ratio in [1.,10.,100.,1000.]:
        q,r,y,ei,k,f=solve(ratio);actual=r['members']['pipe']['samples'][8]['local_translation_m'][1]
        fixed=-160*4**4/(384*ei);errors.append(abs(actual-fixed))
    assert all(b<a for a,b in zip(errors,errors[1:]))
    assert errors[-1]<.012*errors[0]


@pytest.mark.parametrize('length,load',[(2.,65.),(7.,240.)])
def test_actual_length_load_stiffness_have_coherent_si_scaling(length,load):
    q,r,y,ei,k,f=solve(3.,length,load)
    actual=r['members']['pipe']['samples'][8]['local_translation_m'][1]
    assert actual==pytest.approx(y(length/2),rel=3e-10)
    assert sum(r['nodes'][n]['reaction_force_n'][1] for n in ('anchor','tip'))==pytest.approx(load*length,rel=3e-11)
