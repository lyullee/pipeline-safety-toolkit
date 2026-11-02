"""Existing spring-supported eigenbuckling versus the beam/foundation variational minimum."""
import math,runpy
import pytest
from pipeline_integrity.structural import FrameSupport
from pipeline_integrity.buckling import BucklingRequest,evaluate_buckling,buckling_boundary_metadata
MAKE=runpy.run_path('examples/buckling.py')['make_case']

@pytest.mark.parametrize('optimal_half_waves',[1,2,3])
def test_distributed_foundation_critical_force_converges_to_independent_wavenumber_minimum(optimal_half_waves):
    errors=[]
    for elements in (8,16,30):
        q,c=MAKE(reviewed=True,elements=elements);length=4.;section=q.frame.sections[0];e=q.frame.materials[0].young_modulus_pa
        ei=e*section.iz_m4;wave=optimal_half_waves*math.pi/length
        # Distributed support modulus has units N/m^2. Its nodal stiffness is
        # modulus*tributary_length [N/m], never the source's ambiguous unit label.
        foundation=ei*wave**4;h=length/elements;supports=[]
        for i,node in enumerate(q.frame.nodes):
            fixed=['DZ','RX','RY']
            if i==0:fixed+=['DX','DY']
            if i==elements:fixed+=['DY']
            supports.append(FrameSupport(node_id=node.id,fixed_dofs=tuple(fixed),
                translation_springs_n_m={} if i in (0,elements) else {'DY':foundation*h},evidence_id='authored'))
        frame=q.frame.model_copy(update={'supports':tuple(supports)})
        q=BucklingRequest.model_validate(q.model_copy(update={'frame':frame}).model_dump())
        c=c.model_copy(update={'metadata':{'buckling_boundary':buckling_boundary_metadata(q)}})
        result=evaluate_buckling(q,c);assert result['status']=='computed_diagnostic',result['issues']
        actual=result['solution']['critical_positive_load_factor']*1000.
        # Independent Rayleigh field sin(kx) gives EI*k^2+K/k^2. Choosing K
        # makes an admissible integer mode equal the infinite-line minimum.
        expected=2*math.sqrt(ei*foundation)
        finite=min(ei*(n*math.pi/length)**2+foundation/(n*math.pi/length)**2 for n in range(1,15))
        assert finite==pytest.approx(expected,rel=2e-14)
        errors.append(abs(actual/expected-1))
        assert all(m['original_free_dof_equilibrium_passed'] for m in result['solution']['modes'])
    assert errors[2]<errors[1]<errors[0]
    assert errors[-1]<.0003
