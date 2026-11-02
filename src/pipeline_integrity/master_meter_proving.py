"""Conditional mass-factor transfer from a supplied corrected prover volume.

The pressure/temperature effects on the prover are explicit supplied factors,
never presumed unity. Prover and turbine densities retain their actual-state
bases. Pulse interpolation is delegated to the existing compact-prover API.
"""
from __future__ import annotations

import math
from typing import Literal

from .compact_prover import CompactProverRequest, WholePulses, evaluate_compact_prover
from .contracts import Contract, Name, Positive


class MasterMeterTransferObservation(Contract):
    turbine_pulses_in_transfer_window: Positive
    turbine_density_kg_m3: Positive
    turbine_state_basis_id: Name
    downstream_indicated_mass_kg: Positive
    downstream_indication_basis_id: Name
    common_mass_transfer_window_id: Name
    synchronized_window_and_negligible_intermediate_storage_reviewed: bool = False
    calibrated_master_factor_and_pulse_basis_applicable_reviewed: bool = False


class MasterMeterProvingRequest(Contract):
    master_meter_id: Name | None = None
    proving_run_id: Name | None = None
    prover_base_volume_m3: Positive | None = None
    supplied_prover_pressure_volume_factor: Positive | None = None
    supplied_prover_temperature_volume_factor: Positive | None = None
    prover_volume_correction_basis_id: Name | None = None
    prover_actual_density_kg_m3: Positive | None = None
    prover_state_basis_id: Name | None = None
    calibration_turbine_density_kg_m3: Positive | None = None
    calibration_turbine_state_basis_id: Name | None = None
    nominal_turbine_pulse_factor_per_m3: Positive | None = None
    nominal_pulse_factor_basis_id: Name | None = None
    prover_sweep_elapsed_time_s: Positive | None = None
    whole_pulse_interval_elapsed_time_s: Positive | None = None
    whole_turbine_pulses_in_interval: WholePulses | None = None
    elapsed_time_clock_basis_id: Name | None = None
    reference_volume_state_and_correction_applicability_reviewed: bool = False
    synchronized_calibration_mass_window_and_single_phase_reviewed: bool = False
    clock_scale_and_representative_frequency_reviewed: bool = False
    transfer: MasterMeterTransferObservation | None = None


class MasterMeterProvingResult(Contract):
    status: Literal["computed", "needs_data", "out_of_scope"]
    corrected_prover_volume_m3: Positive | None = None
    reference_prover_mass_kg: Positive | None = None
    reference_volume_at_turbine_state_m3: Positive | None = None
    interpolated_calibration_pulses: Positive | None = None
    indicated_calibration_turbine_volume_m3: Positive | None = None
    indicated_calibration_turbine_mass_kg: Positive | None = None
    raw_prover_over_indicated_turbine_volume_ratio: Positive | None = None
    master_true_over_indicated_mass_factor: Positive | None = None
    observed_pulse_factor_at_turbine_state_per_m3: Positive | None = None
    transfer_status: Literal["not_requested", "computed", "needs_data"] = "not_requested"
    transfer_indicated_turbine_mass_kg: Positive | None = None
    transfer_reference_mass_kg: Positive | None = None
    downstream_true_over_indicated_mass_factor: Positive | None = None
    state_density_steel_correction_or_calibration_inferred: bool = False
    measurement_uncertainty_or_field_accuracy_qualified: bool = False
    issues: tuple[str, ...] = ()


