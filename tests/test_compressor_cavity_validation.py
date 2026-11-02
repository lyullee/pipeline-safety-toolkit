"""Native compressor energy and positive-gas cavity verification references."""

import math

import pytest

from pipeline_integrity.cavity_moc import CavityMOCRequest, evaluate_cavity_moc
from pipeline_integrity.compressor_dynamics import (
    CompressorDynamicWindow,
    CompressorDynamicsRequest,
    evaluate_compressor_dynamics,
)
from pipeline_integrity.compressor_train import (
    REVIEWS,
    CompressorMap,
    CompressorStage,
    CompressorTrainRequest,
    IntercoolerBoundary,
    boundary_metadata,
    native_backend_metadata,
)
from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.transient import History, LiquidNode, LiquidPipe, LiquidTransient


def context(metadata=None):
    return RunContext(asset_id="unit", scenario_id="reference", clock_id="seconds", origin="simulation",
        evidence=(EvidenceRef(id="review", citation="Authored compressor and discrete-gas reference",
            origin="synthetic", sha256="b" * 64),), metadata=metadata or {})


def cavity_case(exponent, *, withdrawal=False, step=0.01):
    duration = 0.1
    sections = round(100.0 / (1000.0 * step))
    initial_volumes = [1e-3 * (math.pi * 0.1**2 / 4) * (100.0 / sections)] * sections
    initial_volumes[-1] /= 2.0
    outlet_history = (History(time_s=(0.0, 0.05, 0.1), values=(0.0, 1e-5, 1e-5))
        if withdrawal else History(time_s=(0.0,), values=(0.0,)))
    liquid = LiquidTransient(nodes=(
        LiquidNode(id="reservoir", elevation_m=0.0, initial_head_m=10.0, boundary="head",
            history=History(time_s=(0.0,), values=(10.0,))),
        LiquidNode(id="outlet", elevation_m=0.0, initial_head_m=10.0, boundary="withdrawal", history=outlet_history)),
        pipes=(LiquidPipe(id="pipe", start_node="reservoir", end_node="outlet", length_m=100.0,
            diameter_m=0.1, wave_speed_m_s=1000.0, darcy_friction_factor=0.0, initial_volume_flow_m3_s=0.0),),
        duration_s=duration, requested_timestep_s=step, density_kg_m3=1000.0,
        ambient_pressure_pa_abs=101325.0, vapour_pressure_pa_abs=2000.0)
    return CavityMOCRequest(id="cavity", asset_id="unit", clock_id="seconds", liquid=liquid,
        initial_cavity_volume_m3=tuple(initial_volumes), initial_gas_temperature_K=(300.0,) * sections,
        polytropic_exponent=(exponent,) * sections, noncondensable_specific_gas_constant_J_kg_K=287.0,
        minimum_cavity_volume_m3=1e-10, maximum_supported_absolute_pressure_Pa=1e6,
        absolute_cavity_volume_tolerance_m3=1e-12, absolute_global_liquid_volume_tolerance_m3=1e-10,
        parameter_evidence_id="review", gas_inventory_evidence_id="review", closure_review_evidence_id="review",
        supplied_fixed_gas_locations_amount_and_polytropic_response_reviewed=True,
        constant_liquid_vapor_pressure_and_negligible_vapor_mass_reviewed=True,
        small_void_fraction_acoustic_liquid_and_omitted_slip_reviewed=True)


@pytest.mark.parametrize("exponent", (1.0, 1.4))
def test_positive_gas_cavity_preserves_independent_hydrostatic_and_ideal_gas_equilibrium(exponent):
    request = cavity_case(exponent)
    record = evaluate_cavity_moc(request, context())
    assert record["status"] == "computed_diagnostic", record.get("failure")
    pressure = 101325.0 + 1000.0 * 9.80665 * 10.0
    initial_masses = [(pressure - 2000.0) * volume / (287.0 * 300.0) for volume in request.initial_cavity_volume_m3]
    assert record["initial_noncondensable_gas_mass_kg"] == pytest.approx(initial_masses, rel=1e-12)
    for row in record["states"]:
        assert row["absolute_pressure_Pa"] == pytest.approx([pressure] * (len(initial_masses) + 1), abs=1e-6)
        assert row["cavity_volume_m3"] == pytest.approx(request.initial_cavity_volume_m3, rel=1e-10)
        assert row["gas_temperature_K"] == pytest.approx([300.0] * len(initial_masses), abs=1e-8)
        assert max(abs(value) for value in row["flow_from_left_m3_s"]) < 1e-12
        assert max(abs(value) for value in row["flow_to_right_m3_s"]) < 1e-12


