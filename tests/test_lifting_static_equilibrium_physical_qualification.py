"""Static sling force and moment equilibrium against hand-solvable rigid-body cases."""
import math
import pytest
from examples.lifting import make_case
from pipeline_integrity.lifting import (
    EffectiveLiftingCapacity, ExternalLiftingLoad, LiftingRequest, Sling,
    boundary_metadata, evaluate_lifting,
)

G=9.80665

def capacities(slings):
    targets={('sling',s.id) for s in slings}
    targets.update(('attachment',s.attachment_id) for s in slings)
    targets.update(('anchor',s.anchor_id) for s in slings)
    return tuple(EffectiveLiftingCapacity(target_kind=k,target_id=n,force_limit_n=1e6,
        direction_policy='all_directions',applicability_id='analytic-screen',
        evidence_id='authored',reviewed=True) for k,n in sorted(targets))

def build(**updates):
    request,context=make_case(reviewed=True)
    slings=updates.get('slings',request.slings)
    updates.setdefault('capacities',capacities(slings))
    request=LiftingRequest.model_validate(request.model_copy(update=updates).model_dump(mode='python'))
    context=context.model_copy(update={'metadata':{'lifting_boundary':boundary_metadata(request)}})
    return request,context

def solve(**updates):
    request,context=build(**updates)
    return request,evaluate_lifting(request,context)


def test_symmetric_two_leg_tensions_and_force_moment_closure():
    request,r=solve()
    W=request.mass_kg*G
    assert r['solution']['sling_tensions_n']==pytest.approx({'left':W/math.sqrt(2),'right':W/math.sqrt(2)},rel=2e-13)
    assert r['solution']['net_force_n']==pytest.approx((0,0,0),abs=1e-9)
    assert r['solution']['net_moment_nm']==pytest.approx((0,0,0),abs=1e-9)
    assert r['acceptance']=='within_supplied_limits'


def test_three_vertical_legs_reconstruct_cg_moment_distribution():
    pts=(('one',(0.,0.,0.)),('two',(2.,0.,0.)),('three',(0.,3.,0.)))
    slings=tuple(Sling(id=n,attachment_id=n,attachment_m=pt,anchor_id='a_'+n,
        anchor_m=(pt[0],pt[1],3.),geometry_evidence_id='authored') for n,pt in pts)
    request,r=solve(slings=slings,center_of_mass_m=(0.4,0.9,0.0))
    W=request.mass_kg*G
    assert r['status']=='computed'
    assert r['solution']['sling_tensions_n']==pytest.approx({'one':.5*W,'two':.2*W,'three':.3*W},rel=2e-13)
    assert r['solution']['net_force_n']==pytest.approx((0,0,0),abs=1e-8)
    assert r['solution']['net_moment_nm']==pytest.approx((0,0,0),abs=1e-8)


def test_applied_couple_changes_leg_tensions_and_balances_moment():
    request0,_=make_case(reviewed=True)
    slings=tuple(Sling(id=str(x),attachment_id=str(x),attachment_m=(x,0.,0.),
        anchor_id='a'+str(x),anchor_m=(x,0.,1.),geometry_evidence_id='authored') for x in (-1.,1.))
    couple=ExternalLiftingLoad(id='couple',position_m=(0.,0.,0.),force_n=(0.,0.,0.),
        moment_nm=(0.,1000.,0.),evidence_id='authored')
    request,r=solve(slings=slings,external_loads=(couple,))
    W=request.mass_kg*G
    assert r['solution']['sling_tensions_n']==pytest.approx({'-1.0':(W-1000.)/2,'1.0':(W+1000.)/2},rel=2e-13)
    assert r['solution']['net_force_n']==pytest.approx((0,0,0),abs=1e-8)
    assert r['solution']['net_moment_nm']==pytest.approx((0,0,0),abs=1e-8)


def test_redundant_sling_geometry_withholds_arbitrary_load_sharing():
    request0,_=make_case(reviewed=True)
    slings=(*request0.slings,request0.slings[0].model_copy(update={'id':'redundant'}))
    _,r=solve(slings=slings)
    assert r['status']=='out_of_scope'
    assert r['solution'] is None
    assert 'not uniquely determined' in r['issues'][0]