def evaluate_master_meter_proving(request: MasterMeterProvingRequest) -> MasterMeterProvingResult:
    """Observe same-window mass factors while preserving distinct local states.

    A raw ratio of prover-local volume to turbine-indicated volume is returned
    with that explicit name. When the two local densities differ, it is not
    relabelled the true/indicated volume correction at the turbine state.
    """
    if not isinstance(request, MasterMeterProvingRequest):
        raise TypeError("MasterMeterProvingRequest required")
    q = MasterMeterProvingRequest.model_validate(request.model_dump(mode="python"))
    required = [name for name in type(q).model_fields if name != "transfer" and not name.endswith("reviewed")]
    missing = ["missing " + name for name in required if getattr(q, name) is None]
    for name in ("reference_volume_state_and_correction_applicability_reviewed",
                 "synchronized_calibration_mass_window_and_single_phase_reviewed",
                 "clock_scale_and_representative_frequency_reviewed"):
        if not getattr(q, name):missing.append(name + " is required")
    if missing:
        return MasterMeterProvingResult(status="needs_data", issues=tuple(missing))
    try:
        vp = q.prover_base_volume_m3 * q.supplied_prover_pressure_volume_factor * q.supplied_prover_temperature_volume_factor
        reference_mass = vp * q.prover_actual_density_kg_m3
        vt_reference = reference_mass / q.calibration_turbine_density_kg_m3
        if not all(math.isfinite(v) and v > 0 for v in (vp, reference_mass, vt_reference)):raise ArithmeticError
        compact = evaluate_compact_prover(CompactProverRequest(
            meter_id=q.master_meter_id, proving_run_id=q.proving_run_id,
            corrected_reference_volume_m3=vt_reference, meter_volume_state_basis_id=q.calibration_turbine_state_basis_id,
            reference_volume_correction_basis_id=q.prover_volume_correction_basis_id,
            prover_sweep_elapsed_time_s=q.prover_sweep_elapsed_time_s,
            whole_pulse_interval_elapsed_time_s=q.whole_pulse_interval_elapsed_time_s,
            whole_meter_pulses_in_interval=q.whole_turbine_pulses_in_interval,
            elapsed_time_clock_basis_id=q.elapsed_time_clock_basis_id,
            volume_at_declared_meter_state_reviewed=True, clock_scale_and_interval_pairing_reviewed=True,
            representative_stationary_pulse_frequency_reviewed=True,
            nominal_indicated_pulse_factor_per_m3=q.nominal_turbine_pulse_factor_per_m3,
            nominal_factor_basis_id=q.nominal_pulse_factor_basis_id))
        if compact.status != "computed" or compact.dimensionless_factor_status != "computed":raise ArithmeticError
        indicated_volume = compact.interpolated_pulses_during_sweep / q.nominal_turbine_pulse_factor_per_m3
        indicated_mass = indicated_volume * q.calibration_turbine_density_kg_m3
        raw_volume_ratio = vp / indicated_volume
        if not all(math.isfinite(v) and v > 0 for v in (indicated_volume, indicated_mass, raw_volume_ratio)):raise ArithmeticError
        values = dict(status="computed", corrected_prover_volume_m3=vp, reference_prover_mass_kg=reference_mass,
            reference_volume_at_turbine_state_m3=vt_reference, interpolated_calibration_pulses=compact.interpolated_pulses_during_sweep,
            indicated_calibration_turbine_volume_m3=indicated_volume, indicated_calibration_turbine_mass_kg=indicated_mass,
            raw_prover_over_indicated_turbine_volume_ratio=raw_volume_ratio,
            master_true_over_indicated_mass_factor=compact.true_over_indicated_volume_multiplier,
            observed_pulse_factor_at_turbine_state_per_m3=compact.observed_pulse_factor_per_m3)
        if q.transfer is not None:
            t = q.transfer
            if not t.synchronized_window_and_negligible_intermediate_storage_reviewed or not t.calibrated_master_factor_and_pulse_basis_applicable_reviewed:
                values.update(transfer_status="needs_data", issues=("mass-transfer window/storage and master-factor applicability require review",))
            else:
                transfer_indicated_mass = t.turbine_pulses_in_transfer_window / q.nominal_turbine_pulse_factor_per_m3 * t.turbine_density_kg_m3
                transfer_reference_mass = transfer_indicated_mass * compact.true_over_indicated_volume_multiplier
                downstream_factor = transfer_reference_mass / t.downstream_indicated_mass_kg
                if not all(math.isfinite(v) and v > 0 for v in (transfer_indicated_mass, transfer_reference_mass, downstream_factor)):raise ArithmeticError
                values.update(transfer_status="computed", transfer_indicated_turbine_mass_kg=transfer_indicated_mass,
                    transfer_reference_mass_kg=transfer_reference_mass, downstream_true_over_indicated_mass_factor=downstream_factor)
        return MasterMeterProvingResult(**values)
    except (ArithmeticError, ValueError):
        return MasterMeterProvingResult(status="out_of_scope", issues=("mass/volume/pulse factor leaves finite positive arithmetic support",))
