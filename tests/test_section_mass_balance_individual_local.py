"""Independent open-volume mass ledgers, signs and interval invariants."""

import pytest
from pydantic import ValidationError

from pipeline_integrity.mass_balance_section import (
    SectionMassBalanceRequest, evaluate_section_mass_balance,
)


def case(**updates):
    fields=dict(clock_id="elapsed",window_start_s=10.0,window_finish_s=20.0,
        inlet_masses_kg={"in":40.0},outlet_masses_kg={"out":20.0},
        initial_inventory_mass_kg=100.0,final_inventory_mass_kg=120.0,
        common_window_mass_basis_and_boundary_reviewed=True)
    fields.update(updates)
    return SectionMassBalanceRequest(**fields)


@pytest.mark.parametrize("final_mass,expected", [(120.0,0.0),(113.0,7.0),(125.0,-5.0)])
def test_independent_mass_ledger_preserves_zero_loss_and_negative_residual(final_mass,expected):
    r=evaluate_section_mass_balance(case(final_inventory_mass_kg=final_mass))
    assert r.status == "computed"
    assert r.mass_residual_kg == expected
    assert r.mean_mass_residual_rate_kg_s == expected/10.0
    assert not r.leak_presence_inferred and not r.uncertainty_computed


def test_multiple_ports_and_reversing_port_use_directional_mass_totals():
    q=case(inlet_masses_kg={"A":50.0,"reversing":8.0},
        outlet_masses_kg={"B":30.0,"reversing":5.0},
        final_inventory_mass_kg=121.0)
    r=evaluate_section_mass_balance(q)
    assert r.inlet_mass_kg == 58.0 and r.outlet_mass_kg == 35.0
    assert r.mass_residual_kg == 2.0


def test_closed_section_has_zero_directional_totals_and_explicit_storage_loss():
    r=evaluate_section_mass_balance(case(inlet_masses_kg={},outlet_masses_kg={},
        initial_inventory_mass_kg=15.0,final_inventory_mass_kg=12.0))
    assert r.inlet_mass_kg == r.outlet_mass_kg == 0.0
    assert r.mass_residual_kg == 3.0


def test_constant_inventory_offset_and_clock_translation_leave_residual_unchanged():
    a=evaluate_section_mass_balance(case(final_inventory_mass_kg=113.0))
    b=evaluate_section_mass_balance(case(initial_inventory_mass_kg=1100.0,
        final_inventory_mass_kg=1113.0,window_start_s=510.0,window_finish_s=520.0))
    assert a.mass_residual_kg == b.mass_residual_kg
    assert a.mean_mass_residual_rate_kg_s == b.mean_mass_residual_rate_kg_s


def test_partitioned_windows_have_additive_mass_and_time_weighted_rates():
    a=evaluate_section_mass_balance(case(window_start_s=0.0,window_finish_s=4.0,
        inlet_masses_kg={"in":10.0},outlet_masses_kg={"out":4.0},
        initial_inventory_mass_kg=100.0,final_inventory_mass_kg=104.0))
    b=evaluate_section_mass_balance(case(window_start_s=4.0,window_finish_s=10.0,
        inlet_masses_kg={"in":30.0},outlet_masses_kg={"out":16.0},
        initial_inventory_mass_kg=104.0,final_inventory_mass_kg=113.0))
    total=evaluate_section_mass_balance(case(window_start_s=0.0,window_finish_s=10.0,
        final_inventory_mass_kg=113.0))
    assert total.mass_residual_kg == a.mass_residual_kg+b.mass_residual_kg == 7.0
    weighted=(a.mean_mass_residual_rate_kg_s*4+b.mean_mass_residual_rate_kg_s*6)/10
    assert total.mean_mass_residual_rate_kg_s == pytest.approx(weighted)


@pytest.mark.parametrize("field", ["initial_inventory_mass_kg","final_inventory_mass_kg"])
def test_missing_inventory_is_not_invented(field):
    r=evaluate_section_mass_balance(case(**{field:None}))
    assert r.status == "needs_data" and r.mass_residual_kg is None
    assert r.mean_mass_residual_rate_kg_s is None


def test_unknown_port_and_unreviewed_boundary_withhold_totals():
    for q in (case(inlet_masses_kg={"unknown":None}),
              case(common_window_mass_basis_and_boundary_reviewed=False)):
        r=evaluate_section_mass_balance(q)
        assert r.status == "needs_data" and r.mass_residual_kg is None


@pytest.mark.parametrize("updates", [
    {"window_finish_s":10.0}, {"window_finish_s":9.0},
    {"inlet_masses_kg":{"a":-1.0}}, {"final_inventory_mass_kg":float("nan")},
    {"initial_inventory_mass_kg":True}, {"window_finish_s":float("inf")},
])
def test_invalid_modified_request_revalidates_at_execution_boundary(updates):
    with pytest.raises((ValueError,ValidationError)):
        evaluate_section_mass_balance(case().model_copy(update=updates))


def test_overflow_does_not_emit_an_infinite_residual():
    with pytest.raises(ValueError,match="finite"):
        evaluate_section_mass_balance(case(inlet_masses_kg={"a":1e308,"b":1e308}))
