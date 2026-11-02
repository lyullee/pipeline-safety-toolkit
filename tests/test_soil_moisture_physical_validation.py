"""Nurion validation for the supplied-moisture/P1 soil coupling boundary.

Fixtures are authored numerical inputs. They are not field observations or soil
property claims. Numerical cases are intentionally run on Nurion only.
"""

from pathlib import Path
import runpy

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.soil_fem import SoilFEMRequest
from pipeline_integrity.soil_moisture import (
    SoilMoistureRequest,
    SoilMoistureStage,
    SoilMoistureTable,
    evaluate_soil_moisture,
    soil_moisture_properties,
    verify_soil_moisture_record,
)
from pipeline_integrity.pump_moc_coupling import LiquidPump
from pipeline_integrity.pump_moc_exchange import PumpMOCRequest, evaluate_pump_moc, verify_pump_moc_record
from pipeline_integrity.rotating_dynamics import SignedRotatingMap
from pipeline_integrity.transient import History, LiquidNode, LiquidPipe, LiquidTransient


MAKE = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/soil_fem.py"))["make_case"]


def request_case(*, refinement=1, maximum_step_s=0.05, second_stage_energy_J=0.0,
                 second_stage_water_fraction=0.5, mode="uniform", stage_count=2):
    fem, context = MAKE(mode=mode, refinement=refinement, reviewed=True,
                        maximum_step_s=maximum_step_s)
    table = SoilMoistureTable(
        id="authored_piecewise_soil_properties",
        volumetric_water_fraction=(0.0, 0.5, 1.0),
        conductivity_W_m_K=(1.0, 3.0, 5.0),
        volumetric_heat_capacity_J_m3_K=(10.0, 30.0, 50.0),
        temperature_support_K=(280.0, 340.0),
        sensible_energy_reference_temperature_K=300.0,
        evidence_id="authored",
        uniform_moisture_and_piecewise_linear_properties_reviewed=True,
    )
    second_fem = SoilFEMRequest.model_validate(
        fem.model_copy(update={"clock_offset_s": fem.clock_offset_s + fem.time_s[-1]}).model_dump(mode="python")
    )
    stages = [SoilMoistureStage(id="state_1", volumetric_water_fraction=0.5,
                                fem=fem, evidence_id="authored")]
    if stage_count == 2:
        stages.append(SoilMoistureStage(
            id="state_2", volumetric_water_fraction=second_stage_water_fraction,
            fem=second_fem, moisture_energy_exchange_at_start_J=second_stage_energy_J,
            evidence_id="authored"))
    request = SoilMoistureRequest(
        id="authored_constant_temperature",
        asset_id=fem.asset_id,
        clock_id=fem.clock_id,
        property_table=table,
        stages=tuple(stages),
        absolute_parameter_energy_tolerance_J=1e-7,
        supplied_moisture_and_energy_exchange_mapping_reviewed=True,
    )
    return request, context


def test_piecewise_moisture_properties_match_independent_linear_interpolation():
    table = request_case()[0].property_table

    expected = {
        0.0: (1.0, 10.0),
        0.25: (2.0, 20.0),
        0.5: (3.0, 30.0),
        0.75: (4.0, 40.0),
        1.0: (5.0, 50.0),
    }
    for water_fraction, (conductivity, heat_capacity) in expected.items():
        result = soil_moisture_properties(table, water_fraction)
        assert result["conductivity_W_m_K"] == pytest.approx(conductivity, abs=1e-14)
        assert result["volumetric_heat_capacity_J_m3_K"] == pytest.approx(heat_capacity, abs=1e-14)
    with pytest.raises(ValueError, match="outside the supplied property table"):
        soil_moisture_properties(table, 1.0001)


