"""Elastic first-yield inversion against a separate radial boundary problem."""
import math

import numpy as np
import pytest
from scipy.integrate import solve_bvp

from pipeline_integrity.contracts import EvidenceRef,Pressure,RunContext
from pipeline_integrity.elastic_first_yield import ElasticCylinderFirstYieldRequest as Request
from pipeline_integrity.elastic_first_yield import evaluate_elastic_cylinder_first_yield as evaluate
from pipeline_integrity.material_exchange import PressureSection


def make(radius_ratio=1.5,external=1e5,yield_stress=300e6,maximum=500e6,reviewed=True):
    a=.04;b=a*radius_ratio
    context=RunContext(asset_id='annular_section',scenario_id='elastic_first_yield',clock_id='static',
        origin='synthetic',evidence=(EvidenceRef(id='geometry',citation='Authored annular geometry',origin='synthetic'),
                                    EvidenceRef(id='pressure',citation='Authored pressure/yield inputs',origin='synthetic')))
    section=PressureSection(asset_id=context.asset_id,location_id='wall',clock_id=context.clock_id,time_s=0.,
        inner_radius_m=a,outer_radius_m=b,evaluation_radii_m=(a,(a+b)/2,b),internal_pressure=None,
        external_pressure=Pressure(value=external,unit='Pa',reference='absolute'),
        pressure_loaded_closed_ends=True,geometry_evidence_id='geometry',pressure_evidence_id='pressure',
        absolute_stress_tolerance_pa=1e-4)
    return Request(section=section,yield_stress_pa=yield_stress,maximum_excess_pressure_pa=maximum,
        homogeneous_small_strain_elastic_first_yield_reviewed=reviewed),context


def reference_bore_yield(radius_ratio,strength):
    x=np.linspace(1.,radius_ratio,101)
    def ode(r,y):
        gradient=(y[1]-y[0])/r
        return np.vstack((gradient,-gradient))
    def ends(inner,outer):return np.array([inner[0]+1.,outer[0]])
    initial=np.vstack((np.linspace(-1.,0.,len(x)),np.ones(len(x))))
    reference=solve_bvp(ode,ends,x,initial,tol=2e-10,max_nodes=5000)
    assert reference.success,reference.message
    radial,hoop=reference.sol(1.)
    axial=1/(radius_ratio**2-1)  # Independent closed-end pressure-cap balance.
    unit_vm=math.sqrt(((hoop-axial)**2+(axial-radial)**2+(radial-hoop)**2)/2)
    return strength/unit_vm


@pytest.mark.parametrize('ratio',[1.15,1.5,3.])
def test_first_yield_matches_radial_boundary_problem_and_occurs_at_bore(ratio):
    q,c=make(ratio)
    result=evaluate(q,c)
    assert result.status=='computed',result.issues
    expected=reference_bore_yield(ratio,q.yield_stress_pa)
    assert result.predicted_first_yield_excess_pressure_pa==pytest.approx(expected,rel=3e-8)
    assert result.controlling_radius_m==q.section.inner_radius_m
    assert result.peak_von_mises_stress_pa==pytest.approx(q.yield_stress_pa,rel=2e-9)
    assert result.plastic_collapse_or_burst_predicted is False
    assert result.pressure_section_record['buckling_plasticity_or_fitness_for_service_qualified'] is False


def test_common_hydrostatic_pressure_does_not_change_closed_end_first_yield():
    q,c=make(external=1e5);a=evaluate(q,c)
    q,c=make(external=7e6);b=evaluate(q,c)
    assert a.status==b.status=='computed'
    assert b.predicted_first_yield_excess_pressure_pa==pytest.approx(a.predicted_first_yield_excess_pressure_pa,rel=3e-12)
    assert b.predicted_internal_pressure_pa_abs-a.predicted_internal_pressure_pa_abs==pytest.approx(6.9e6,abs=1e-6)


def test_yield_strength_scales_threshold_without_changing_critical_radius():
    q,c=make(yield_stress=150e6);a=evaluate(q,c)
    q,c=make(yield_stress=300e6);b=evaluate(q,c)
    assert a.status==b.status=='computed'
    assert b.predicted_first_yield_excess_pressure_pa==pytest.approx(2*a.predicted_first_yield_excess_pressure_pa,rel=3e-12)
    assert a.controlling_radius_m==b.controlling_radius_m


def test_full_geometry_solution_converges_to_thin_wall_first_yield_limit():
    relative_errors=[]
    for thickness_over_od in (.05,.02,.005,.001):
        ratio=1/(1-2*thickness_over_od)
        thin_limit=4*thickness_over_od*300e6/math.sqrt(3.)
        q,c=make(ratio,maximum=2*thin_limit)
        result=evaluate(q,c)
        assert result.status=='computed',result.issues
        relative_errors.append(abs(result.predicted_first_yield_excess_pressure_pa/thin_limit-1))
    assert all(right<left for left,right in zip(relative_errors,relative_errors[1:]))
    assert relative_errors[0]>.04
    assert relative_errors[-1]<.002


def test_unbracketed_threshold_is_not_reported_as_a_failed_material_or_as_zero_pressure():
    q,c=make(maximum=1000.)
    result=evaluate(q,c)
    assert result.status=='threshold_not_bracketed'
    assert result.predicted_first_yield_excess_pressure_pa is None


@pytest.mark.parametrize('mode',['missing_yield','missing_external','unreviewed'])
def test_unknown_yield_or_load_or_applicability_withholds_inversion(mode):
    q,c=make()
    if mode=='missing_yield':q=q.model_copy(update={'yield_stress_pa':None})
    elif mode=='missing_external':q=q.model_copy(update={'section':q.section.model_copy(update={'external_pressure':None})})
    else:q=q.model_copy(update={'homogeneous_small_strain_elastic_first_yield_reviewed':False})
    result=evaluate(q,c)
    assert result.status=='needs_data'
    assert result.predicted_first_yield_excess_pressure_pa is None


def test_open_ends_are_outside_this_closed_end_first_yield_inversion():
    q,c=make();q=q.model_copy(update={'section':q.section.model_copy(update={'pressure_loaded_closed_ends':False})})
    assert evaluate(q,c).status=='out_of_scope'
