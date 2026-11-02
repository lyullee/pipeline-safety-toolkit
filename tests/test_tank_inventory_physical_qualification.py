"""Tank inventory conservation and piecewise-linear calibration exact cases."""
import pytest
from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.tanks import (
    CalibratedTankCurve, DeclaredTankDensity, TankInventoryRequest,
    TankObservation, evaluate_tank_inventory,
)

CTX = RunContext(
    asset_id="tank-A", scenario_id="inventory-static", clock_id="elapsed-s",
    origin="synthetic",
    evidence=(
        EvidenceRef(id="geom", citation="Synthetic calibrated geometry", origin="synthetic", license_id="MIT"),
        EvidenceRef(id="obs", citation="Synthetic tank observation", origin="synthetic", license_id="MIT"),
        EvidenceRef(id="liq", citation="Synthetic liquid density", origin="synthetic", license_id="MIT"),
        EvidenceRef(id="gas", citation="Synthetic gas density", origin="synthetic", license_id="MIT"),
    ),
)
GEOM = CalibratedTankCurve(heights_m=(0.0, 1.0, 2.0), volumes_m3=(0.0, 2.0, 6.0), evidence_id="geom")
LIQ = DeclaredTankDensity(phase="liquid", fluid="water", density_kg_m3=1000.0, property_basis="declared-uniform", evidence_id="liq")
GAS = DeclaredTankDensity(phase="gas", fluid="air", density_kg_m3=2.0, property_basis="declared-uniform", evidence_id="gas")


def request(quantity, value):
    unit = "m" if quantity == "level_from_internal_bottom" else "m^3"
    return TankInventoryRequest(
        geometry=GEOM,
        observation=TankObservation(tank_id="tank-A", clock_id="elapsed-s", time_s=0.0,
            quantity=quantity, value=value, unit=unit, evidence_id="obs"),
        liquid_density=LIQ, headspace_density=GAS,
    )


def test_level_observation_closes_piecewise_volume_and_two_phase_mass_balance():
    r = evaluate_tank_inventory(request("level_from_internal_bottom", 0.5), CTX)
    v = r["values"]
    assert r["status"] == "computed"
    assert v["total_internal_volume_m3"] == pytest.approx(6.0)
    assert v["liquid_volume_m3"] == pytest.approx(1.0)  # 2 m3/m over first segment
    assert v["headspace_volume_m3"] == pytest.approx(5.0)
    assert v["liquid_mass_kg"] == pytest.approx(1000.0)
    assert v["gas_mass_kg"] == pytest.approx(10.0)
    assert v["total_fluid_mass_kg"] == pytest.approx(1010.0)
    assert v["volume_residual_m3"] <= 1e-9


def test_volume_observation_inverts_second_piecewise_segment_exactly():
    r = evaluate_tank_inventory(request("liquid_volume", 4.0), CTX)
    v = r["values"]
    assert r["status"] == "computed"
    assert v["liquid_level_m"] == pytest.approx(1.5)
    assert v["liquid_volume_m3"] == pytest.approx(4.0)
    assert v["headspace_volume_m3"] == pytest.approx(2.0)
    assert v["total_fluid_mass_kg"] == pytest.approx(4004.0)


def test_observation_outside_capacity_is_withheld_without_inventory_claim():
    r = evaluate_tank_inventory(request("liquid_volume", 6.01), CTX)
    assert r["status"] == "out_of_scope"
    assert not r["values"]