@pytest.mark.parametrize("refinement,maximum_step_s", [(1, 0.05), (2, 0.05), (2, 0.025)])
def test_constant_temperature_is_exact_under_mesh_and_timestep_refinement(refinement, maximum_step_s):
    request, context = request_case(refinement=refinement, maximum_step_s=maximum_step_s)
    result = evaluate_soil_moisture(request, context)

    assert result["status"] == "computed_diagnostic", result["failure"]
    assert result["infiltration_or_moisture_transport_solved"] is False
    assert result["latent_energy_inferred"] is False
    assert len(result["stages"]) == 2
    assert result["supplied_moisture_energy_J"] == pytest.approx(0.0, abs=1e-12)
    assert result["thermal_boundary_energy_J"] == pytest.approx(0.0, abs=1e-10)
    # Sensible energy is integrated from P1 nodal offsets before element-volume
    # aggregation, so the reference-temperature oracle remains tight while
    # allowing the native solve's residual floating-point temperature error.
    assert result["initial_sensible_energy_J"] == pytest.approx(0.0, abs=1e-9)
    assert result["final_sensible_energy_J"] == pytest.approx(0.0, abs=1e-9)
    assert result["energy_residual_J"] == pytest.approx(0.0, abs=1e-9)

    for stage in result["stages"]:
        owner = stage["owner_record"]
        assert owner["status"] == "computed"
        for row in owner["native"]["rows"]:
            assert row["node_temperature_K"] == pytest.approx(
                [300.0] * len(row["node_temperature_K"]), abs=1e-10
            )
            assert row["cumulative_total_boundary_energy_J"] == pytest.approx(0.0, abs=1e-10)
            if row["step_energy_residual_J"] is not None:
                assert row["step_energy_residual_J"] == pytest.approx(0.0, abs=1e-10)
    assert verify_soil_moisture_record(result, request=request, context=context)


def test_unbalanced_property_jump_is_withheld_instead_of_hidden_as_boundary_heat():
    request, context = request_case(second_stage_water_fraction=1.0, second_stage_energy_J=1.0)
    result = evaluate_soil_moisture(request, context)

    assert result["status"] == "withheld"
    assert len(result["stages"]) == 1
    assert "does not reconcile" in result["failure"]
    assert result["thermal_boundary_energy_J"] == pytest.approx(0.0, abs=1e-10)
    with pytest.raises(ValueError, match="withheld moisture snapshots"):
        verify_soil_moisture_record(result)


def test_reviewed_heat_capacity_jump_reconciles_against_carried_p1_field():
    first_request, context = request_case(mode="robin", stage_count=1)
    first = evaluate_soil_moisture(first_request, context)
    assert first["status"] == "computed_diagnostic", first["failure"]
    owner = first["stages"][0]["owner_record"]["native"]
    row = owner["rows"][-1]
    volume_temperature = sum(
        area * (temperature - first_request.property_table.sensible_energy_reference_temperature_K)
        for area, temperature in zip(
            owner["geometry"]["cell_areas_m2"], row["cell_mean_from_P1_K"], strict=True
        )
    ) * first_request.stages[0].fem.mesh.axial_extrusion_length_m
    old_capacity = soil_moisture_properties(
        first_request.property_table, first_request.stages[0].volumetric_water_fraction
    )["volumetric_heat_capacity_J_m3_K"]
    new_capacity = soil_moisture_properties(first_request.property_table, 1.0)[
        "volumetric_heat_capacity_J_m3_K"
    ]
    required_jump_J = (new_capacity - old_capacity) * volume_temperature
    assert required_jump_J > 0.0

    request, context = request_case(
        mode="robin", second_stage_water_fraction=1.0, second_stage_energy_J=required_jump_J
    )
    result = evaluate_soil_moisture(request, context)
    assert result["status"] == "computed_diagnostic", result["failure"]
    assert result["energy_transitions"][1]["property_jump_energy_required_J"] == pytest.approx(
        required_jump_J, rel=1e-10, abs=1e-9
    )
    assert result["energy_transitions"][1]["supplied_moisture_energy_J"] == pytest.approx(
        required_jump_J, rel=1e-10, abs=1e-9
    )
    assert abs(result["energy_residual_J"]) <= 1e-7 + 1e-8 * max(
        abs(result["initial_sensible_energy_J"]), abs(result["final_sensible_energy_J"]), 1.0
    )
    assert verify_soil_moisture_record(result, request=request, context=context)


