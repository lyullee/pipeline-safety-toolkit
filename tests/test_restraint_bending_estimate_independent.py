"""Load-resultant dimensional reference and actual beam end-condition bounds."""
import pytest
from pipeline_integrity.restraint_bending_estimate import RestraintBendingEstimateRequest,evaluate_restraint_bending_estimate

def calc(**kw):
    return evaluate_restraint_bending_estimate(RestraintBendingEstimateRequest(**(dict(uniform_transverse_load_n_m=200.,span_m=4.,section_modulus_m3=2e-5,
        supplied_moment_coefficient=.1,coefficient_basis_id='reviewed-heuristic',coefficient_and_elastic_section_basis_reviewed=True)|kw)))

@pytest.mark.parametrize('scale',[.01,1.,100.])
def test_nominal_stress_from_independent_customary_unit_coordinate(scale):
    result=calc(uniform_transverse_load_n_m=200*scale)
    # Separate lbf/in, inch, in^3 coordinate conversion.
    inch=.0254;lbf=4.4482216152605
    w=200*scale*inch/lbf;length=4/inch;z=2e-5/inch**3
    psi=w*length**2/(10*z)
    assert result.nominal_extreme_fibre_stress_pa==pytest.approx(psi*lbf/inch**2,rel=1e-14)

def test_supplied_intermediate_coefficient_is_between_exact_udl_end_moment_bounds():
    w=200;length=4
    # Exact simply-supported maximum: wL^2/8; clamped maximum absolute: wL^2/12.
    assert w*length**2/12<calc().estimated_moment_nm<w*length**2/8
    assert calc().support_stiffness_or_real_stress_qualified is False

@pytest.mark.parametrize('factor',[.5,2.,3.])
def test_span_and_section_similarity(factor):
    assert calc(span_m=4*factor).nominal_extreme_fibre_stress_pa==pytest.approx(calc().nominal_extreme_fibre_stress_pa*factor**2)
    assert calc(section_modulus_m3=2e-5*factor).nominal_extreme_fibre_stress_pa==pytest.approx(calc().nominal_extreme_fibre_stress_pa/factor)

def test_zero_load_gives_zero_moment_and_stress():
    assert calc(uniform_transverse_load_n_m=0).nominal_extreme_fibre_stress_pa==0

def test_unknown_coefficient_is_not_replaced_by_an_automatic_support_assumption():
    assert calc(supplied_moment_coefficient=None).status=='needs_data'
