"""Independent resultant integration and classical rectangular line section."""
import math
import numpy as np
import pytest
from pipeline_integrity.weld_line_group import WeldLineSegment as Segment,WeldLineGroupRequest as Request,evaluate_weld_line_group as evaluate

def request(points,force=(10.,20.,30.),moment=(40.,50.,60.)):
    return Request(segments=tuple(Segment(start_m=a,end_m=b) for a,b in zip(points,points[1:]+points[:1])),
        force_n=force,moment_about_line_centroid_nm=moment,uniform_effective_throat_m=.004,planar_equal_stiffness_elastic_line_group_reviewed=True)

@pytest.mark.parametrize('force,moment',[(f,m) for f,m in [((20.,0.,0.),(0.,0.,0.)),((0.,30.,0.),(0.,0.,0.)),((0.,0.,40.),(0.,0.,0.)),((0.,0.,0.),(50.,0.,0.)),((0.,0.,0.),(0.,60.,0.)),((0.,0.,0.),(0.,0.,70.)),((20.,-30.,40.),(-50.,60.,-70.))]])
def test_independent_line_integral_recovers_all_six_resultants(force,moment):
    q=request([(0.,0.),(1.2,.1),(.8,.9),(-.2,.6)],force,moment);r=evaluate(q)
    totalforce=np.zeros(3);totalmoment=np.zeros(3);nodes,weights=np.polynomial.legendre.leggauss(5)
    for segment,tractions in zip(q.segments,r['endpoint_traction_n_m']):
        start=np.array(segment.start_m);end=np.array(segment.end_m);l=np.linalg.norm(end-start)
        for node,weight in zip(nodes,weights):
            xi=(node+1)/2;point=start+xi*(end-start)-r['line_centroid_m']
            force_density=(1-xi)*np.array(tractions[0])+xi*np.array(tractions[1])
            totalforce+=l/2*weight*force_density
            totalmoment+=l/2*weight*np.cross(np.r_[point,0.],force_density)
    assert totalforce==pytest.approx(force,abs=3e-12)
    assert totalmoment==pytest.approx(moment,abs=3e-12)

@pytest.mark.parametrize('a,b,shear,moment',[(.2,.3,200.,30.),(.5,.1,100.,-40.),(.7,.6,-300.,80.)])
def test_rectangular_line_section_modulus_and_orthogonal_resultant(a,b,shear,moment):
    r=evaluate(request([(-a/2,-b/2),(a/2,-b/2),(a/2,b/2),(-a/2,b/2)],(shear,0.,0.),(moment,0.,0.)))
    section_modulus=a*b+b*b/3
    expected=math.hypot(shear/(2*(a+b)),moment/section_modulus)
    assert r['maximum_endpoint_resultant_n_m']==pytest.approx(expected,rel=3e-14)
    assert r['maximum_nominal_resultant_stress_pa']==pytest.approx(expected/.004,rel=3e-14)

def test_unknown_loads_are_not_zero():
    q=request([(0.,0.),(1.,0.),(0.,1.)]).model_copy(update={'force_n':None})
    assert evaluate(q)['status']=='needs_data'

def test_collinear_group_rejected():
    with pytest.raises(ValueError,match='nondegenerate'):
        evaluate(request([(0.,0.),(1.,0.),(2.,0.)]))
