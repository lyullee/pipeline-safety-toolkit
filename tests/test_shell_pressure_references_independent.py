"""Force/unit references and a separate bracketed root for shell screening laws."""
import pytest
from scipy.optimize import brentq

from pipeline_integrity.shell_pressure_references import (
    ShortPipeHoopReferenceRequest,IntermediateShellReferenceRequest,OvalShellInteractionRequest,
    evaluate_short_pipe_hoop_reference,evaluate_intermediate_shell_reference,evaluate_oval_shell_interaction,
)


def short(**changes):
    values=dict(outside_diameter_m=.4,wall_thickness_m=.008,supplied_compressive_hoop_limit_pa=250e6,
                short_stiffened_regime_reviewed=True)
    values.update(changes)
    return evaluate_short_pipe_hoop_reference(ShortPipeHoopReferenceRequest(**values))


@pytest.mark.parametrize('diameter,thickness,limit',[(.4,.008,250e6),(.15,.004,170e6),(1.,.02,430e6)])
def test_short_pipe_reference_closes_nominal_half_section_hoop_force(diameter,thickness,limit):
    result=short(outside_diameter_m=diameter,wall_thickness_m=thickness,supplied_compressive_hoop_limit_pa=limit)
    assert result.status=='computed'
    # Unit-length half-cylinder pressure resultant balances two wall strips.
    assert result.external_over_internal_reference_pressure_pa*diameter==pytest.approx(2*thickness*limit,rel=3e-15)
    assert result.empirical_buckling_or_plastic_collapse_qualified is False


def test_short_reference_is_invariant_to_common_geometry_scale_and_linear_in_limit():
    a=short();b=short(outside_diameter_m=1.2,wall_thickness_m=.024,supplied_compressive_hoop_limit_pa=500e6)
    assert b.external_over_internal_reference_pressure_pa==pytest.approx(2*a.external_over_internal_reference_pressure_pa,rel=3e-15)


def test_short_unknown_compression_limit_is_not_replaced_with_a_material_default():
    assert short(supplied_compressive_hoop_limit_pa=None).status=='needs_data'


def intermediate(**changes):
    values=dict(outside_diameter_m=.4,wall_thickness_m=.008,length_m=2.,young_modulus_pa=200e9,
        supplied_correlation_coefficient=2.6,intermediate_length_and_coefficient_basis_reviewed=True)
    values.update(changes)
    return evaluate_intermediate_shell_reference(IntermediateShellReferenceRequest(**values))


@pytest.mark.parametrize('diameter_in,thickness_in,length_ft,modulus_psi,coefficient',
    [(16.,.25,6.,29e6,2.6),(10.,.2,5.,12e6,2.4),(30.,.4,10.,31e6,2.8)])
def test_intermediate_correlation_matches_separate_customary_unit_coordinate_expression(diameter_in,thickness_in,length_ft,modulus_psi,coefficient):
    psi=0.45359237*9.80665/.0254**2
    result=intermediate(outside_diameter_m=diameter_in*.0254,wall_thickness_m=thickness_in*.0254,
        length_m=length_ft*.3048,young_modulus_pa=modulus_psi*psi,supplied_correlation_coefficient=coefficient)
    expected_psi=coefficient*modulus_psi*(thickness_in/diameter_in)**2.5/(12*length_ft/diameter_in)
    assert result.external_over_internal_reference_pressure_pa==pytest.approx(expected_psi*psi,rel=2e-14)
    strain=coefficient/2*(thickness_in/diameter_in)**1.5/(12*length_ft/diameter_in)
    assert result.nominal_hoop_strain_magnitude==pytest.approx(strain,rel=2e-14)


