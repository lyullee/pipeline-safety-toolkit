"""Independent point-force equilibrium, geometry work and coordinate tests."""
import math

import numpy as np
import pytest

from pipeline_integrity.guy_projection import (
    GuyLoadDistribution, GuyProjectionRequest, evaluate_guy_projection,
    xz_force_from_magnitude_and_elevation,
)


def request(**kw):
    return GuyProjectionRequest.from_xz_elevations(id='authored-guy',coordinate_frame_id='authored-xz',
        attachment_elevation_m=20.,anchorage_elevation_m=-2.,signed_horizontal_anchor_offset_m=-30.,
        external_force_n=(500.,0.,-50.),
        coplanar_incremental_load_and_selected_reaction_direction_reviewed=True,**kw)


@pytest.mark.parametrize('height,anchor_height,anchor_x,horizontal,vertical',[
    (20.,-2.,-30.,500.,-50.),(30.,4.,-25.,700.,120.),(10.,14.,-8.,200.,-40.),
    (18.,-5.,40.,-900.,25.),(90*.3048,-10*.3048,-170*.3048,500.,-50.)])
def test_actual_vector_projection_matches_independent_two_equation_force_system(height,anchor_height,anchor_x,horizontal,vertical):
    q=GuyProjectionRequest.from_xz_elevations(id='geometry-case',coordinate_frame_id='declared-xz',
        attachment_elevation_m=height,anchorage_elevation_m=anchor_height,signed_horizontal_anchor_offset_m=anchor_x,
        external_force_n=(horizontal,0.,vertical),coplanar_incremental_load_and_selected_reaction_direction_reviewed=True)
    r=evaluate_guy_projection(q);assert r.status=='computed'
    delta=np.array([anchor_x,anchor_height-height]);unit=delta/np.linalg.norm(delta)
    tension,reaction=np.linalg.solve(np.array([[unit[0],0.],[unit[1],1.]]),[-horizontal,-vertical])
    assert r.coplanar_load_induced_pull_n==pytest.approx(tension,rel=2e-14)
    assert r.aggregate_guy_force_n==pytest.approx((tension*unit[0],0.,tension*unit[1]),abs=2e-10)
    assert r.required_base_reaction_force_n==pytest.approx((0.,0.,reaction),abs=2e-10)
    assert r.required_base_reaction_couple_nm==pytest.approx((0.,0.,0.),abs=2e-9)
    assert r.guy_inclination_magnitude_from_horizontal_rad==pytest.approx(math.atan2(abs(anchor_height-height),abs(anchor_x)))
    assert r.maximum_individual_extra_tension_n is None


def test_horizontal_virtual_work_converges_from_actual_length_changes():
    q=request();r=evaluate_guy_projection(q);a=np.array(q.attachment_point_m);b=np.array(q.anchorage_point_m)
    length=np.linalg.norm(a-b);errors=[]
    for dx in (.1,.03,.01,.003):
        new=np.linalg.norm(a+np.array([dx,0.,0.])-b)
        external_work=q.external_force_n[0]*dx
        resisting_work=-r.coplanar_load_induced_pull_n*(new-length)
        errors.append(abs(external_work+resisting_work)/abs(external_work))
    assert all(a>b>0 for a,b in zip(errors,errors[1:]))
    assert errors[-1]<3e-5


def test_required_couple_and_rotation_virtual_work_do_not_assume_pin_capacity():
    q=request().model_copy(update={'external_force_application_point_m':(3.,0.,9.),'external_applied_couple_nm':(4.,-7.,2.)})
    r=evaluate_guy_projection(q);assert r.status=='computed'
    base=np.array(q.reaction_reference_point_m);a=np.array(q.attachment_point_m);f=np.array(q.external_force_application_point_m)
    external=np.array(q.external_force_n);guy=np.array(r.aggregate_guy_force_n)
    expected=-np.cross(f-base,external)-np.cross(a-base,guy)-np.array(q.external_applied_couple_nm)
    assert r.required_base_reaction_couple_nm==pytest.approx(expected,abs=2e-10)
    rotation=np.array([.03,-.02,.01])
    work=external@np.cross(rotation,f-base)+guy@np.cross(rotation,a-base)
    work+=(np.array(q.external_applied_couple_nm)+np.array(r.required_base_reaction_couple_nm))@rotation
    assert abs(work)<1e-10
    assert r.force_balance_residual_n==pytest.approx((0.,0.,0.),abs=1e-12)
    assert r.moment_balance_residual_nm==pytest.approx((0.,0.,0.),abs=1e-10)
    assert not r.rope_or_support_capacity_or_code_acceptance_assessed


