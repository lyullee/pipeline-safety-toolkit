"""Uniaxial elastic work and circumferential release-length references only."""
import math
import pytest
from scipy.integrate import quad
from pipeline_integrity.local_crack_arrest_energy import LocalCrackArrestEnergyRequest as Request,evaluate_local_crack_arrest_energy as evaluate

def req(stress=100e6,E=200e9,R=.3):
    return Request(tensile_opening_stress_pa=stress,pipe_reference_radius_m=R,elastic_modulus_pa=E,supplied_impact_energy_j=100.,
        impact_specimen_net_section_area_m2=.0001,supplied_impact_to_crack_area_energy_factor=.7,
        opening_stress_geometry_and_energy_transfer_basis_id='authored-elastic-work-no-charpy-calibration',elastic_driving_coordinate_and_impact_energy_transfer_reviewed=True)

@pytest.mark.parametrize('stress',[0.,50e6,200e6])
@pytest.mark.parametrize('E,R',[(70e9,.1),(200e9,.3),(10e9,1.)])
def test_independent_loading_work_times_release_length(stress,E,R):
    q=req(stress,E,R);density=quad(lambda strain:E*strain,0,stress/E)[0]
    reference=quad(lambda angle:density*R,0,2*math.pi)[0]
    r=evaluate(q)
    # Two analytic linear-work integrals differ by floating operation order.
    # Budget in representable floating spacings rather than a dimensionful
    # fixed absolute threshold that becomes sub-ULP at large energies.
    assert abs(r['driving_energy_coordinate_j_m2']-reference)<=32*math.ulp(reference)
    assert r['actual_dynamic_arrest_or_material_transfer_qualified'] is False

def test_resistance_input_is_a_separate_energy_area_transfer():
    q=req();r=evaluate(q)
    s=evaluate(q.model_copy(update={'supplied_impact_energy_j':200.,'impact_specimen_net_section_area_m2':.0002}))
    assert s['supplied_resistance_coordinate_j_m2']==pytest.approx(r['supplied_resistance_coordinate_j_m2'])

def test_unknown_transfer_factor_does_not_select_historical_default():
    assert evaluate(req().model_copy(update={'supplied_impact_to_crack_area_energy_factor':None}))['status']=='needs_data'

def test_equality_is_not_a_strictly_lower_driving_energy():
    q=req();driving=evaluate(q)['driving_energy_coordinate_j_m2']
    # Authored resistance on the same energy/area basis, not measured Charpy.
    q=q.model_copy(update={'supplied_impact_energy_j':driving,'impact_specimen_net_section_area_m2':1.,'supplied_impact_to_crack_area_energy_factor':1.})
    assert evaluate(q)['strictly_below_supplied_local_resistance'] is False