def test_intermediate_geometric_and_modulus_similarity_are_explicit():
    a=intermediate();b=intermediate(outside_diameter_m=1.2,wall_thickness_m=.024,length_m=6.)
    assert b.external_over_internal_reference_pressure_pa==pytest.approx(a.external_over_internal_reference_pressure_pa,rel=2e-14)
    assert intermediate(young_modulus_pa=400e9).external_over_internal_reference_pressure_pa==pytest.approx(2*a.external_over_internal_reference_pressure_pa,rel=2e-14)
    assert intermediate(length_m=4.).external_over_internal_reference_pressure_pa==pytest.approx(.5*a.external_over_internal_reference_pressure_pa,rel=2e-14)


def test_intermediate_coefficient_is_required_not_chosen_for_a_material():
    assert intermediate(supplied_correlation_coefficient=None).status=='needs_data'


def oval(**changes):
    values=dict(elastic_reference_pressure_pa=1e6,plastic_hoop_reference_pressure_pa=2e6,reference_radius_m=.2,
        radius_basis_id='declared_nominal_radius',wall_thickness_m=.002,
        major_diameter_over_nominal_minus_one=.003,reviewed_ovality_upper_bound=.05,
        interaction_and_reference_pressure_bases_reviewed=True)
    values.update(changes)
    return evaluate_oval_shell_interaction(OvalShellInteractionRequest(**values))


@pytest.mark.parametrize('elastic,plastic,delta,radius_over_wall',[(1e6,2e6,.003,100.),(2e6,1e6,.01,50.),
    (1e6,1e6,1e-9,70.),(3e8,5e5,.02,100.)])
def test_oval_lower_root_matches_an_independent_bracketed_polynomial_root(elastic,plastic,delta,radius_over_wall):
    limit=min(elastic,plastic)
    def equation(z):
        p=z*limit
        return (p*p-(plastic+(1+6*radius_over_wall*delta)*elastic)*p+plastic*elastic)/(plastic*elastic)
    independent=brentq(equation,0.,1.,xtol=1e-14,rtol=1e-13)*limit
    result=oval(elastic_reference_pressure_pa=elastic,plastic_hoop_reference_pressure_pa=plastic,
        major_diameter_over_nominal_minus_one=delta,reference_radius_m=radius_over_wall*.002)
    assert result.status=='computed'
    assert result.external_over_internal_reference_pressure_pa==pytest.approx(independent,rel=2e-11)
    assert 0<result.external_over_internal_reference_pressure_pa<limit
    assert abs(result.normalized_equation_residual)<1e-12


@pytest.mark.parametrize('elastic,plastic',[(1e6,2e6),(2e6,1e6),(1e6,1e6)])
def test_zero_ovality_selects_the_smaller_reference_including_the_double_root(elastic,plastic):
    result=oval(elastic_reference_pressure_pa=elastic,plastic_hoop_reference_pressure_pa=plastic,
                major_diameter_over_nominal_minus_one=0.)
    assert result.external_over_internal_reference_pressure_pa==min(elastic,plastic)


def test_oval_pressure_decreases_monotonically_with_declared_ovality():
    values=[oval(major_diameter_over_nominal_minus_one=d).external_over_internal_reference_pressure_pa
            for d in (0.,.0001,.001,.01,.03)]
    assert all(b<a for a,b in zip(values,values[1:]))


def test_oval_common_pressure_scale_preserves_the_dimensionless_root():
    a=oval();b=oval(elastic_reference_pressure_pa=3.5e6,plastic_hoop_reference_pressure_pa=7e6)
    assert b.external_over_internal_reference_pressure_pa==pytest.approx(3.5*a.external_over_internal_reference_pressure_pa,rel=3e-15)


def test_oval_unknown_radius_basis_is_not_relabelled_as_an_outer_or_mean_radius():
    assert oval(radius_basis_id=None).status=='needs_data'


def test_oval_declared_support_boundary_is_preserved():
    result=oval(major_diameter_over_nominal_minus_one=.051)
    assert result.status=='out_of_scope'
    assert result.external_over_internal_reference_pressure_pa is None


def test_oval_unknown_reference_pressure_is_not_inferred_from_a_default_material():
    assert oval(elastic_reference_pressure_pa=None).status=='needs_data'
