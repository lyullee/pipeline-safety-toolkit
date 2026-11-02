import math
import numpy as np
import pytest
from scipy.optimize import brentq
from pipeline_integrity.contracts import EvidenceRef,RunContext
from pipeline_integrity.udl_span_sizing import UDLSpanSizingRequest,evaluate_udl_span_sizing

CTX=RunContext(asset_id='authored-span',scenario_id='supplied-ceiling',clock_id='static',origin='synthetic',
    evidence=(EvidenceRef(id='reviewed-span',citation='Authored actual SI section and reviewed end restraint and stress ceiling',origin='synthetic'),))


def request(**changes):
    return UDLSpanSizingRequest(**dict(dict(id='span-inverse',asset_id=CTX.asset_id,clock_id=CTX.clock_id,
        outside_diameter_m=.1,inside_diameter_m=.08,young_modulus_pa=210e9,poisson_ratio=.3,
        downward_uniform_load_n_m=180.,supplied_bending_stress_ceiling_pa=6e6,equal_end_restraint='pinned',
        span_bracket_m=(1.,7.),evidence_id='reviewed-span',section_material_loading_restraints_and_ceiling_reviewed=True),**changes))


@pytest.mark.parametrize('restraint,coefficient',[('pinned',8.),('clamped',12.)])
def test_actual_owner_inverse_matches_independent_force_and_section_integral(restraint,coefficient):
    q=request(equal_end_restraint=restraint);r=evaluate_udl_span_sizing(q,CTX);assert r['status']=='computed'
    # Direct area integration of y^2 gives I for the actual annular cross-section.
    from scipy.integrate import quad
    a=q.inside_diameter_m/2;b=q.outside_diameter_m/2
    inertia=quad(lambda radius:math.pi*radius**3,a,b)[0]
    expected=math.sqrt(coefficient*q.supplied_bending_stress_ceiling_pa*inertia/(q.downward_uniform_load_n_m*b))
    assert r['maximum_span_under_supplied_ceiling_m']==pytest.approx(expected,rel=2e-9)
    actual=r['selected_observation'];assert abs(actual['supplied_ceiling_residual_pa'])<.05
    native=actual['actual_frame_result']['combinations']['case']
    assert sum(native['nodes'][n]['reaction_force_n'][1] for n in ['left','right'])==pytest.approx(q.downward_uniform_load_n_m*expected,rel=2e-9)
    assert native['static_balance']['passed']


@pytest.mark.parametrize('stiffness',[5e4,8e5])
def test_finite_actual_spring_inverse_matches_independent_fourth_order_bvp(stiffness):
    q=request(equal_end_restraint='supplied_equal_rotational_springs',supplied_each_end_rotation_stiffness_nm_rad=stiffness)
    inertia=math.pi*(q.outside_diameter_m**4-q.inside_diameter_m**4)/64;ei=q.young_modulus_pa*inertia;p=np.polynomial.Polynomial
    def stress(length):
        particular=p([0.,0.,0.,0.,-q.downward_uniform_load_n_m/(24*ei)]);basis=[p([0.]*j+[1.]) for j in range(4)]
        rows=[lambda f:f(0),lambda f:f(length),lambda f:ei*f.deriv(2)(0)-stiffness*f.deriv()(0),lambda f:ei*f.deriv(2)(length)+stiffness*f.deriv()(length)]
        matrix=np.array([[f(b) for b in basis] for f in rows]);rhs=-np.array([f(particular) for f in rows]);coeff=np.linalg.solve(matrix,rhs)
        curve=particular+sum((a*b for a,b in zip(coeff,basis)),p([0.]))
        return max(abs(ei*curve.deriv(2)(x)) for x in [0,length/2,length])*(q.outside_diameter_m/2)/inertia
    expected=brentq(lambda length:stress(length)-q.supplied_bending_stress_ceiling_pa,*q.span_bracket_m,xtol=1e-11)
    actual=evaluate_udl_span_sizing(q,CTX)
    assert actual['status']=='computed'
    assert actual['maximum_span_under_supplied_ceiling_m']==pytest.approx(expected,rel=3e-9)


def test_explicit_zero_spring_limit_is_same_pinned_physics_without_a_fake_clamp():
    plain=evaluate_udl_span_sizing(request(),CTX)
    zero=evaluate_udl_span_sizing(request(equal_end_restraint='supplied_equal_rotational_springs',supplied_each_end_rotation_stiffness_nm_rad=0.),CTX)
    assert plain['maximum_span_under_supplied_ceiling_m']==zero['maximum_span_under_supplied_ceiling_m']


@pytest.mark.parametrize('bracket',[(.5,1.5),(6.,7.)])
def test_unbracketed_ceiling_is_not_extrapolated_or_reported_as_a_finite_span(bracket):
    r=evaluate_udl_span_sizing(request(span_bracket_m=bracket),CTX)
    assert r['status']=='bracket_does_not_bound_supported_ceiling_crossing'
    assert 'maximum_span_under_supplied_ceiling_m' not in r


def test_unknown_applicability_does_not_default_actual_support_or_allowable():
    assert evaluate_udl_span_sizing(request(section_material_loading_restraints_and_ceiling_reviewed=False),CTX)['status']=='needs_data'


@pytest.mark.parametrize('changes',[dict(inside_diameter_m=.1),dict(downward_uniform_load_n_m=0.),dict(span_bracket_m=(7.,1.)),dict(equal_end_restraint='supplied_equal_rotational_springs')])
def test_invalid_geometry_force_or_missing_stiffness_is_rejected(changes):
    with pytest.raises(ValueError):request(**changes)


@pytest.mark.parametrize('changes',[dict(asset_id='another'),dict(clock_id='another')])
def test_existing_context_identity_is_not_mixed(changes):
    with pytest.raises(ValueError):evaluate_udl_span_sizing(request(**changes),CTX)
