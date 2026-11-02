"""Owner-provider batch transport contracts; queued for Nurion."""

from copy import deepcopy

import pytest

from pipeline_integrity.batch_transport import (
    BatchComposition,
    BatchInletStep,
    BatchTransportRequest,
    evaluate_batch_transport,
    verify_batch_transport_record,
)
from pipeline_integrity.contracts import EvidenceRef, RunContext, digest


def boundary():
    return RunContext(
        asset_id="line",
        scenario_id="batch",
        clock_id="elapsed",
        origin="synthetic",
        evidence=tuple(
            EvidenceRef(id=key, citation="Authored boundary fixture", origin="synthetic")
            for key in ("geometry", "boundary", "properties", "thermal")
        ),
    )


def request(**changes):
    composition = BatchComposition(fraction_by_component={"A": 1.0, "B": 0.0})
    fields = dict(
        id="batch",
        asset_id="line",
        location_id="route",
        clock_id="elapsed",
        route_length_m=100.0,
        cross_section_area_m2=1.0,
        duration_s=10.0,
        component_ids=("A", "B"),
        initial_composition=composition,
        inlet_steps=(
            BatchInletStep(
                time_s=0.0,
                volume_flow_m3_s=1.0,
                composition=BatchComposition(fraction_by_component={"A": 0.0, "B": 1.0}),
                temperature_K=300.0,
                pressure_Pa_abs=200000.0,
            ),
        ),
        property_basis_id="authored",
        dispersion_model_id="owner-model",
        geometry_evidence_id="geometry",
        boundary_evidence_id="boundary",
        property_evidence_id="properties",
        absolute_mass_balance_tolerance_kg=1e-9,
        relative_mass_balance_tolerance=1e-9,
        maximum_mass_numerical_adjustment_kg=1e-6,
        thermal_mode="prescribed_temperature",
    )
    return BatchTransportRequest(**(fields | changes))


class Provider:
    identity = {"id": "stub", "version": "1", "source_digest": "a" * 64}

    def __init__(self, change=None):
        self.change = change

    def evaluate(self, specification, context):
        result = {
            "request_digest": digest(specification),
            "field_layout": "time_major_position_minor",
            "mass_ledger_sign_convention": (
                "initial + inlet - outlet - final + numerical_adjustment = residual"
            ),
            "time_s": [0.0, 10.0],
            "position_m": [0.0, 100.0],
            "fraction_by_component": {"A": [1.0, 0.5, 0.2, 0.0], "B": [0.0, 0.5, 0.8, 1.0]},
            "density_kg_m3": [800.0, 810.0, 820.0, 830.0],
            "dynamic_viscosity_Pa_s": [0.01, 0.011, 0.012, 0.013],
            "temperature_K": [300.0, 299.0, 298.0, 297.0],
            "pressure_Pa_abs": [200000.0, 190000.0, 180000.0, 170000.0],
            "mass_ledger_kg": {
                "initial": 100.0,
                "inlet": 10.0,
                "outlet": 20.0,
                "final": 90.0,
                "numerical_adjustment": 0.0,
            },
            "component_mass_ledger_kg": {
                "A": {
                    "initial": 100.0,
                    "inlet": 0.0,
                    "outlet": 20.0,
                    "final": 80.0,
                    "numerical_adjustment": 0.0,
                },
                "B": {
                    "initial": 0.0,
                    "inlet": 10.0,
                    "outlet": 0.0,
                    "final": 10.0,
                    "numerical_adjustment": 0.0,
                },
            },
        }
        if specification.thermal_mode == "energy_coupled":
            result["energy_ledger_sign_convention"] = (
                "initial + inlet - outlet - final + heat_transfer + shaft_work + "
                "numerical_adjustment = residual"
            )
            result["energy_ledger_J"] = {
                "initial": 1000.0,
                "inlet": 100.0,
                "outlet": 200.0,
                "final": 910.0,
                "heat_transfer": 10.0,
                "shaft_work": 0.0,
                "numerical_adjustment": 0.0,
            }
        if self.change == "balance":
            result["mass_ledger_kg"]["final"] = 80.0
        elif self.change == "fraction":
            result["fraction_by_component"]["B"][2] = 0.7
        elif self.change == "grid":
            result["position_m"][-1] = 99.0
        elif self.change == "property":
            result["density_kg_m3"][0] = 0.0
        elif self.change == "component_balance":
            result["component_mass_ledger_kg"]["A"]["final"] = 70.0
        elif self.change == "component_sum":
            result["component_mass_ledger_kg"]["A"]["initial"] = 90.0
        elif self.change == "energy_balance":
            result["energy_ledger_J"]["final"] = 900.0
        elif self.change == "missing_energy":
            result.pop("energy_ledger_J")
        elif self.change == "hidden_mass_adjustment":
            result["mass_ledger_kg"]["initial"] = 90.0
            result["mass_ledger_kg"]["numerical_adjustment"] = 10.0
            result["component_mass_ledger_kg"]["A"]["initial"] = 90.0
            result["component_mass_ledger_kg"]["A"]["numerical_adjustment"] = 10.0
        elif self.change == "hidden_energy_adjustment":
            result["energy_ledger_J"]["initial"] = 900.0
            result["energy_ledger_J"]["numerical_adjustment"] = 100.0
        return result


