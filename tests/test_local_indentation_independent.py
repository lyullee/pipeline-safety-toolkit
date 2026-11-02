"""Bounded constitutive-coordinate references; not a pipe contact validation.

The manufactured reference is a shallow parabolic punch on a rigid-perfectly
plastic bearing strip. Its uniform traction, contact boundary and dissipated
displacement work are independently integrated. A real pipe is not inferred to
be this bearing strip merely because a supplied coefficient produces its law.
"""
import math
import pytest
from scipy.integrate import quad
from pipeline_integrity.local_indentation_coordinate import LocalIndentationRequest,evaluate_local_indentation_coordinate


def request(law='square_root_force_law',**changes):
    values=dict(coordinate_basis=law,pipe_wall_thickness_m=.012,supplied_yield_stress_pa=220e6,
        supplied_contact_coefficient=3.7,force_magnitude_n=4000. if law=='square_root_force_law' else None,
        impact_energy_j=7. if law=='diameter_scaled_energy_law' else None,pipe_outside_diameter_m=.6,
        maximum_reviewed_indentation_m=.02,edge_material_geometry_and_coefficient_basis_id='manufactured-bearing-only',
        selected_coordinate_and_contact_applicability_reviewed=True)
    values.update(changes);return LocalIndentationRequest(**values)


def plastic_reference(depth,radius,width,traction):
    a=math.sqrt(2*radius*depth)
    force=quad(lambda x:traction*width,-a,a,epsabs=1e-8)[0]
    work=quad(lambda x:traction*width*(depth-x*x/(2*radius)),-a,a,epsabs=1e-10)[0]
    return force,work


@pytest.mark.parametrize('radius,width',[(.07,.018),(.2,.045)])
@pytest.mark.parametrize('depth',[.0002,.001,.003])
def test_force_inverse_against_integrated_plastic_contact_traction_and_displacement_work(radius,width,depth):
    sy=220e6;t=.012;f,w=plastic_reference(depth,radius,width,sy)
    c=2*width*math.sqrt(2*radius)/t**1.5
    q=request(supplied_contact_coefficient=c,force_magnitude_n=f)
    r=evaluate_local_indentation_coordinate(q)
    assert r['indentation_coordinate_m']==pytest.approx(depth,rel=2e-14)
    assert r['selected_law_integrated_work_coordinate_j']==pytest.approx(w,rel=3e-14)
    assert not r['actual_contact_field_impact_history_or_calibration_qualified']


@pytest.mark.parametrize('radius,width',[(.07,.018),(.2,.045)])
@pytest.mark.parametrize('depth',[.0002,.001,.003])
def test_energy_inverse_and_force_against_independent_plastic_displacement_integral(radius,width,depth):
    sy=220e6;t=.012;D=.6;f,w=plastic_reference(depth,radius,width,sy)
    # The coefficient is derived from this declared manufactured geometry;
    # it is neither a pipe empirical default nor borrowed from the force law.
    c=(4/3)*width*math.sqrt(2*radius)*math.sqrt(D)/t**2
    r=evaluate_local_indentation_coordinate(request('diameter_scaled_energy_law',supplied_contact_coefficient=c,impact_energy_j=w))
    assert r['indentation_coordinate_m']==pytest.approx(depth,rel=2e-14)
    assert r['selected_law_force_coordinate_n']==pytest.approx(f,rel=2e-14)
    assert r['selected_law_integrated_work_coordinate_j']==pytest.approx(w,rel=2e-14)