def test_incompatible_pin_retains_required_nonzero_couple():
    q=request().model_copy(update={'external_force_application_point_m':(0.,0.,9.),'reaction_model':'pin_without_couple'})
    r=evaluate_guy_projection(q);assert r.status=='out_of_scope'
    assert math.hypot(*r.required_base_reaction_couple_nm)>100.


def test_missing_force_point_does_not_manufacture_a_pin_equilibrium():
    q=request().model_copy(update={'external_force_application_point_m':None,'reaction_model':'pin_without_couple'})
    r=evaluate_guy_projection(q);assert r.status=='needs_data'
    assert r.coplanar_load_induced_pull_n is not None and r.required_base_reaction_couple_nm is None


def test_rigid_rotation_and_translation_preserve_scalar_and_transform_reactions():
    q=request().model_copy(update={'external_force_application_point_m':(2.,0.,11.),'external_applied_couple_nm':(3.,7.,-2.)})
    r=evaluate_guy_projection(q);axis=np.array([1.,2.,3.]);axis/=np.linalg.norm(axis);angle=.61
    skew=np.array([[0.,-axis[2],axis[1]],[axis[2],0.,-axis[0]],[-axis[1],axis[0],0.]])
    rotate=np.eye(3)*math.cos(angle)+(1-math.cos(angle))*np.outer(axis,axis)+math.sin(angle)*skew
    offset=np.array([123.,-56.,78.]);updates={}
    for name in ['attachment_point_m','anchorage_point_m','external_force_application_point_m','reaction_reference_point_m']:
        updates[name]=tuple(rotate@np.array(getattr(q,name))+offset)
    for name in ['external_force_n','external_applied_couple_nm','horizontal_axis_in_load_plane','upward_vertical_axis']:
        updates[name]=tuple(rotate@np.array(getattr(q,name)))
    transformed=evaluate_guy_projection(q.model_copy(update=updates));assert transformed.status=='computed'
    assert transformed.coplanar_load_induced_pull_n==pytest.approx(r.coplanar_load_induced_pull_n,rel=2e-14)
    for name in ['aggregate_guy_force_n','required_base_reaction_force_n','required_base_reaction_couple_nm']:
        assert getattr(transformed,name)==pytest.approx(rotate@np.array(getattr(r,name)),abs=3e-9)


@pytest.mark.parametrize('scale,force_scale',[(.1,.5),(3.,2.),(100.,4.)])
def test_length_and_force_similarity_have_distinct_moment_dimensions(scale,force_scale):
    q=request().model_copy(update={'external_force_application_point_m':(2.,0.,11.)});r=evaluate_guy_projection(q)
    fields=['attachment_point_m','anchorage_point_m','external_force_application_point_m','reaction_reference_point_m']
    updates={name:tuple(scale*x for x in getattr(q,name)) for name in fields}
    updates['external_force_n']=tuple(force_scale*x for x in q.external_force_n)
    other=evaluate_guy_projection(q.model_copy(update=updates))
    assert other.coplanar_load_induced_pull_n==pytest.approx(force_scale*r.coplanar_load_induced_pull_n)
    assert other.required_base_reaction_couple_nm==pytest.approx(np.array(r.required_base_reaction_couple_nm)*scale*force_scale,abs=2e-8)


def test_single_actual_coplanar_guy_and_unknown_initial_tension_are_distinct():
    distribution=GuyLoadDistribution(mode='single_coplanar_guy',guy_count=1,basis_id='single-actual-guy')
    q=request(distribution=distribution);r=evaluate_guy_projection(q)
    assert r.maximum_individual_extra_tension_n==r.coplanar_load_induced_pull_n
    assert r.conditional_individual_total_tension_n is None
    known=evaluate_guy_projection(q.model_copy(update={'distribution':distribution.model_copy(update={'supplied_initial_individual_tension_n':800.})}))
    assert known.conditional_individual_total_tension_n==pytest.approx(800+r.coplanar_load_induced_pull_n)


@pytest.mark.parametrize('factor',[.42,.68,1.27])
def test_reviewed_individual_distribution_is_data_not_an_inferred_guy_count_formula(factor):
    d=GuyLoadDistribution(mode='reviewed_factor',guy_count=7,basis_id='caller-factor',
        maximum_individual_increment_to_coplanar_pull_factor=factor,
        uniform_spacing_equal_erection_tension_and_factor_domain_reviewed=True,
        supplied_initial_individual_tension_n=300.)
    r=evaluate_guy_projection(request(distribution=d))
    assert r.maximum_individual_extra_tension_n==pytest.approx(factor*r.coplanar_load_induced_pull_n)
    assert r.conditional_individual_total_tension_n==pytest.approx(300+factor*r.coplanar_load_induced_pull_n)
    assert not r.actual_multi_guy_stiffness_or_sag_solved


