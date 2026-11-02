"""Independent energy, displaced-mass and dimensional-coordinate checks."""
import math

import pytest

from pipeline_integrity.mechanical_screening_quantities import (
    KineticPressureScaleRequest,MassDampingIndexRequest,EquivalentMassScaledDistanceRequest,
    evaluate_kinetic_pressure_scale,evaluate_mass_damping_index,evaluate_equivalent_mass_scaled_distance,
)


def kinetic(rho=999.,speed=2.,**changes):
    return evaluate_kinetic_pressure_scale(KineticPressureScaleRequest(**dict(
        mass_density_kg_m3=rho,speed_change_magnitude_m_s=speed,uniform_density_kinetic_scale_reviewed=True,
        **changes)))


@pytest.mark.parametrize('rho,speed',[(1.2,.3),(999.,2.),(850.,7.),(1000.,0.)])
def test_kinetic_scale_matches_removed_kinetic_energy_per_volume(rho,speed):
    volume=3.7;mass=rho*volume
    result=kinetic(rho,speed)
    assert result.kinetic_pressure_scale_pa==pytest.approx((mass*speed*speed/2)/volume,rel=3e-15)
    assert result.transient_pressure_history_inferred is False


def test_kinetic_scale_has_linear_density_and_quadratic_speed_similarity():
    base=kinetic().kinetic_pressure_scale_pa
    assert kinetic(rho=1998.).kinetic_pressure_scale_pa==pytest.approx(2*base)
    assert kinetic(speed=4.).kinetic_pressure_scale_pa==pytest.approx(4*base)


def test_kinetic_unknown_density_is_withheld():
    assert kinetic(rho=None).status=='needs_data'


def damping(**changes):
    values=dict(span_mass_kg_m=40.,supplied_air_damping_parameter=.04,damping_parameter_definition='supplied_beta_direct',
        surrounding_density_kg_m3=1000.,outside_diameter_m=.2,parameter_and_mass_basis_reviewed=True)
    values.update(changes)
    return evaluate_mass_damping_index(MassDampingIndexRequest(**values))


@pytest.mark.parametrize('mass,density,diameter,beta',[(40.,1000.,.2,.04),(120.,1025.,.5,.07),(2.,1.2,.1,.01)])
def test_mass_damping_index_matches_circular_displaced_mass_ratio(mass,density,diameter,beta):
    length=3.7;displaced_mass=density*math.pi*(diameter/2)**2*length
    normalized_mass=mass*length/displaced_mass
    result=damping(span_mass_kg_m=mass,surrounding_density_kg_m3=density,
                   outside_diameter_m=diameter,supplied_air_damping_parameter=beta)
    assert result.mass_damping_index==pytest.approx(math.pi/2*beta*normalized_mass,rel=3e-15)
    assert result.above_supplied_threshold is None


@pytest.mark.parametrize('changes',[dict(span_mass_kg_m=0.),dict(supplied_air_damping_parameter=0.)])
def test_mass_damping_zero_limits_are_explicit(changes):
    assert damping(**changes).mass_damping_index==0


def test_mass_damping_geometric_mass_similarity_preserves_index():
    a=damping();scale=3.5
    b=damping(span_mass_kg_m=40.*scale**2,outside_diameter_m=.2*scale)
    assert b.mass_damping_index==pytest.approx(a.mass_damping_index,rel=3e-15)


@pytest.mark.parametrize('factor,above',[(.5,True),(1.,False),(2.,False)])
def test_mass_damping_threshold_is_only_a_strict_supplied_comparison(factor,above):
    index=damping().mass_damping_index
    result=damping(supplied_comparison_threshold=index*factor)
    assert result.above_supplied_threshold is above
    assert result.vibration_amplitude_or_stability_qualified is False


def test_mass_damping_unknown_definition_does_not_assume_a_ratio_or_log_decrement():
    assert damping(damping_parameter_definition=None).status=='needs_data'


def scaled(**changes):
    values=dict(input_basis='supplied_equivalent_mass',distance_m=10.,equivalent_mass_kg=8.,
        reference_energy_basis_id='authored_reference_energy_basis',equivalence_and_unit_basis_reviewed=True)
    values.update(changes)
    return evaluate_equivalent_mass_scaled_distance(EquivalentMassScaledDistanceRequest(**values))


@pytest.mark.parametrize('mass,distance',[(1.,4.),(.125,2.),(8.,10.)])
def test_scaled_distance_satisfies_the_cubic_mass_distance_identity(mass,distance):
    result=scaled(equivalent_mass_kg=mass,distance_m=distance)
    assert result.scaled_distance_m_kg_neg_third**3*mass==pytest.approx(distance**3,rel=3e-15)
    assert result.blast_damage_or_overpressure_inferred is False


@pytest.mark.parametrize('energy,specific',[(8e6,4e6),(15e6,5e6),(1e4,2e6)])
def test_energy_and_direct_equivalent_mass_bases_agree(energy,specific):
    energy_result=scaled(input_basis='available_energy',available_energy_j=energy,reference_specific_energy_j_kg=specific)
    mass_result=scaled(equivalent_mass_kg=energy/specific)
    assert energy_result.equivalent_mass_kg==energy/specific
    assert energy_result.scaled_distance_m_kg_neg_third==pytest.approx(mass_result.scaled_distance_m_kg_neg_third,rel=3e-15)


def test_geometric_cube_root_similarity_preserves_scaled_coordinate():
    a=scaled();scale=3.5
    b=scaled(distance_m=10.*scale,equivalent_mass_kg=8.*scale**3)
    assert b.scaled_distance_m_kg_neg_third==pytest.approx(a.scaled_distance_m_kg_neg_third,rel=3e-15)


def test_foot_pound_mass_scaled_coordinate_converts_with_both_length_and_mass_units():
    feet,pounds_mass=30.,17.
    raw=feet/math.cbrt(pounds_mass)
    result=scaled(distance_m=feet*.3048,equivalent_mass_kg=pounds_mass*.45359237)
    assert result.scaled_distance_m_kg_neg_third==pytest.approx(raw*.3048/math.cbrt(.45359237),rel=3e-15)


def test_zero_equivalent_energy_is_not_mapped_to_an_infinite_or_zero_scaled_distance():
    result=scaled(input_basis='available_energy',available_energy_j=0.,reference_specific_energy_j_kg=4e6)
    assert result.status=='out_of_scope'
    assert result.equivalent_mass_kg==0
    assert result.scaled_distance_m_kg_neg_third is None


def test_unknown_reference_specific_energy_is_not_replaced_by_an_explosive_default():
    result=scaled(input_basis='available_energy',available_energy_j=1e6,reference_specific_energy_j_kg=None)
    assert result.status=='needs_data'
