from copy import deepcopy

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.pump_transient import (
    PumpCoastdownRequest,
    evaluate_pump_coastdown,
    verify_pump_coastdown_record,
)


def context():
    return RunContext(
        asset_id="pump-station",
        scenario_id="trip",
        clock_id="elapsed",
        origin="synthetic",
        evidence=tuple(
            EvidenceRef(id=value, citation=value, origin="synthetic")
            for value in ("hydraulic", "rotor", "drive")
        ),
    )


def request(**changes):
    fields = dict(
        id="trip-1",
        asset_id="pump-station",
        pump_id="P-1",
        clock_id="elapsed",
        duration_s=2.0,
        density_kg_m3=1000.0,
        rotor_inertia_kg_m2=2.0,
        initial_angular_speed_rad_s=10.0,
        pump_model_id="owner-pump",
        drive_model_id="owner-trip",
        hydraulic_evidence_id="hydraulic",
        rotor_evidence_id="rotor",
        drive_evidence_id="drive",
        absolute_power_consistency_tolerance_W=1e-9,
        relative_power_consistency_tolerance=1e-9,
        absolute_energy_balance_tolerance_J=1e-9,
        relative_energy_balance_tolerance=1e-9,
        maximum_energy_numerical_adjustment_J=1e-6,
    )
    return PumpCoastdownRequest(**(fields | changes))


class Provider:
    identity = {"id": "owner", "version": "1", "source_digest": "a" * 64}

    def __init__(self, change=None):
        self.change = change

    def evaluate(self, specification, boundary):
        flow = [0.001, 0.0005, 0.0]
        head = [5.0, 2.0, 0.0]
        power = [
            specification.density_kg_m3 * specification.gravity_m_s2 * q * h
            for q, h in zip(flow, head, strict=True)
        ]
        hydraulic_energy = 0.5 * (power[0] + power[1]) + 0.5 * (power[1] + power[2])
        dissipated_energy = 100.0 - hydraulic_energy
        dissipated_power = [dissipated_energy / 2.0] * 3
        result = {
            "request_digest": digest(specification),
            "energy_ledger_sign_convention": "initial_rotor + drive - hydraulic - dissipated - final_rotor + numerical_adjustment = residual",
            "time_s": [0.0, 1.0, 2.0],
            "angular_speed_rad_s": [10.0, 5.0, 0.0],
            "volume_flow_m3_s": flow,
            "head_gain_m": head,
            "shaft_power_W": [0.0, 0.0, 0.0],
            "hydraulic_power_W": power,
            "dissipated_power_W": dissipated_power,
            "energy_ledger_J": {
                "initial_rotor": 100.0,
                "drive": 0.0,
                "hydraulic": hydraulic_energy,
                "dissipated": dissipated_energy,
                "final_rotor": 0.0,
                "numerical_adjustment": 0.0,
            },
            "converged": True,
        }
        if self.change == "power":
            result["hydraulic_power_W"][0] += 1.0
        elif self.change == "rotor":
            result["energy_ledger_J"]["final_rotor"] = 1.0
        elif self.change == "balance":
            result["energy_ledger_J"]["hydraulic"] -= 10.0
        elif self.change == "adjustment":
            result["energy_ledger_J"]["initial_rotor"] = 90.0
            result["energy_ledger_J"]["numerical_adjustment"] = 10.0
        elif self.change == "clock":
            result["time_s"][-1] = 1.9
        return result


def test_owner_pump_response_preserves_boundary_and_replays_without_copying_dynamics():
    q, c, provider = request(), context(), Provider()
    record = evaluate_pump_coastdown(q, c, provider=provider)
    assert record["status"] == "computed"
    assert record["moc_boundary"]["head_gain_m"] == [5.0, 2.0, 0.0]
    assert not record["direct_moc_coupling_implemented"]
    assert not record["pump_dynamics_reimplemented"]
    assert verify_pump_coastdown_record(record, request=q, context=c, provider=provider)


@pytest.mark.parametrize("change", ["power", "rotor", "balance", "adjustment"])
def test_power_rotor_energy_or_adjustment_failure_withholds_response(change):
    record = evaluate_pump_coastdown(request(), context(), provider=Provider(change))
    assert record["status"] == "withheld"


def test_invalid_owner_clock_is_rejected_instead_of_repaired():
    with pytest.raises(ValueError, match="clock"):
        evaluate_pump_coastdown(request(), context(), provider=Provider("clock"))


def test_missing_context_evidence_rejects_owner_call():
    with pytest.raises(ValueError, match="evidence"):
        evaluate_pump_coastdown(request(), context().model_copy(update={"evidence": ()}), provider=Provider())


def test_resealed_provider_change_fails_replay():
    q, c, provider = request(), context(), Provider()
    record = evaluate_pump_coastdown(q, c, provider=provider)
    changed = deepcopy(record)
    changed["provider"]["version"] = "2"
    changed["record_digest"] = digest(
        {key: value for key, value in changed.items() if key != "record_digest"}
    )
    with pytest.raises(ValueError, match="replay"):
        verify_pump_coastdown_record(changed, request=q, context=c, provider=provider)