@pytest.mark.parametrize('reviewed,factor',[(False,.68),(True,None)])
def test_unknown_distribution_keeps_resultant_but_not_individual_tension(reviewed,factor):
    d=GuyLoadDistribution(mode='reviewed_factor',guy_count=7,basis_id='not-yet-qualified',
        maximum_individual_increment_to_coplanar_pull_factor=factor,
        uniform_spacing_equal_erection_tension_and_factor_domain_reviewed=reviewed)
    r=evaluate_guy_projection(request(distribution=d));assert r.status=='computed'
    assert r.coplanar_load_induced_pull_n>0 and r.distribution_status=='needs_data'
    assert r.maximum_individual_extra_tension_n is None


@pytest.mark.parametrize('force,expected',[((500.,0.,0.),'out_of_scope'),((0.,0.,-50.),'underdetermined')])
def test_vertical_guy_singular_balance_is_not_zero_tension(force,expected):
    q=request().model_copy(update={'anchorage_point_m':(0.,0.,-2.),'external_force_n':force})
    r=evaluate_guy_projection(q);assert r.status==expected
    assert r.coplanar_load_induced_pull_n is None


def test_conditioning_support_is_explicit_and_customizable():
    q=request().model_copy(update={'anchorage_point_m':(-1e-10,0.,-2.)})
    assert evaluate_guy_projection(q).status=='out_of_scope'
    r=evaluate_guy_projection(q.model_copy(update={'minimum_absolute_horizontal_direction_cosine':1e-14}))
    assert r.status=='computed'
    assert r.aggregate_guy_force_n[0]==pytest.approx(-500.,rel=2e-14)


def test_reversed_load_requires_other_guy_or_a_separate_unloading_model():
    r=evaluate_guy_projection(request().model_copy(update={'external_force_n':(-500.,0.,0.)}))
    assert r.status=='out_of_scope' and r.required_signed_coplanar_increment_n<0
    assert r.aggregate_guy_force_n is None


@pytest.mark.parametrize('changes',[{'anchorage_point_m':(-30.,.01,-2.)},{'external_force_n':(500.,1.,-50.)}])
def test_non_coplanar_input_does_not_get_a_fabricated_missing_guy(changes):
    assert evaluate_guy_projection(request().model_copy(update=changes)).status=='out_of_scope'


@pytest.mark.parametrize('field',['attachment_point_m','anchorage_point_m','external_force_n'])
def test_unknown_geometry_or_force_is_not_defaulted(field):
    assert evaluate_guy_projection(request().model_copy(update={field:None})).status=='needs_data'


def test_unreviewed_model_and_unknown_moment_reference_remain_explicit():
    q=request().model_copy(update={'coplanar_incremental_load_and_selected_reaction_direction_reviewed':False})
    assert evaluate_guy_projection(q).status=='needs_data'
    r=evaluate_guy_projection(request().model_copy(update={'reaction_reference_point_m':None}))
    assert r.status=='computed' and r.required_base_reaction_couple_nm is None


@pytest.mark.parametrize('changes',[{'horizontal_axis_in_load_plane':(2.,0.,0.)},{'upward_vertical_axis':(1.,0.,0.)},
    {'external_force_n':(float('nan'),0.,0.)},{'anchorage_point_m':(0.,0.,20.)}])
def test_invalid_axes_nonfinite_or_zero_span_are_rejected(changes):
    with pytest.raises(ValueError):evaluate_guy_projection(request().model_copy(update=changes))


@pytest.mark.parametrize('magnitude,angle,sign',[(700.,-.5,1),(300.,.7,-1),(0.,.1,1),(500.,0.,1)])
def test_force_angle_has_signed_elevation_and_explicit_horizontal_direction(magnitude,angle,sign):
    force=xz_force_from_magnitude_and_elevation(magnitude,angle,sign)
    assert math.hypot(*force)==pytest.approx(magnitude)
    assert force[0]==pytest.approx(sign*magnitude*math.cos(angle))
    assert force[2]==pytest.approx(magnitude*math.sin(angle))


@pytest.mark.parametrize('magnitude,angle,sign',[(-1.,0.,1),(1.,math.pi,1),(1.,float('nan'),1),(1.,0.,True)])
def test_force_angle_invalid_domain_is_not_silently_wrapped(magnitude,angle,sign):
    with pytest.raises(ValueError):xz_force_from_magnitude_and_elevation(magnitude,angle,sign)
