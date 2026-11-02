"""Independent minimum elastic complementary-energy force distribution."""
import numpy as np
import pytest
from scipy.optimize import minimize
from pipeline_integrity.anchor_row_demand import AnchorRowDemandRequest as Request,evaluate_anchor_row_demand as evaluate

def request(count=2,total=4,moment=800.,lever=.2,shear=(200.,-100.)):
    return Request(total_anchor_count=total,tension_row_anchor_count=count,bending_moment_magnitude_nm=moment,
        resultant_shear_n=shear,tension_row_to_compression_resultant_lever_arm_m=lever,compression_resultant_basis_id='reviewed-bearing-line',
        pure_moment_equal_tension_row_equal_shear_and_bearing_location_reviewed=True)

@pytest.mark.parametrize('count,total,moment,lever',[(2,4,800.,.2),(3,6,1500.,.3),(4,8,4000.,.5),(1,3,50.,.1)])
def test_minimum_complementary_energy_with_independent_force_moment_constraints(count,total,moment,lever):
    q=request(count,total,moment,lever);r=evaluate(q)
    # Unknown normalized bolt tensions and compression. Equal bolt stiffness;
    # force and cross-product moment are imposed independently as constraints.
    force_scale=moment/lever
    xy=[np.array([lever,(i-(count-1)/2)*.1,0.]) for i in range(count)]
    def normal_balance(x):return np.sum(x[:count])-x[-1]
    def moment_balance(x):
        reaction=sum((np.cross(point,np.array([0.,0.,x[i]])) for i,point in enumerate(xy)),start=np.zeros(3))
        return reaction[1]+moment/force_scale
    def cross_balance(x):
        return sum(point[1]*x[i] for i,point in enumerate(xy))
    constraints=[{'type':'eq','fun':normal_balance},{'type':'eq','fun':moment_balance}]
    if count>1:constraints.append({'type':'eq','fun':cross_balance})
    solved=minimize(lambda x:.5*np.dot(x[:count],x[:count]),np.ones(count+1),method='SLSQP',constraints=constraints,
        options={'ftol':1e-13,'maxiter':200})
    assert solved.success
    assert solved.x[:count]*force_scale==pytest.approx(np.full(count,r['tension_per_tension_row_anchor_n']),rel=3e-7)
    assert solved.x[-1]*force_scale==pytest.approx(r['compression_resultant_magnitude_n'],rel=3e-7)
    assert np.array(r['equal_share_shear_per_anchor_n'])*total==pytest.approx(q.resultant_shear_n,rel=1e-14)

def test_moving_reviewed_bearing_line_changes_demand_without_choosing_contact_law():
    a=evaluate(request(lever=.2));b=evaluate(request(lever=.1))
    assert b['tension_per_tension_row_anchor_n']==pytest.approx(2*a['tension_per_tension_row_anchor_n'])
    assert b['contact_distribution_prying_or_anchor_capacity_calculated'] is False

def test_unknown_lever_arm_not_defaulted():
    assert evaluate(request().model_copy(update={'tension_row_to_compression_resultant_lever_arm_m':None}))['status']=='needs_data'

def test_zero_moment_preserves_shear_only_demand():
    r=evaluate(request(moment=0.));assert r['tension_per_tension_row_anchor_n']==0
    assert r['equal_share_shear_per_anchor_n']==(50.,-25.)

def test_inconsistent_anchor_counts_rejected():
    with pytest.raises(ValueError,match='exceeds'):evaluate(request(count=5,total=4))
