"""Independent per-leg force recursion and work/kinematic limits."""
from decimal import Decimal,localcontext
import math
import pytest
from pipeline_integrity.reeved_hoist import ReevedHoistRequest as Q,evaluate_reeved_hoist as solve

def make(**kw):return Q(**(dict(supported_load_N=1200.,working_rope_parts=4,per_sheave_efficiency=.9,load_rise_m=2.,successive_loaded_leg_loss_and_direct_hoist_route_reviewed=True)|kw))

@pytest.mark.parametrize('n,e',[(1,.6),(2,.8),(4,.9),(7,.95),(12,.7),(4,1.),(5,1-1e-15)])
def test_independent_leg_recursion_and_virtual_work(n,e):
 with localcontext() as c:
  c.prec=65;eps=Decimal(str(e));ratio=Decimal(1);supported=Decimal(0)
  for _ in range(n):ratio*=eps;supported+=ratio
 r=solve(make(working_rope_parts=n,per_sheave_efficiency=e));expected=float(supported)
 assert r.mechanical_advantage==pytest.approx(expected,rel=3e-15)
 leg=r.required_lead_pull_N;total=0.
 for _ in range(n):leg*=e;total+=leg
 assert total==pytest.approx(1200.,rel=3e-15)
 assert r.lead_rope_travel_m==2*n
 assert r.useful_lifting_work_J==2400.
 assert r.lead_input_work_J==pytest.approx(r.useful_lifting_work_J+r.dissipated_work_J,rel=3e-15)
 assert 0<r.lifting_efficiency<=1

@pytest.mark.parametrize('n',[1,100,1000000])
def test_perfect_sheaves_exact_limit(n):
 r=solve(make(working_rope_parts=n,per_sheave_efficiency=1.));assert r.mechanical_advantage==n and r.lifting_efficiency==1
 assert r.dissipated_work_J==0 and r.required_lead_pull_N==1200/n

def test_zero_load_and_missing_displacement_are_distinct():
 r=solve(make(supported_load_N=0.,load_rise_m=None));assert r.required_lead_pull_N==0 and r.lead_input_work_J is None

@pytest.mark.parametrize('scale',[.01,10.])
def test_force_scaling(scale):
 a=solve(make());b=solve(make(supported_load_N=1200*scale));assert b.required_lead_pull_N==pytest.approx(a.required_lead_pull_N*scale)
 assert b.lead_input_work_J==pytest.approx(a.lead_input_work_J*scale)

@pytest.mark.parametrize('kw',[{'supported_load_N':None},{'per_sheave_efficiency':None},{'successive_loaded_leg_loss_and_direct_hoist_route_reviewed':False}])
def test_unknown_or_unreviewed_withheld(kw):assert solve(make(**kw)).status=='needs_data'

@pytest.mark.parametrize('kw',[{'working_rope_parts':0},{'working_rope_parts':1.5},{'per_sheave_efficiency':0.},{'per_sheave_efficiency':1.1},{'supported_load_N':-1.}])
def test_invalid_domain(kw):
 with pytest.raises(ValueError):make(**kw)