@pytest.mark.parametrize("exponent", (1.0, 1.4))
def test_withdrawal_cavity_polytropic_work_fixed_gas_mass_and_liquid_volume_budget(exponent):
    request = cavity_case(exponent, withdrawal=True)
    record = evaluate_cavity_moc(request, context())
    assert record["status"] == "computed_diagnostic", record.get("failure")
    assert record["states"][-1]["time_s"] == pytest.approx(0.1)
    initial_partial_pressure = 101325.0 + 1000.0 * 9.80665 * 10.0 - 2000.0
    initial_masses = [initial_partial_pressure * volume / (287.0 * 300.0)
        for volume in request.initial_cavity_volume_m3]
    for row in record["states"]:
        for index, (volume, partial, temperature, work) in enumerate(zip(row["cavity_volume_m3"],
                row["gas_partial_pressure_Pa"], row["gas_temperature_K"], row["gas_partial_pressure_expansion_work_J"])):
            initial_volume = request.initial_cavity_volume_m3[index]
            assert volume > 0.0 and partial > 0.0
            assert partial * volume**exponent == pytest.approx(initial_partial_pressure * initial_volume**exponent, rel=1e-10)
            assert partial * volume / (287.0 * temperature) == pytest.approx(initial_masses[index], rel=1e-10)
            assert row["absolute_pressure_Pa"][index + 1] == pytest.approx(partial + 2000.0, abs=1e-6)
            expected_work = (initial_partial_pressure * initial_volume * math.log(volume / initial_volume)
                if exponent == 1.0 else (partial * volume - initial_partial_pressure * initial_volume) / (1.0 - exponent))
            assert work == pytest.approx(expected_work, rel=1e-8, abs=1e-11)
        # Audit stored liquid compression separately from gas-volume growth;
        # no gas-law work is relabelled as a solved total thermal-energy budget.
        residual = (row["linearized_liquid_pressure_storage_change_m3"]
            - math.fsum(v - v0 for v, v0 in zip(row["cavity_volume_m3"], request.initial_cavity_volume_m3))
            - row["integrated_external_liquid_inflow_m3"])
        assert abs(residual) < 1.1e-10
    assert record["states"][-1]["cavity_volume_m3"][-1] > request.initial_cavity_volume_m3[-1]
    assert record["states"][-1]["gas_partial_pressure_Pa"][-1] < initial_partial_pressure


