"""Analytic verification for caller-supplied cylindrical deposit inventory."""

import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.deposition_inventory import (
    DepositInventoryRequest,
    DepositWindow,
    evaluate_deposit_inventory,
    prescribed_mass_step,
)


def _case(*, deposition, removal, duration, cleaning=0.0):
    q = DepositInventoryRequest(
        id="deposit-analytic",
        asset_id="pipe-1",
        clock_id="elapsed-1",
        start_time_s=5.0,
        clean_inner_diameter_m=0.2,
        length_m=10.0,
        deposit_density_kg_m3=1000.0,
        initial_thickness_m=0.001,
        minimum_open_diameter_m=0.18,
        windows=(DepositWindow(
            id="window-1", duration_s=duration,
            deposition_kg_s=deposition, removal_kg_s=removal,
            cleaning_fraction_at_end=cleaning, evidence_id="rates-1",
        ),),
        evidence_ids=("geometry-1", "rates-1"),
    )
    c = RunContext(
        asset_id=q.asset_id, scenario_id="authored-cylinder",
        clock_id=q.clock_id, origin="synthetic",
        evidence=(EvidenceRef(id="geometry-1", citation="Authored geometry", origin="synthetic"),
                  EvidenceRef(id="rates-1", citation="Authored rates", origin="synthetic")),
    )
    return q, c


def test_exact_mass_balance_cylindrical_geometry_threshold_and_cleaning():
    q, c = _case(deposition=0.6, removal=0.0, duration=100.0, cleaning=0.5)
    result = evaluate_deposit_inventory(q, c)
    factor = math.pi * q.length_m * q.deposit_density_kg_m3
    radius = q.clean_inner_diameter_m / 2
    initial = factor * (radius**2 - (radius-q.initial_thickness_m)**2)
    capacity = factor * (radius**2 - (q.minimum_open_diameter_m/2)**2)
    expected_before_clean = initial + 60.0
    expected_after_clean = expected_before_clean * 0.5
    assert result["status"] == "computed"
    assert result["states"][0]["deposit_mass_kg"] == pytest.approx(initial, abs=1e-12)
    before = result["states"][1]
    after = result["states"][2]
    assert before["deposit_mass_kg"] == pytest.approx(expected_before_clean, abs=1e-12)
    assert before["open_diameter_m"] == pytest.approx(
        2*math.sqrt(radius**2-expected_before_clean/factor), abs=1e-12)
    assert before["flow_area_m2"] == pytest.approx(math.pi*before["open_diameter_m"]**2/4, abs=1e-12)
    assert before["below_minimum_opening"] is True
    assert after["deposit_mass_kg"] == pytest.approx(expected_after_clean, abs=1e-12)
    assert result["events"] == [
        {"kind": "minimum_opening_reached", "window_id": "window-1",
         "time_s": pytest.approx(q.start_time_s+(capacity-initial)/0.6)},
        {"kind": "minimum_opening_restored_by_cleaning", "window_id": "window-1",
         "time_s": q.start_time_s+100.0},
    ]
    assert result["deposited_mass_kg"] == pytest.approx(60.0)
    assert result["removed_mass_kg"] == 0.0
    assert result["cleaned_mass_kg"] == pytest.approx(expected_before_clean*0.5)
    assert result["mass_residual_kg"] == pytest.approx(0.0, abs=1e-12)


def test_exact_removal_recovery_event_and_no_silent_clipping():
    mass, deposited, removed = prescribed_mass_step(2.0, 0.01, 0.015, 100.0)
    assert deposited == pytest.approx(1.0)
    assert removed == pytest.approx(1.5)
    assert mass == pytest.approx(1.5)
    with pytest.raises(ValueError, match="exceeds available"):
        prescribed_mass_step(0.2, 0.0, 0.01, 30.0)


def test_restoration_crossing_has_exact_linear_event_time():
    q, c = _case(deposition=0.0, removal=0.1, duration=600.0)
    q = q.model_copy(update={"initial_thickness_m": 0.02})
    # A thick initial layer begins below the minimum opening; removal restores it.
    result = evaluate_deposit_inventory(q, c)
    factor = math.pi*q.length_m*q.deposit_density_kg_m3
    radius = q.clean_inner_diameter_m/2
    initial = factor*(radius**2-(radius-q.initial_thickness_m)**2)
    capacity = factor*(radius**2-(q.minimum_open_diameter_m/2)**2)
    assert result["events"][0]["kind"] == "minimum_opening_already_reached"
    assert result["events"][0]["time_s"] == q.start_time_s
    assert len(result["events"]) == 2
    assert result["events"][1]["kind"] == "minimum_opening_restored"
    assert result["events"][1]["time_s"] == pytest.approx(
        q.start_time_s+(capacity-initial)/(-0.1), abs=1e-12)
    assert result["mass_residual_kg"] == pytest.approx(0.0, abs=1e-12)
