"""Independent unit-normalized head rule and dimensional similarities."""
import math
import pytest
from pipeline_integrity.impeller_head_scale import ImpellerHeadScaleRequest as Request
from pipeline_integrity.impeller_head_scale import evaluate_impeller_head_scale as evaluate


def calc(**changes):
    args=dict(impeller_diameter_m=.25,rotational_frequency_hz=25.,head_coefficient=.5,
              identical_stages=2,coefficient_and_identical_stage_basis_reviewed=True)
    args.update(changes)
    return evaluate(Request(**args))


@pytest.mark.parametrize("diam_in,rpm,ku,stages",[(8.,900.,.97,1),(13.,1500.,1.08,3),(6.5,2100.,1.13,2)])
def test_head_coefficient_parameterization_matches_independent_us_design_coordinate(diam_in,rpm,ku,stages):
    g=9.80665
    psi=.3048*g*(60/(.0254*1840*ku*math.pi))**2
    result=calc(impeller_diameter_m=diam_in*.0254,rotational_frequency_hz=rpm/60,
                head_coefficient=psi,identical_stages=stages)
    expected_ft=(diam_in*rpm/(1840*ku))**2*stages
    assert result.estimated_head_m/.3048==pytest.approx(expected_ft,rel=4e-15)


@pytest.mark.parametrize("field,factor,power",[("impeller_diameter_m",1.7,2),("rotational_frequency_hz",1.7,2),
    ("identical_stages",3,1)])
def test_declared_geometry_speed_and_stage_similarities(field,factor,power):
    base=dict(impeller_diameter_m=.25,rotational_frequency_hz=25.,identical_stages=2)
    assert calc(**{field:base[field]*factor}).estimated_head_m/calc().estimated_head_m==pytest.approx(factor**power)


def test_gravity_conversion_preserves_specific_work_scale():
    a,b=calc(gravity_m_s2=9.80665),calc(gravity_m_s2=8.)
    assert a.estimated_head_m*9.80665==pytest.approx(b.estimated_head_m*8.,rel=2e-15)


def test_unknown_coefficient_or_unreviewed_basis_does_not_claim_a_pump_head():
    assert calc(head_coefficient=None).estimated_head_m is None
    assert calc(coefficient_and_identical_stage_basis_reviewed=False).status=="needs_data"


def test_nonpositive_stage_count_is_rejected():
    with pytest.raises(ValueError):calc(identical_stages=0)