@pytest.mark.parametrize('law',['square_root_force_law','diameter_scaled_energy_law'])
def test_work_derivative_matches_force_under_symmetric_refinement(law):
    radius=.1;width=.025;sy=220e6;depth=.001
    _,work=plastic_reference(depth,radius,width,sy)
    if law=='square_root_force_law':
        c=2*width*math.sqrt(2*radius)/.012**1.5
    else:c=(4/3)*width*math.sqrt(2*radius)*math.sqrt(.6)/.012**2
    def model(d):
        f,w=plastic_reference(d,radius,width,sy)
        args={'force_magnitude_n':f} if law=='square_root_force_law' else {'impact_energy_j':w}
        return evaluate_local_indentation_coordinate(request(law,supplied_contact_coefficient=c,**args))
    center=model(depth);errors=[]
    for fraction in (.1,.05,.025):
        step=depth*fraction
        dw=(model(depth+step)['selected_law_integrated_work_coordinate_j']-model(depth-step)['selected_law_integrated_work_coordinate_j'])/(2*step)
        errors.append(abs(dw-center['selected_law_force_coordinate_n']))
    assert errors[1]<errors[0]/3.8 and errors[2]<errors[1]/3.8


@pytest.mark.parametrize('law',['square_root_force_law','diameter_scaled_energy_law'])
def test_stationary_zero_limit_has_no_inferred_residual_dent(law):
    q=request(law,**({'force_magnitude_n':0.} if law=='square_root_force_law' else {'impact_energy_j':0.}))
    r=evaluate_local_indentation_coordinate(q)
    assert r['indentation_coordinate_m']==r['selected_law_force_coordinate_n']==r['selected_law_integrated_work_coordinate_j']==0.
    assert not r['actual_contact_field_impact_history_or_calibration_qualified']


@pytest.mark.parametrize('change',[{'selected_coordinate_and_contact_applicability_reviewed':False},
    {'supplied_contact_coefficient':None},{'supplied_yield_stress_pa':None},{'force_magnitude_n':None}])
def test_unknown_contact_or_material_data_stays_unknown(change):
    assert evaluate_local_indentation_coordinate(request(**change))['status']=='needs_data'


@pytest.mark.parametrize('law',['square_root_force_law','diameter_scaled_energy_law'])
def test_exact_declared_depth_limit_and_support_exit(law):
    q=request(law);r=evaluate_local_indentation_coordinate(q);d=r['indentation_coordinate_m']
    assert evaluate_local_indentation_coordinate(q.model_copy(update={'maximum_reviewed_indentation_m':d}))['status']=='computed'
    out=evaluate_local_indentation_coordinate(q.model_copy(update={'maximum_reviewed_indentation_m':d*.999}))
    assert out['status']=='out_of_scope' and 'indentation_coordinate_m' not in out


def test_energy_branch_requires_positive_bore_and_its_own_data():
    with pytest.raises(ValueError):evaluate_local_indentation_coordinate(request('diameter_scaled_energy_law',pipe_outside_diameter_m=.02))
    assert evaluate_local_indentation_coordinate(request('diameter_scaled_energy_law',pipe_outside_diameter_m=None))['status']=='needs_data'
    assert evaluate_local_indentation_coordinate(request('diameter_scaled_energy_law',impact_energy_j=None))['status']=='needs_data'


@pytest.mark.parametrize('change',[{'supplied_contact_coefficient':0.}, {'force_magnitude_n':float('nan')},
    {'pipe_wall_thickness_m':1e-300}, {'force_magnitude_n':1e300}, {'force_magnitude_n':1e-300}])
def test_invalid_overflow_and_underflow_are_not_zero_depth_states(change):
    with pytest.raises(ValueError):evaluate_local_indentation_coordinate(request(**change))


def test_branches_do_not_silently_share_a_constitutive_coefficient():
    q=request();a=evaluate_local_indentation_coordinate(q)
    b=evaluate_local_indentation_coordinate(request('diameter_scaled_energy_law',impact_energy_j=a['selected_law_integrated_work_coordinate_j']))
    # Same numeric coefficient and apparent dimensions are not the same law:
    # the energy branch has an additional thickness/diameter dependence.
    assert not math.isclose(a['indentation_coordinate_m'],b['indentation_coordinate_m'],rel_tol=.01)
    assert a['coordinate_basis']!=b['coordinate_basis']
