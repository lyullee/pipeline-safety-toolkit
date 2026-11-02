"""Synthetic ideal-gas oracle for explicit wet/dry reference-volume conversion."""

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.gas_basis import (
    REVIEWS,
    GasBasisRequest,
    GasBasisState,
    evaluate_gas_basis,
)
from pipeline_integrity.gas_meter import GasComponent


def _request(*, source, target, source_value, quantity_kind="volume"):
    review_ids = {name: f"review-{index}" for index, name in enumerate(REVIEWS)}
    evidence_ids = {
        "composition",
        "source-state",
        "source-water",
        "source-phase",
        "source-z",
        "target-state",
        "target-water",
        "target-phase",
        "target-z",
        *review_ids.values(),
    }
    context = RunContext(
        asset_id="synthetic-gas",
        scenario_id="reference-conversion",
        clock_id="declared-snapshot",
        origin="synthetic",
        evidence=tuple(
            EvidenceRef(
                id=identity,
                citation="Authored synthetic ideal-gas reference",
                origin="synthetic",
                license_id="MIT",
                sha256="b" * 64,
            )
            for identity in sorted(evidence_ids)
        ),
    )
    request = GasBasisRequest(
        id="explicit-wet-dry-reference-conversion",
        asset_id=context.asset_id,
        clock_id=context.clock_id,
        event_id="synthetic-event",
        time_s=0.0,
        quantity_kind=quantity_kind,
        source_value=source_value,
        dry_composition_id="pure-methane-reference",
        dry_components=(GasComponent(fluid="Methane", mole_fraction=1.0),),
        composition_evidence_id="composition",
        source=source,
        target=target,
        Z_mode="supplied",
        reviews={name: True for name in REVIEWS},
        review_evidence_ids=review_ids,
        evidence_ids=tuple(sorted(evidence_ids)),
    )
    return request, context


def _state(*, identity, pressure, temperature, water, basis):
    return GasBasisState(
        id=identity,
        pressure_pa_abs=pressure,
        temperature_K=temperature,
        water_vapor_mole_fraction=water,
        volume_basis=basis,
        compressibility_Z_common_R=1.0,
        state_evidence_id=f"{identity}-state",
        water_fraction_evidence_id=f"{identity}-water",
        gas_phase_evidence_id=f"{identity}-phase",
        compressibility_evidence_id=f"{identity}-z",
    )


def test_wet_dry_conversion_preserves_dry_moles_and_matches_ideal_gas_reference():
    dry = _state(identity="source", pressure=200_000.0, temperature=300.0, water=0.0, basis="dry_gas")
    wet = _state(identity="target", pressure=100_000.0, temperature=400.0, water=0.1, basis="wet_gas")
    request, context = _request(source=dry, target=wet, source_value=9.0)
    record = evaluate_gas_basis(request, context)

    # At fixed dry-species amount, V_target/V_source follows P_s/P_t *
    # T_t/T_s * (1-y_s)/(1-y_t) for equal Z. This is exactly 80/27.
    assert record["status"] == "computed"
    assert record["values"]["target_over_source_volume_factor"] == pytest.approx(80.0 / 27.0, rel=1e-13)
    assert record["values"]["target_value"] == pytest.approx(80.0 / 3.0, rel=1e-13)
    ledger = record["molar_ledger"]
    assert ledger["conserved_dry_molar_quantity"] == pytest.approx(
        ledger["source_total_molar_quantity"], rel=1e-13
    )
    assert ledger["target_total_molar_quantity"] == pytest.approx(
        ledger["conserved_dry_molar_quantity"] / 0.9, rel=1e-13
    )
    assert ledger["net_water_to_gas_quantity"] == pytest.approx(
        ledger["target_water_vapor_quantity"], rel=1e-13
    )
    assert ledger["dry_species"][0]["source_dry_quantity"] == pytest.approx(
        ledger["dry_species"][0]["target_dry_quantity"], rel=1e-13
    )
    assert ledger["total_molar_quantity_residual"] == pytest.approx(0.0, abs=1e-12)
    assert ledger["target_volume_molar_quantity_residual"] == pytest.approx(0.0, abs=1e-12)


def test_reverse_reference_conversion_round_trips_volume_and_volume_rate():
    dry = _state(identity="source", pressure=200_000.0, temperature=300.0, water=0.0, basis="dry_gas")
    wet = _state(identity="target", pressure=100_000.0, temperature=400.0, water=0.1, basis="wet_gas")
    forward_request, forward_context = _request(source=dry, target=wet, source_value=9.0)
    forward = evaluate_gas_basis(forward_request, forward_context)
    reverse_request, reverse_context = _request(
        source=wet,
        target=dry,
        source_value=forward["values"]["target_value"],
    )
    reverse = evaluate_gas_basis(reverse_request, reverse_context)

    assert reverse["status"] == "computed"
    assert reverse["values"]["target_value"] == pytest.approx(9.0, rel=1e-13)
    assert reverse["molar_ledger"]["net_water_to_gas_quantity"] < 0.0

    flow_request, flow_context = _request(
        source=dry, target=wet, source_value=9.0, quantity_kind="volume_flow"
    )
    flow = evaluate_gas_basis(flow_request, flow_context)
    assert flow["values"]["unit"] == "m^3/s"
    assert flow["molar_ledger"]["unit"] == "mol/s"
    assert flow["values"]["target_value"] == pytest.approx(80.0 / 3.0, rel=1e-13)