def test_owner_batch_result_preserves_composition_and_mass_ledger_and_replays():
    q, c, provider = request(), boundary(), Provider()
    record = evaluate_batch_transport(q, c, provider=provider)
    assert record["status"] == "computed" and record["mass_balance_residual_kg"] == 0
    assert record["component_mass_balance_residual_kg"] == {"A": 0.0, "B": 0.0}
    assert record["component_ledger_sum_matches_total"]
    assert not record["constant_density_or_water_quality_assumed"]
    assert verify_batch_transport_record(record, request=q, context=c, provider=provider)


def test_mass_imbalance_is_withheld_not_relabelled_as_transport_success():
    record = evaluate_batch_transport(request(), boundary(), provider=Provider("balance"))
    assert record["status"] == "withheld" and record["mass_balance_residual_kg"] == 10.0


def test_energy_coupled_result_requires_and_checks_a_separate_energy_ledger():
    q = request(
        thermal_mode="energy_coupled",
        thermal_model_id="owner-thermal",
        thermal_evidence_id="thermal",
        absolute_energy_balance_tolerance_J=1e-9,
        relative_energy_balance_tolerance=1e-9,
        maximum_energy_numerical_adjustment_J=1e-6,
    )
    record = evaluate_batch_transport(q, boundary(), provider=Provider())
    assert record["status"] == "computed"
    assert record["energy_balance_residual_J"] == 0
    assert record["energy_balance_within_tolerance"]
    withheld = evaluate_batch_transport(q, boundary(), provider=Provider("energy_balance"))
    assert withheld["status"] == "withheld"
    assert not withheld["energy_balance_within_tolerance"]
    with pytest.raises(ValueError, match="energy ledger"):
        evaluate_batch_transport(q, boundary(), provider=Provider("missing_energy"))


def test_closed_residual_cannot_hide_excessive_numerical_adjustment():
    mass = evaluate_batch_transport(request(), boundary(), provider=Provider("hidden_mass_adjustment"))
    assert mass["mass_balance_within_tolerance"]
    assert not mass["numerical_adjustments_within_limits"]
    assert mass["status"] == "withheld"
    q = request(
        thermal_mode="energy_coupled",
        thermal_model_id="owner-thermal",
        thermal_evidence_id="thermal",
        absolute_energy_balance_tolerance_J=1e-9,
        relative_energy_balance_tolerance=1e-9,
        maximum_energy_numerical_adjustment_J=1e-6,
    )
    energy = evaluate_batch_transport(q, boundary(), provider=Provider("hidden_energy_adjustment"))
    assert energy["energy_balance_within_tolerance"]
    assert not energy["energy_numerical_adjustment_within_limit"]
    assert energy["status"] == "withheld"


@pytest.mark.parametrize("change", ["component_balance", "component_sum"])
def test_component_mass_loss_cannot_hide_behind_a_closed_total_ledger(change):
    record = evaluate_batch_transport(request(), boundary(), provider=Provider(change))
    assert record["status"] == "withheld"
    assert not (
        record["component_mass_balances_within_tolerance"] and record["component_ledger_sum_matches_total"]
    )


@pytest.mark.parametrize("change", ["fraction", "grid", "property"])
def test_invalid_owner_fields_are_rejected(change):
    with pytest.raises(ValueError):
        evaluate_batch_transport(request(), boundary(), provider=Provider(change))


def test_request_and_replay_bind_property_geometry_clock_and_provider():
    q, c, provider = request(), boundary(), Provider()
    record = evaluate_batch_transport(q, c, provider=provider)
    changed = deepcopy(record)
    changed["provider"]["version"] = "2"
    changed["record_digest"] = digest({k: v for k, v in changed.items() if k != "record_digest"})
    with pytest.raises(ValueError, match="replay"):
        verify_batch_transport_record(changed, request=q, context=c, provider=provider)
    with pytest.raises(ValueError, match="evidence"):
        evaluate_batch_transport(q, c.model_copy(update={"evidence": ()}), provider=provider)
