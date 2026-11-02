"""Discrete soil-spring beam versus an exact fourth-order foundation solution."""
import cmath,runpy
import numpy as np
import pytest
from pipeline_integrity.structural import FrameSupport
from pipeline_integrity.soil_pipe import SoilPipeRequest,evaluate_soil_pipe,soil_pipe_boundary_metadata

MAKE=runpy.run_path('examples/soil_pipe.py')['make_case']

def exact_profile(rigidity,foundation,amplitude,length):
    polynomial=np.polynomial.Polynomial
    ground=16*amplitude/length**4*polynomial([0.,0.,length**2,-2*length,1.])
    particular=ground-rigidity/foundation*ground.deriv(4)(0)
    kappa=(foundation/(4*rigidity))**.25
    def basis(j,x,order=0):
        root=(1 if j<2 else -1)*kappa+1j*kappa
        value=root**order*cmath.exp(root*x)
        return value.real if j%2==0 else value.imag
    constraints=((0.,0),(0.,1),(length,0),(length,1))
    coefficients=np.linalg.solve([[basis(j,x,order) for j in range(4)] for x,order in constraints],
        [-particular.deriv(order)(x) for x,order in constraints])
    def response(x,order=0):return particular.deriv(order)(x)+sum(a*basis(j,x,order) for j,a in enumerate(coefficients))
    return ground,response

@pytest.mark.parametrize('foundation,amplitude',[(1e4,.001),(1e5,-.001),(1e6,.0005)])
def test_prescribed_ground_profile_converges_in_displacement_and_support_reaction(foundation,amplitude):
    errors=[];reaction_errors=[];length=4.
    for elements in (4,8,16,32):
        q,c=MAKE(reviewed=True)
        rigidity=q.frame.materials[0].young_modulus_pa*q.frame.sections[0].iz_m4
        ground,reference=exact_profile(rigidity,foundation,amplitude,length)
        nodes=tuple(q.frame.nodes[0].model_copy(update={'id':'anchor' if i==0 else 'tip' if i==elements else f'n{i}',
            'position_m':(length*i/elements,0.,0.)}) for i in range(elements+1))
        members=tuple(q.frame.members[0].model_copy(update={'id':f'e{i}','i_node':nodes[i].id,'j_node':nodes[i+1].id}) for i in range(elements))
        supports=tuple(FrameSupport(node_id=node.id,fixed_dofs=('DX','DZ','RX','RY')+(('DY','RZ') if i in (0,elements) else ()),
            evidence_id='authored') for i,node in enumerate(nodes))
        frame=q.frame.model_copy(update={'nodes':nodes,'members':members,'supports':supports})
        soil=tuple(q.soil_supports[0].model_copy(update={'node_id':nodes[i].id,'stiffness_N_m':{'DY':foundation*length/elements},
            'ground_displacement_m':(0.,float(ground(length*i/elements)),0.)}) for i in range(1,elements))
        bodies=tuple(q.body_states[0].model_copy(update={'member_id':member.id}) for member in members)
        q=SoilPipeRequest.model_validate(q.model_copy(update={'frame':frame,'soil_supports':soil,'body_states':bodies}).model_dump(mode='python'))
        c=c.model_copy(update={'metadata':{'soil_pipe_boundary':soil_pipe_boundary_metadata(q)}})
        result=evaluate_soil_pipe(q,c)
        assert result['status']=='computed_diagnostic',result['issues']
        actual={r['node_id']:r['axes'][0]['pipe_displacement_m'] for r in result['solution']['soil_response']}
        errors.append(max(abs(actual[nodes[i].id]-reference(length*i/elements)) for i in range(1,elements))/abs(amplitude))
        reactions=result['solution']['physical_reactions_by_node']
        exact_reaction=rigidity*reference(0.,3)
        reaction_errors.append(abs(reactions['anchor'][1]/exact_reaction-1))
        assert result['solution']['physical_static_balance']['passed']
        assert result['solution']['physical_nodal_balance']['passed']
        assert result['solution']['spring_law_passed']
        for item in result['solution']['soil_response']:
            axis=item['axes'][0]
            assert axis['soil_force_on_pipe_N']==pytest.approx(-axis['stiffness_N_m']*axis['relative_displacement_m'],rel=2e-13,abs=1e-10)
    assert errors[0]>errors[1]>errors[2]>errors[3]
    assert reaction_errors[0]>reaction_errors[1]>reaction_errors[2]>reaction_errors[3]
    assert errors[-1]<4e-4
    assert reaction_errors[-1]<.004
