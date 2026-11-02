"""Conditional supplied phase coefficient aggregation, bounds and missing-data policy."""
import pytest
from pipeline_integrity.phase_capacity_sum import PhaseCapacitySumRequest as Request
from pipeline_integrity.phase_capacity_sum import evaluate_phase_capacity_sum as evaluate


def calc(liquid=3.,vapor=5.,**changes):
    p=dict(required_liquid_cv=liquid,required_vapor_cv=vapor,common_cv_and_phase_partition_basis="authored_same_cv_basis",
        common_pressure_phase_partition_and_cv_basis_reviewed=True,additive_two_phase_capacity_approximation_reviewed=True)
    p.update(changes)
    return evaluate(Request(**p))


@pytest.mark.parametrize("liquid,vapor,total",[(0.,4.,4.),(3.,0.,3.),(2.,5.,7.)])
def test_single_phase_limits_and_known_additive_requirements(liquid,vapor,total):
    out=calc(liquid,vapor)
    assert out.approximate_combined_required_cv==total
    assert out.phase_split_or_phase_sizing_performed is False


def test_partition_scaling_is_commutative_and_cannot_be_below_either_requirement():
    a,b,c=calc(2.5,7.),calc(7.,2.5),calc(5.,14.)
    assert a.approximate_combined_required_cv==b.approximate_combined_required_cv
    assert c.approximate_combined_required_cv==2*a.approximate_combined_required_cv
    assert a.approximate_combined_required_cv>=max(2.5,7.)


def test_zero_supplied_requirements_preserve_zero_aggregate():
    assert calc(0.,0.).approximate_combined_required_cv==0


@pytest.mark.parametrize("phase",["required_liquid_cv","required_vapor_cv"])
def test_unknown_phase_requirement_is_not_zero_filled(phase):
    out=calc(**{phase:None})
    assert out.status=="needs_data" and out.approximate_combined_required_cv is None


def test_unreviewed_phase_or_coefficient_basis_is_withheld():
    assert calc(common_pressure_phase_partition_and_cv_basis_reviewed=False).status=="needs_data"


def test_negative_required_coefficient_is_rejected():
    with pytest.raises(ValueError):calc(-1.,2.)
