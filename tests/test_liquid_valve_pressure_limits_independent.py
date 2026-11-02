"""Independent FL/FF pressure budgets and declared incipient-index boundaries."""
import math

import pytest

from pipeline_integrity.liquid_valve_pressure_limits import LiquidValvePressureLimitsRequest as Request
from pipeline_integrity.liquid_valve_pressure_limits import evaluate_liquid_valve_pressure_limits as evaluate

BASE=dict(inception_criterion='nominal_recovery_proxy',inlet_pressure_pa_abs=8e5,outlet_pressure_pa_abs=6e5,
    vapour_pressure_pa_abs=2e4,critical_pressure_pa_abs=22e6,pressure_recovery_factor=.8,
    pressure_state_and_factor_bases_reviewed=True)


def calc(**changes):return evaluate(Request(**(BASE|changes)))


@pytest.mark.parametrize('recovery',[.7,.85,.95])
@pytest.mark.parametrize('drop_ratio',[.9,1.1])
def test_nominal_inception_proxy_and_separate_choke_budget(recovery,drop_ratio):
    p1,pv,pc=BASE['inlet_pressure_pa_abs'],BASE['vapour_pressure_pa_abs'],BASE['critical_pressure_pa_abs']
    inception=recovery**2*(p1-pv)
    ff=.96-.28*math.sqrt(pv/pc)
    choking=recovery**2*(p1-ff*pv)
    drop=inception*drop_ratio
    result=calc(pressure_recovery_factor=recovery,outlet_pressure_pa_abs=p1-drop)
    assert result.status=='computed',result.issues
    assert result.incipient_pressure_drop_limit_pa==pytest.approx(inception,rel=2e-15)
    assert result.choked_pressure_drop_limit_pa==pytest.approx(choking,rel=2e-15)
    assert result.cavitation_index==pytest.approx((p1-pv)/drop,rel=2e-15)
    assert result.at_or_above_declared_inception_limit is (drop>=inception)
    assert result.at_or_above_choked_limit is (drop>=choking)


def test_supplied_index_has_an_explicit_different_basis_from_the_recovery_factor():
    result=calc(inception_criterion='supplied_inception_index',supplied_inception_index=4.,
                inception_index_basis_id='authored_incipient_index')
    assert result.status=='computed'
    assert result.incipient_pressure_drop_limit_pa==(8e5-2e4)/4
    assert result.at_or_above_declared_inception_limit is True
    assert result.cavitation_damage_or_phase_fraction_inferred is False


def test_unknown_inception_threshold_preserves_known_choke_limit_without_filling_a_default():
    result=calc(inception_criterion='supplied_inception_index')
    assert result.status=='needs_data'
    assert result.incipient_pressure_drop_limit_pa is None
    assert result.at_or_above_declared_inception_limit is None
    assert result.choked_pressure_drop_limit_pa>0


def test_zero_pressure_drop_keeps_the_index_undefined_and_both_finite_limit_comparisons_false():
    result=calc(outlet_pressure_pa_abs=BASE['inlet_pressure_pa_abs'])
    assert result.status=='computed'
    assert result.cavitation_index is None
    assert result.at_or_above_declared_inception_limit is False
    assert result.at_or_above_choked_limit is False


def test_below_vapour_outlet_is_only_a_pressure_condition_not_an_inferred_phase_fraction():
    result=calc(outlet_pressure_pa_abs=1e4)
    assert result.outlet_below_vapour_pressure is True
    assert result.cavitation_damage_or_phase_fraction_inferred is False


def test_inconsistent_supplied_inception_after_choke_is_not_silently_accepted():
    result=calc(inception_criterion='supplied_inception_index',supplied_inception_index=.5,
                inception_index_basis_id='inconsistent_authored_index')
    assert result.status=='needs_data'
    assert result.at_or_above_declared_inception_limit is None


def test_invalid_subcritical_pressure_state_is_rejected():
    with pytest.raises(ValueError):calc(critical_pressure_pa_abs=1e4)
