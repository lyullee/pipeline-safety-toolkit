"""Individual elastic-ring instability against an independent modal determinant."""
import math

import numpy as np
import pytest
from scipy.optimize import brentq

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.elastic_shell_ring import (
    ElasticShellRingRequest, elastic_shell_ring_backend_metadata, evaluate_elastic_shell_ring,
)


def request(thickness, points=64, harmonics=3):
    effective = 2.1e9 / (1-0.3**2)
    classical = effective*thickness**3/4
    return ElasticShellRingRequest(
        id='individual_ring_pressure',asset_id='ring',clock_id='seconds',cross_section_id='section',
        coordinate_frame_id='xy',mean_reference_centerline_radius_m=1.0,wall_thickness_m=thickness,
        axial_strip_width_m=0.2,young_modulus_Pa=2.1e9,poisson_ratio=0.3,
        axial_constraint='plane_strain_axially_uniform_shell',
        external_minus_internal_pressure_Pa=(0.0,0.5*classical,1.1*classical),
        maximum_cartesian_harmonic=harmonics,quadrature_points=points,
        minimum_pressure_step_Pa=classical*1e-10,pressure_event_tolerance_Pa=classical*2e-7,
        absolute_equilibrium_force_tolerance_N=1e-7,relative_equilibrium_force_tolerance=1e-10,
        positive_tangent_stiffness_tolerance_N_m=1e-7,
        absolute_quadrature_stiffness_tolerance_N_m=1e-6,
        relative_quadrature_stiffness_tolerance=1e-6,
        native_backend_pins=elastic_shell_ring_backend_metadata(),
        geometry_evidence_id='authored',material_evidence_id='authored',pressure_evidence_id='authored',
        scope_review_evidence_id='authored',reference_ovality_is_stress_free_geometry_reviewed=True,
        homogeneous_linear_elastic_thin_unshearable_ring_reviewed=True,
        axial_constraint_and_omitted_finite_length_modes_reviewed=True,
        conservative_uniform_differential_pressure_reviewed=True,
    )


def independent_extensible_pressure(q):
    # For radial/tangential n=2 perturbations, independently eliminate the
    # tangential amplitude from the 2x2 second-variation determinant. The
    # circular prestretch is 1/(1+alpha), alpha=p*R/(E_effective*t).
    ratio=q.wall_thickness_m/q.mean_reference_centerline_radius_m
    gamma=ratio**2/12
    alpha=brentq(lambda a:a*((1+a)**-2+gamma)-3*gamma,0.,6*gamma,
        xtol=1e-15,rtol=1e-14)
    effective=q.young_modulus_Pa/(1-q.poisson_ratio**2)
    return alpha*effective*q.wall_thickness_m/q.mean_reference_centerline_radius_m


def classical_pressure(q):
    effective=q.young_modulus_Pa/(1-q.poisson_ratio**2)
    return effective*q.wall_thickness_m**3/(4*q.mean_reference_centerline_radius_m**3)


@pytest.fixture(scope='module')
def solved():
    context=RunContext(asset_id='ring',scenario_id='individual_elastic_pressure',clock_id='seconds',origin='synthetic',
        evidence=(EvidenceRef(id='authored',citation='Authored elastic circular-ring benchmark',origin='synthetic',sha256='a'*64),))
    results={}
    for thickness in (0.04,0.02,0.01):
        for points in (64,128):
            q=request(thickness,points)
            result=evaluate_elastic_shell_ring(q,context)
            results[(thickness,points)]=(q,result)
    return results


@pytest.mark.parametrize('thickness',[0.04,0.02,0.01])
@pytest.mark.parametrize('points',[64,128])
def test_actual_instability_event_matches_independent_extensible_modal_reference(solved,thickness,points):
    q,r=solved[(thickness,points)]
    assert r['status']=='computed_diagnostic',r['failure']
    event=r['stability_event']
    assert event is not None
    lo,hi=event['pressure_bracket_Pa']
    expected=independent_extensible_pressure(q)
    assert hi-lo<=q.pressure_event_tolerance_Pa
    # The reported event uses a small positive stiffness floor rather than
    # mathematical zero; pressure-width accuracy covers that declared floor.
    assert lo-q.pressure_event_tolerance_Pa<=expected<=hi+q.pressure_event_tolerance_Pa
    assert (lo+hi)/2==pytest.approx(expected,rel=8e-7)
    assert event['stable_equilibrium']['positive_tangent'] is True
    assert event['marginal_or_unstable_equilibrium']['positive_tangent'] is False
    assert r['finite_length_axial_shell_modes_solved'] is False
    assert r['plasticity_or_wall_contact_solved'] is False


def test_thickness_refinement_approaches_classical_long_cylinder_limit(solved):
    errors=[]
    for thickness in (0.04,0.02,0.01):
        q,r=solved[(thickness,128)]
        assert r['status']=='computed_diagnostic',r['failure']
        midpoint=sum(r['stability_event']['pressure_bracket_Pa'])/2
        error=midpoint/classical_pressure(q)-1
        assert 0<error<0.6*thickness**2
        errors.append(error)
    assert errors[0]>errors[1]>errors[2]
    assert 3.6<errors[0]/errors[1]<4.4
    assert 3.6<errors[1]/errors[2]<4.4


@pytest.mark.parametrize('thickness',[0.04,0.02,0.01])
def test_angular_quadrature_refinement_retains_critical_pressure_and_energy_closure(solved,thickness):
    a,b=(solved[(thickness,p)] for p in (64,128))
    qa,ra=a
    qb,rb=b
    assert ra['status']==rb['status']=='computed_diagnostic'
    pa=sum(ra['stability_event']['pressure_bracket_Pa'])/2
    pb=sum(rb['stability_event']['pressure_bracket_Pa'])/2
    assert pa==pytest.approx(pb,rel=5e-7)
    for q,r in (a,b):
        for row in r['states']:
            assert row['force_residual_N']<=row['force_tolerance_N']
            allowed=q.absolute_pressure_work_tolerance_J+q.relative_pressure_work_tolerance*max(
                row['axial_energy_J']+row['bending_energy_J'],abs(row['cumulative_external_pressure_work_J']))
            assert abs(row['cumulative_pressure_work_energy_residual_J'])<=allowed


def test_independent_reference_has_expected_strip_width_independence():
    q=request(0.02)
    wider=q.model_copy(update={'axial_strip_width_m':0.8})
    assert independent_extensible_pressure(q)==independent_extensible_pressure(wider)