def pump_moc_case():
    density, gravity = 998.0, 9.80665
    length, diameter, wave_speed, friction = 10.0, 0.1, 1000.0, 0.02
    duration, timestep = 0.02, 0.005
    pipe_flow, pump_flow = -0.001, 0.001
    segments = round(length / (wave_speed * timestep))
    area = 3.141592653589793 * diameter**2 / 4.0
    segment_dx = length / segments
    segment_resistance = friction * segment_dx / (2.0 * gravity * diameter * area**2)
    darcy_head_change = segment_resistance * segments * pipe_flow * abs(pipe_flow)
    head_a = 10.0
    head_b = head_a - darcy_head_change
    pressure_rise = density * gravity * (head_b - head_a)
    performance_map = SignedRotatingMap(
        id="authored_linear_signed_map",
        speed_rad_s=(90.0, 110.0),
        volume_flow_m3_s=(0.0005, 0.0015),
        resisting_torque_N_m=((1.0, 1.0), (1.0, 1.0)),
        pressure_rise_Pa=((pressure_rise - 0.5, pressure_rise + 0.5),
                          (pressure_rise - 0.5, pressure_rise + 0.5)),
        evidence_id="map",
        signed_axes_and_torque_pressure_conventions_reviewed=True,
    )
    pump = LiquidPump(
        id="pump",
        start_node="a",
        end_node="b",
        shaft_id="shaft",
        performance_map=performance_map,
        initial_speed_rad_s=100.0,
        initial_volume_flow_m3_s=pump_flow,
        inertia_kg_m2=0.1,
        drive_torque_N_m=1.0,
        rotor_and_drive_evidence_id="drive",
        colocated_equal_velocity_head_ports_reviewed=True,
        initial_drive_equilibrium_required=True,
    )
    history_a, history_b = History(time_s=(0.0,), values=(head_a,)), History(time_s=(0.0,), values=(head_b,))
    specification = LiquidTransient(
        nodes=(
            LiquidNode(id="a", elevation_m=0.0, initial_head_m=head_a,
                       boundary="head", history=history_a),
            LiquidNode(id="b", elevation_m=0.0, initial_head_m=head_b,
                       boundary="head", history=history_b),
        ),
        pipes=(LiquidPipe(id="pipe", start_node="a", end_node="b", length_m=length,
                          diameter_m=diameter, wave_speed_m_s=wave_speed,
                          darcy_friction_factor=friction, initial_volume_flow_m3_s=pipe_flow),),
        pumps=(pump,),
        duration_s=duration,
        requested_timestep_s=timestep,
        density_kg_m3=density,
        ambient_pressure_pa_abs=101325.0,
        vapour_pressure_pa_abs=2000.0,
        initial_head_tolerance_m=1e-9,
        initial_flow_tolerance_m3_s=1e-10,
        mass_balance_tolerance_m3=1e-8,
        valve_head_tolerance_m=1e-8,
        valve_flow_tolerance_m3_s=1e-10,
    )
    context = RunContext(
        asset_id="closed_pump_loop",
        scenario_id="authored_steady_circulation",
        clock_id="elapsed_s",
        origin="synthetic",
        evidence=(
            EvidenceRef(id="map", citation="Authored linear pump map for solver verification", origin="synthetic"),
            EvidenceRef(id="drive", citation="Authored constant drive torque fixture", origin="synthetic"),
        ),
    )
    request = PumpMOCRequest(id="pump_moc", asset_id=context.asset_id,
                             clock_id=context.clock_id, specification=specification)
    return request, context, dict(pipe_head_change_m=darcy_head_change,
                                  pump_pressure_rise_Pa=pressure_rise,
                                  pump_flow_m3_s=pump_flow)


def test_simultaneous_pump_moc_steady_circulation_matches_darcy_map_and_rotor_references():
    request, context, reference = pump_moc_case()
    result = evaluate_pump_moc(request, context)
    assert result["status"] == "computed_diagnostic", result["native"]["notes"]
    native = result["native"]
    assert native["simultaneous_pump_rotor_coupling"] is True
    assert native["grid"]["pipe"]["segments"] == 2
    assert native["timestep_s"] == pytest.approx(0.005, abs=1e-14)
    assert native["all_volume_balances_within_tolerance"]
    assert native["maximum_pipe_volume_residual_m3"] <= request.specification.mass_balance_tolerance_m3
    assert abs(native["maximum_junction_residual_m3_s"]) <= request.specification.valve_flow_tolerance_m3_s

    pump_rows = native["pumps"]["pump"]
    for row in pump_rows:
        assert row["speed_rad_s"] == pytest.approx(100.0, abs=1e-8)
        assert row["volume_flow_m3_s"] == pytest.approx(reference["pump_flow_m3_s"], abs=1e-9)
        assert row["head_gain_m"] == pytest.approx(-reference["pipe_head_change_m"], abs=1e-8)
        assert row["energy_residual_J"] == pytest.approx(0.0, abs=1e-6)
    for audit in native["pump_solver"]:
        assert audit["root_qualified"]
        assert audit["maximum_flow_residual_m3_s"] <= request.specification.valve_flow_tolerance_m3_s
        assert audit["maximum_head_residual_m"] <= request.specification.valve_head_tolerance_m
        assert audit["pumps"]["pump"]["map_passive"]
        assert audit["pumps"]["pump"]["cumulative_energy_residual_J"] == pytest.approx(0.0, abs=1e-6)
    assert verify_pump_moc_record(result, request=request, context=context)