def compressor_case(step, recycle):
    performance_map = CompressorMap(id="flat_map", fluid="Hydrogen", coordinate_convention="actual",
        speed_rad_s=(1000.0, 2000.0), mass_flow_kg_s=(0.005, 0.02),
        pressure_ratio=((1.2, 1.2), (1.2, 1.2)), isentropic_efficiency=((0.8, 0.8), (0.8, 0.8)),
        surge_flow_kg_s=(0.006, 0.006), choke_flow_kg_s=(0.019, 0.019),
        inlet_pressure_range_pa_abs=(50000.0, 200000.0), inlet_temperature_range_K=(250.0, 500.0), evidence_id="review")
    stage = CompressorStage(id="stage", shaft_id="common", inlet_port_id="inlet", compressor_outlet_port_id="outlet",
        outlet_port_id="outlet", speed_rad_s=1500.0, performance_map=performance_map,
        mechanical_efficiency=1.0, drive_efficiency=1.0,
        minimum_surge_flow_margin_fraction=0.05, minimum_choke_flow_margin_fraction=0.02,
        intercooler=IntercoolerBoundary(mode="none", pressure_drop_pa=0.0, evidence_id="review"), evidence_id="review")
    base = CompressorTrainRequest(train_id="train", asset_id="unit", clock_id="seconds", event_id="reference",
        time_s=0.0, clock_offset_s=0.0, source_port_id="inlet", target_port_id="outlet", fluid="Hydrogen",
        # The native owner requires its exact fluid-specific CoolProp reference.
        energy_reference="CoolProp-HEOS:Hydrogen:native", inlet_total_pressure_pa_abs=1e5, inlet_total_temperature_K=300.0,
        mass_flow_kg_s=0.01, stages=(stage,), evidence_ids=("review",), reviews=dict.fromkeys(REVIEWS, True),
        review_evidence_ids=dict.fromkeys(REVIEWS, "review"), owner_pins=native_backend_metadata())
    request = CompressorDynamicsRequest(id="compressor", base=base, shaft_id="common", rotor_inertia_kg_m2=1.0,
        initial_speed_rad_s=1500.0,
        windows=(CompressorDynamicWindow(id="coast", duration_s=0.1, fresh_mass_flow_kg_s=0.01 - recycle,
            recycle_mass_flow_kg_s=recycle, inlet_total_pressure_pa_abs=1e5, inlet_total_temperature_K=300.0,
            drive_torque_N_m=0.0, evidence_id="review"),), rotor_evidence_id="review",
        quasi_steady_map_and_common_shaft_reviewed=True,
        external_inlet_conditioning_and_prescribed_recycle_reviewed=True,
        maximum_step_s=step, sample_step_s=0.05, relative_tolerance=1e-9,
        absolute_energy_residual_tolerance_J=0.001, maximum_native_calls=1000)
    return request, context({"compressor_train_boundary": boundary_metadata(base)})


@pytest.mark.parametrize("recycle", (0.0, 0.005))
def test_native_flat_map_compressor_coastdown_matches_independent_constant_power_and_recycle_energy(recycle):
    import CoolProp.CoolProp as CP

    # Constant ratio/efficiency/total mass produce constant gas and shaft power.
    eos = CP.AbstractState("HEOS", "Hydrogen")
    eos.update(CP.PT_INPUTS, 1e5, 300.0)
    inlet_h, inlet_s = eos.hmass(), eos.smass()
    eos.update(CP.PSmass_INPUTS, 1.2e5, inlet_s)
    enthalpy_rise = (eos.hmass() - inlet_h) / 0.8
    power = 0.01 * enthalpy_rise
    endpoints = []
    for step in (0.05, 0.025):
        request, boundary = compressor_case(step, recycle)
        record = evaluate_compressor_dynamics(request, boundary)
        assert record["status"] == "computed_diagnostic"
        for row in record["states"]:
            elapsed = row["time_s"]
            expected_energy = 0.5 * 1500.0**2 - power * elapsed
            assert row["rotor_energy_J"] == pytest.approx(expected_energy, abs=0.001)
            assert row["speed_rad_s"] == pytest.approx(math.sqrt(2 * expected_energy), abs=1e-6)
            work = row["cumulative_energy_J"]
            assert work["shaft_load"] == pytest.approx(power * elapsed, abs=0.001)
            assert work["net_enthalpy"] == pytest.approx((0.01 - recycle) * enthalpy_rise * elapsed, abs=0.001)
            assert work["recycle_conditioning"] == pytest.approx(recycle * enthalpy_rise * elapsed, abs=0.001)
            assert work["drive"] == work["drag"] == work["train_cooling"] == work["mechanical_loss"] == 0.0
        mass = record["mass_ledger_kg"]
        assert mass["through_compressor"] == pytest.approx(0.001)
        assert mass["net_product"] == pytest.approx((0.01 - recycle) * 0.1)
        assert mass["accumulated_gas"] == 0.0
        endpoints.append(record["states"][-1]["speed_rad_s"])
    assert endpoints[0] == pytest.approx(endpoints[1], abs=1e-6)
