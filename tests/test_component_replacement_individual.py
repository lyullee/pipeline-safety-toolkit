"""Independent volume ledgers for one uniform withdrawal/refill event."""
from decimal import Decimal,localcontext
import pytest
from pipeline_integrity.component_replacement import ComponentReplacementRequest as Q,solve_component_replacement as solve

def make(**kw):return Q(**(dict(system_volume_m3=8.,initial_component_volume_fraction=.2,feed_component_volume_fraction=1.,target_component_volume_fraction=.6,additive_volumes_common_state_and_uniform_withdrawal_reviewed=True)|kw))

@pytest.mark.parametrize('a,b,f',[(0.,1.,.25),(.2,1.,.5),(.8,0.,.3),(.2,.7,1.),(.2,.7,0.),(1.,0.,1.),(1.,1.,0.),(0.,0.,0.)])
def test_inverse_from_independent_decimal_component_inventory(a,b,f):
 with localcontext() as c:
  c.prec=65;aa,bb,ff=map(lambda z:Decimal(str(z)),(a,b,f));target=float((1-ff)*aa+ff*bb)
 r=solve(make(initial_component_volume_fraction=a,feed_component_volume_fraction=b,target_component_volume_fraction=target));assert r.status=='computed'
 assert r.replacement_fraction==pytest.approx(f,abs=2e-16)
 before=8*a;retained=(8-r.replacement_volume_m3)*a;added=r.replacement_volume_m3*b
 assert r.final_component_volume_m3==pytest.approx(retained+added,abs=2e-15)
 assert r.withdrawn_component_volume_m3==pytest.approx(before-retained,abs=2e-15)
 assert r.final_component_volume_fraction==pytest.approx(target,abs=2e-16)
 assert abs(r.component_budget_residual_m3)<2e-15

@pytest.mark.parametrize('scale',[.001,10.,1e6])
def test_extensive_inventory_scale(scale):
 a=solve(make());b=solve(make(system_volume_m3=8*scale));assert b.replacement_fraction==a.replacement_fraction
 assert b.replacement_volume_m3==pytest.approx(a.replacement_volume_m3*scale)
 assert b.final_component_volume_m3==pytest.approx(a.final_component_volume_m3*scale)

@pytest.mark.parametrize('target',[.1,.9])
def test_unreachable_target_is_not_clipped(target):
 r=solve(make(feed_component_volume_fraction=.7,target_component_volume_fraction=target));assert r.status=='out_of_scope' and r.replacement_volume_m3 is None

def test_no_feed_contrast_cannot_reach_other_target():
 assert solve(make(feed_component_volume_fraction=.2)).status=='non_identifiable'

@pytest.mark.parametrize('field',['initial_component_volume_fraction','feed_component_volume_fraction','target_component_volume_fraction'])
def test_unknown_fraction_is_preserved(field):assert solve(make(**{field:None})).status=='needs_data'

def test_basis_review_required():assert solve(make(additive_volumes_common_state_and_uniform_withdrawal_reviewed=False)).status=='needs_data'

@pytest.mark.parametrize('field,value',[('system_volume_m3',0.),('initial_component_volume_fraction',-.1),('feed_component_volume_fraction',1.1)])
def test_invalid_domain(field,value):
 with pytest.raises(ValueError):make(**{field:value})
