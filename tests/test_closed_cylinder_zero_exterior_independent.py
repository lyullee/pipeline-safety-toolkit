"""Closed cylinder stresses against a separately solved radial boundary problem."""
import numpy as np
import pytest
from scipy.integrate import solve_bvp

from pipeline_integrity.contracts import EvidenceRef, Pressure, RunContext
from pipeline_integrity.material_exchange import PressureSection, evaluate_pressure_section


@pytest.mark.parametrize('radius_ratio', [1.15, 1.5, 3.0])
def test_closed_internal_pressure_profile_matches_boundary_problem_and_end_force(radius_ratio):
    # Normalize radius by the bore and stresses by the applied pressure.
    # Radial equilibrium and cylindrical elastic compatibility form an
    # independent first-order boundary problem; do not call the owner's formula.
    mesh = np.linspace(1., radius_ratio, 101)
    def ode(radius, stress):
        gradient = (stress[1]-stress[0])/radius
        return np.vstack((gradient, -gradient))
    def boundary(inner, outer):
        return np.array([inner[0]+1., outer[0]])
    initial = np.vstack((np.linspace(-1., 0., len(mesh)), np.ones(len(mesh))))
    reference = solve_bvp(ode, boundary, mesh, initial, tol=2e-10, max_nodes=5000)
    assert reference.success, reference.message
    coordinates = np.linspace(1., radius_ratio, 11)
    a, pressure, common_pressure = 0.04, 3.7e6, 1e5
    b = a*radius_ratio
    context = RunContext(asset_id='closed_cylinder', scenario_id='radial_boundary',
        clock_id='static', origin='synthetic', evidence=(
            EvidenceRef(id='geometry',citation='Authored annular test geometry',origin='synthetic'),
            EvidenceRef(id='pressure',citation='Authored internal pressure test',origin='synthetic'),))
    request = PressureSection(asset_id=context.asset_id,location_id='section',clock_id=context.clock_id,
        time_s=0.,inner_radius_m=a,outer_radius_m=b,
        evaluation_radii_m=tuple(a*coordinates),
        internal_pressure=Pressure(value=pressure+common_pressure,unit='Pa',reference='absolute'),
        external_pressure=Pressure(value=common_pressure,unit='Pa',reference='absolute'),
        pressure_loaded_closed_ends=True,geometry_evidence_id='geometry',pressure_evidence_id='pressure',
        absolute_stress_tolerance_pa=1e-5)
    result = evaluate_pressure_section(request,context)
    assert result['status']=='computed'
    # Equal pressure on bore, exterior and closed ends is a uniform compressive
    # stress. Remove that common stress to compare the zero-exterior-reference
    # boundary problem while keeping both process absolute pressures positive.
    predicted = [{key:value+common_pressure for key,value in r['native']['payload']['values'].items()
                  if key in ('radial_stress_Pa','hoop_stress_Pa','axial_stress_Pa')}
                 for r in result['material_records']]
    radial, hoop = reference.sol(coordinates)
    assert [v['radial_stress_Pa']/pressure for v in predicted] == pytest.approx(radial,rel=2e-8,abs=2e-9)
    assert [v['hoop_stress_Pa']/pressure for v in predicted] == pytest.approx(hoop,rel=2e-8,abs=2e-9)
    # The metal end area carries the pressure acting on the bore cap area.
    for row in predicted:
        assert row['axial_stress_Pa']*(b*b-a*a) == pytest.approx(pressure*a*a,rel=2e-13)
    assert predicted[0]['radial_stress_Pa'] == pytest.approx(-pressure,abs=1e-5)
    assert predicted[-1]['radial_stress_Pa'] == pytest.approx(0.,abs=1e-5)
    assert result['buckling_plasticity_or_fitness_for_service_qualified'] is False
