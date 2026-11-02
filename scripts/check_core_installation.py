"""Run with a clean wheel installation containing only mandatory dependencies."""

import argparse
import hashlib
import importlib.util
import json
import platform
import runpy
import sys
import zipfile
from pathlib import Path

import pipeline_integrity
from pipeline_integrity import create_registry
from pipeline_integrity.engineering_methods import engineering_method_implementation_progress
from pipeline_integrity.acoustics import DelayRequest, PipeSpan
from pipeline_integrity.alarm_exchange import replay_alarm
from pipeline_integrity.buried_thermal import evaluate_buried_thermal, verify_buried_thermal_record
from pipeline_integrity.calibration import fit_calibration, verify_calibration_record
from pipeline_integrity.capabilities import capabilities
from pipeline_integrity.characterization import evaluate_characterization
from pipeline_integrity.compressor_train import evaluate_compressor_train, verify_compressor_train_record
from pipeline_integrity.consequence_exchange import AnnualRiskRequest, ConsequenceCase
from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame
from pipeline_integrity.control_exchange import PIDSession
from pipeline_integrity.corrosion_wear import evaluate_corrosion_wear
from pipeline_integrity.creep_exchange import evaluate_creep, evaluate_creep_sequence
from pipeline_integrity.cyclic_fatigue import evaluate_cyclic_fatigue
from pipeline_integrity.datasets import catalog
from pipeline_integrity.electrochemical_exchange import evaluate_electrochemical
from pipeline_integrity.equipment_exchange import PumpMapConversion, lh2dt_pump_map
from pipeline_integrity.fabrication_exchange import evaluate_fabrication
from pipeline_integrity.fatigue_exchange import (
    PressureFatigueRequest,
    PressureHistory,
    assess_pressure_fatigue,
)
from pipeline_integrity.fracture_exchange import assess_mode_i
from pipeline_integrity.frame_loading import (
    prepare_pressure_frame,
    prepare_uniform_thermal_frame,
    solve_loaded_frame,
)
from pipeline_integrity.gas_meter import evaluate_gas_meter, verify_gas_meter_record
from pipeline_integrity.gas_network import GasNetwork, solve_gas_network
from pipeline_integrity.hardness_exchange import evaluate_hardness
from pipeline_integrity.hydrogen_transport import evaluate_hydrogen_transport
from pipeline_integrity.ili import ILILoadRequest
from pipeline_integrity.inspection import InspectionRun
from pipeline_integrity.inspection_planning import InspectionHypothesis, plan_inspections
from pipeline_integrity.inspection_statistics import evaluate_inspection_statistics
from pipeline_integrity.instrument_exchange import TransmitterScenario, replay_transmitter
from pipeline_integrity.inverse import FitParameter
from pipeline_integrity.knowledge_exchange import evaluate_knowledge, verify_knowledge_record
from pipeline_integrity.lifecycle import evaluate_lifecycle
from pipeline_integrity.lifecycle_exchange import PowerIntegration, integrate_power_frames
from pipeline_integrity.lifting import evaluate_lifting, verify_lifting_record
from pipeline_integrity.material_exchange import evaluate_pressure_section
from pipeline_integrity.material_quality import evaluate_material_quality
from pipeline_integrity.metrology import verify_measurement_uncertainty
from pipeline_integrity.models.pump_curves import ReferencePumpCurve
from pipeline_integrity.monitoring_exchange import evaluate_monitoring, verify_monitoring_record
from pipeline_integrity.nde import EchoThicknessRequest, evaluate_echo_thickness, load_tubers_waveform
from pipeline_integrity.period_risk import evaluate_period_logic
from pipeline_integrity.phase_change_exchange import evaluate_phase_change, verify_phase_change_record
from pipeline_integrity.pipe_soil_transient import (
    evaluate_pipe_soil_transient,
    verify_pipe_soil_transient_record,
)
from pipeline_integrity.pool_exchange import PoolRequest, evaluate_pool_request
from pipeline_integrity.pool_exchange import reference_config as pool_config
from pipeline_integrity.pressure_tests import evaluate_gas_decay_series, evaluate_pressure_test
from pipeline_integrity.profile_capacity import evaluate_profile_capacity
from pipeline_integrity.release_exchange import PipeAccidentRequest
from pipeline_integrity.reliability_exchange import evaluate_physical_reliability
from pipeline_integrity.response_exchange import HumanResponseClient, ResponseWindow
from pipeline_integrity.risk_exchange import FaultTreeRequest, HRARiskClient, RiskProbability
from pipeline_integrity.scheduling import ScheduleRequest, solve_schedule
from pipeline_integrity.soil_cross_section import (
    evaluate_soil_cross_section,
    verify_soil_cross_section_record,
)
from pipeline_integrity.soil_fem import evaluate_soil_fem, verify_soil_fem_record
from pipeline_integrity.spatial_risk import evaluate_spatial_risk, verify_spatial_risk_record
from pipeline_integrity.station_exchange import evaluate as evaluate_station
from pipeline_integrity.station_exchange import validate_stored_result as verify_station
from pipeline_integrity.structural import solve_frame
from pipeline_integrity.tanks import (
    AxisymmetricTank,
    CalibratedTankCurve,
    DeclaredTankDensity,
    TankInventoryRequest,
    TankObservation,
    evaluate_tank_inventory,
)
from pipeline_integrity.totalization import totalize_measurements, verify_totalization_record
from pipeline_integrity.transient import LiquidTransient, LiquidValve, simulate_liquid_transient
from pipeline_integrity.uncertainty import (
    EnsembleMetric,
    EnsembleRequest,
    JointCase,
    Marginal,
    UncertainVariable,
    evaluate_ensemble,
    generate_ensemble,
    registry_node,
)
from pipeline_integrity.uncertainty_fit import fit_joint_distribution
from pipeline_integrity.units import convert_quantity
from pipeline_integrity.valve_motion import ConductanceCurve, ValveMotionRequest
from pipeline_integrity.water_inverse import WaterParameter
from pipeline_integrity.water_quality import WaterQualityRun
from pipeline_integrity.welding_exchange import evaluate_weld_waveform, evaluate_welding
from pipeline_integrity.workflow import Workflow, run_workflow


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--output", type=Path, default=Path("research/core_installation.json"))
    args = parser.parse_args()
    installed = Path(pipeline_integrity.__file__).resolve().is_relative_to(Path(sys.prefix).resolve())
    assert installed, "use the built wheel in an isolated environment"
    wheel = args.wheel or Path("dist") / f"pipeline_safety_toolkit-{pipeline_integrity.__version__}-py3-none-any.whl"
    with zipfile.ZipFile(wheel) as archive:
        for name in archive.namelist():
            if name.startswith("pipeline_integrity/") and not name.endswith("/"):
                installed_file = Path(pipeline_integrity.__file__).parent / name.split("/", 1)[1]
                assert installed_file.read_bytes() == archive.read(name), (
                    "installed code differs from the current wheel"
                )
    missing_optional = {
        name: importlib.util.find_spec(name) is None
        for name in (
            "CoolProp",
            "fluids",
            "ht",
            "wntr",
            "pandas",
            "scipy",
            "lh2dt",
            "lh2_tno",
            "matintegrity",
            "human_reliability",
            "slabx",
            "slabx_lh2",
            "openpyxl",
            "matplotlib",
            "rainflow",
            "ortools",
            "Pynite",
            "h5py",
            "process_monitoring",
            "dynamiclh2poolx",
            "h2station",
            "saga",
            "pymupdf",
            "sklearn",
            "pyod",
            "torch",
            "fipy",
            "skfem",
            "pypdf",
            "odrpack",
        )
    }
    assert all(missing_optional.values()), "this check requires a core-only environment"
    engineering_progress = engineering_method_implementation_progress()
    assert engineering_progress["implemented"] == engineering_progress["total"] == 223
    engine = create_registry()
    data = json.loads(Path("examples/engineering_cases.json").read_text(encoding="utf-8"))
    context = RunContext.model_validate_json(json.dumps(data["context"]))
    pump = engine.evaluate("equipment.pump_power", data["cases"]["equipment.pump_power"], context)
    assert pump.status == "computed"
    workflow = Workflow.model_validate_json(
        Path("examples/pump_energy_workflow.json").read_text(encoding="utf-8")
    )
    assert run_workflow(engine, workflow)["all_computed"]
    assert convert_quantity(36.0, "m3/h", "m3/s") == 0.01
    mapped = lh2dt_pump_map(
        PumpMapConversion(
            reference_curve=ReferencePumpCurve(
                volume_flow_m3_s=(0.0, 0.01), head_m=(40.0, 20.0), source_id="core_installation_probe"
            ),
            reference_density_kg_m3=1000.0,
        )
    )
    assert mapped["head_curve_points"][1] == [10.0, 196133.0]
    assert "initial_pressure_pa_abs" in PipeAccidentRequest.model_fields
    assert "joint_ids" in ILILoadRequest.model_fields and "coordinate_frame_id" in InspectionRun.model_fields
    assert "sources" in WaterQualityRun.model_fields and "parameter_source_id" in WaterQualityRun.model_fields
    assert "lower" in FitParameter.model_fields and "kind" in WaterParameter.model_fields
    assert "timing" in DelayRequest.model_fields and "source_bracketed" in PipeSpan.model_fields
    assert (
        "binding_digest" in RiskProbability.model_fields and "joint_states" in FaultTreeRequest.model_fields
    )
    assert "initiating_condition_id" in ConsequenceCase.model_fields
    assert "time_scope_evidence_id" in AnnualRiskRequest.model_fields
    assert "valves" in LiquidTransient.model_fields and "binding_digest" in LiquidValve.model_fields
    assert "position_tolerance" in ValveMotionRequest.model_fields
    assert "parameter_source_id" in ConductanceCurve.model_fields
    schedule_job = json.loads(Path("examples/operations_schedule.json").read_text(encoding="utf-8"))
    schedule_spec = ScheduleRequest.model_validate_json(json.dumps(schedule_job["specification"]))
    schedule_context = RunContext.model_validate_json(json.dumps(schedule_job["context"]))
    try:
        solve_schedule(schedule_spec.model_copy(update={"time_quantum_s": 7.0}), schedule_context)
    except ValueError as error:
        assert "time grid" in str(error)
    else:
        raise AssertionError("planning times cannot be silently rounded")
    try:
        solve_schedule(schedule_spec, schedule_context)
    except ImportError as error:
        assert "ortools" in str(error)
    else:
        raise AssertionError("schedule optimization requires its separately installed solver")
    gas_job = json.loads(Path("examples/gas_network.json").read_text(encoding="utf-8"))
    gas_spec = GasNetwork.model_validate_json(json.dumps(gas_job["specification"]))
    gas_context = RunContext.model_validate_json(json.dumps(gas_job["context"]))
    raised = gas_spec.junctions[0].model_copy(update={"elevation_m": 1.0})
    unsupported_gas = solve_gas_network(gas_spec.model_copy(update={"junctions": (raised,)}), gas_context)
    assert unsupported_gas.status == "out_of_scope" and unsupported_gas.native is None
    try:
        solve_gas_network(gas_spec, gas_context)
    except ImportError as error:
        assert "lh2dt" in str(error)
    else:
        raise AssertionError("gas physics needs the separately installed native engine")
    fatigue_job = json.loads(Path("examples/pressure_fatigue.json").read_text(encoding="utf-8"))
    fatigue_request = PressureFatigueRequest.model_validate_json(json.dumps(fatigue_job["specification"]))
    fatigue_context = RunContext.model_validate_json(json.dumps(fatigue_job["context"]))
    unresolved = PressureHistory.model_validate_json(
        json.dumps(
            fatigue_request.history.model_dump(mode="json")
            | {"pressure_reference": "unspecified", "ambient_pressure_pa_abs": None, "binding_digest": None}
        )
    )
    withheld = assess_pressure_fatigue(
        fatigue_request.model_copy(update={"history": unresolved}), fatigue_context
    )
    assert withheld.status == "needs_data" and withheld.native_result is None
    try:
        assess_pressure_fatigue(fatigue_request, fatigue_context)
    except ImportError as error:
        assert "matintegrity" in str(error)
    else:
        raise AssertionError("resolved fatigue needs the separately installed material engine")
    try:
        HRARiskClient()
    except ImportError as error:
        assert "human_reliability" in str(error)
    else:
        raise AssertionError("the core-only environment must not contain the optional HRA engine")
    window = ResponseWindow(clock_id="elapsed", cue_time_s=5.0, limit_time_s=15.0, evidence_id="example")
    assert window.available_s == 10.0
    hypothesis = InspectionHypothesis(
        id="example", prior_mass=1.0, unrevealed_weight=1.0, evidence_id="example"
    )
    assert hypothesis.prior_mass == 1.0
    tank_context = RunContext(
        asset_id="core_tank",
        scenario_id="declared",
        clock_id="elapsed",
        origin="synthetic",
        evidence=(EvidenceRef(id="e", citation="Original calibration reference", origin="synthetic"),),
    )
    curve = CalibratedTankCurve(heights_m=(0.0, 1.0), volumes_m3=(0.0, 1.0), evidence_id="e")
    tank_request = TankInventoryRequest(
        geometry=curve,
        observation=TankObservation(
            tank_id="core_tank",
            clock_id="elapsed",
            time_s=0.0,
            quantity="level_from_internal_bottom",
            value=0.5,
            unit="m",
            evidence_id="e",
        ),
        liquid_density=DeclaredTankDensity(
            phase="liquid",
            fluid="authored_liquid",
            density_kg_m3=1000.0,
            property_basis="authored",
            evidence_id="e",
        ),
        headspace_density=DeclaredTankDensity(
            phase="gas", fluid="authored_gas", density_kg_m3=1.0, property_basis="authored", evidence_id="e"
        ),
    )
    calibrated = evaluate_tank_inventory(tank_request, tank_context)
    assert calibrated["mass_status"] == "computed" and calibrated["values"]["total_fluid_mass_kg"] == 500.5
    native_tank = tank_request.model_copy(
        update={
            "geometry": AxisymmetricTank(
                inner_diameter_m=1.0, straight_length_m=1.0, horizontal=False, evidence_id="e"
            )
        }
    )
    assert (
        evaluate_tank_inventory(
            native_tank.model_copy(
                update={"observation": native_tank.observation.model_copy(update={"value": None})}
            ),
            tank_context,
        )["status"]
        == "needs_data"
    )
    try:
        evaluate_tank_inventory(native_tank, tank_context)
    except ImportError as error:
        assert "fluids" in str(error)
    else:
        raise AssertionError("native tank geometry requires the separately installed engineering library")
    nde_fixture = Path("artifacts/verification/core_nde_fixture.zip")
    nde_fixture.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(nde_fixture, "w") as fixture:
        fixture.writestr(
            "Ultrasonic Data/Reference/trace.csv", "x-axis,1\nsecond,Volt\n-1e-6,1\n0,2\n1e-6,3\n2e-6,\n"
        )
    trace = load_tubers_waveform(nde_fixture, "Ultrasonic Data/Reference/trace.csv")
    assert trace.time_s[0] == -1e-6 and trace.voltage_v[-1] is None
    nde_context = RunContext(
        asset_id="core_reference",
        scenario_id="declared",
        clock_id=trace.native_clock_id,
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="e",
                citation="Authored core-installation fixture",
                origin="synthetic",
                sha256=trace.archive_sha256,
            ),
        ),
    )
    nde_request = EchoThicknessRequest(
        waveform_digest=trace.waveform_digest,
        reference_index=0,
        return_index=2,
        sound_speed_m_s=2000.0,
        time_zero_offset_s=0.0,
        incidence_angle_rad=0.0,
        source_evidence_id="e",
        echo_review_evidence_id="e",
        calibration_evidence_id="e",
    )
    assert (
        evaluate_echo_thickness(trace, nde_request.model_copy(update={"return_index": 3}), nde_context)[
            "status"
        ]
        == "needs_data"
    )
    try:
        evaluate_echo_thickness(trace, nde_request, nde_context)
    except ImportError as error:
        assert "matintegrity" in str(error)
    else:
        raise AssertionError("reviewed echo metrology requires the separately installed material engine")
    inspection_example = runpy.run_path("examples/inspection_campaign.py")
    try:
        plan_inspections(*inspection_example["make_case"]())
    except ImportError as error:
        assert "matintegrity" in str(error)
    else:
        raise AssertionError("inspection likelihoods require the separately installed material engine")
    try:
        HumanResponseClient()
    except ImportError as error:
        assert "human_reliability" in str(error)
    else:
        raise AssertionError("time/recovery calculations require the separate HRA engine")
    transient_request = json.loads(Path("examples/liquid_transient.json").read_text(encoding="utf-8"))
    transient = simulate_liquid_transient(
        LiquidTransient.model_validate_json(json.dumps(transient_request["specification"])),
        RunContext.model_validate_json(json.dumps(transient_request["context"])),
    )
    assert transient["status"] == "computed" and transient["volume_balance"]["within_tolerance"]
    try:
        engine.evaluate("inventory.uniform", data["cases"]["inventory.uniform"], context)
    except ImportError:
        pass
    else:
        raise AssertionError("a missing optional dependency must be reported")
    control_example = runpy.run_path("examples/control_loop.py")
    loop, control_context = control_example["make_loop"]()
    try:
        PIDSession(loop, control_context)
    except ImportError as error:
        assert "lh2dt" in str(error)
    else:
        raise AssertionError("PID control requires the separately installed owner")
    transmitter = TransmitterScenario(
        instrument_id=control_context.asset_id,
        source_stream_id=loop.measurement_stream_id,
        channel=loop.measurement_channel,
        axis=loop.measurement_axis,
        lower_range=0.0,
        upper_range=1e6,
        initial_filtered_value=200000.0,
        parameter_evidence_id="authored:parameters",
        source_evidence_id="authored:source",
    )
    control_frame = control_example["observe"](loop, control_context, 0.0, 200000.0).frame
    try:
        replay_transmitter(transmitter, (control_frame,), control_context)
    except ImportError as error:
        assert "lh2dt" in str(error)
    else:
        raise AssertionError("transmitter physics requires the separately installed owner")
    material_example = runpy.run_path("examples/material_sections.py")
    section = material_example["pressure_case"]()
    section_context = material_example["context"]()
    unresolved_section = section.model_copy(update={"internal_pressure": None})
    assert evaluate_pressure_section(unresolved_section, section_context)["status"] == "needs_data"
    try:
        evaluate_pressure_section(section, section_context)
    except ImportError as error:
        assert "matintegrity" in str(error)
    else:
        raise AssertionError("resolved section elasticity requires the separately installed material engine")
    fracture_example = runpy.run_path("examples/fracture_screen.py")
    crack = fracture_example["make_mode_i"]()
    crack_context = fracture_example["context"]()
    unqualified_crack = crack.model_copy(
        update={"resistance": crack.resistance.model_copy(update={"kind": "crack_arrest"})}
    )
    assert assess_mode_i(unqualified_crack, crack_context)["status"] == "needs_data"
    try:
        assess_mode_i(crack, crack_context)
    except ImportError as error:
        assert "matintegrity" in str(error)
    else:
        raise AssertionError("qualified Mode-I calculations require the material owner")
    frame_example = runpy.run_path("examples/pipe_frame.py")
    frame_request, frame_context = frame_example["make_case"](), frame_example["context"]()
    assert (
        solve_frame(frame_request.model_copy(update={"supports_reviewed": False}), frame_context)["status"]
        == "needs_data"
    )
    try:
        solve_frame(frame_request, frame_context)
    except ImportError as error:
        assert "structural" in str(error)
    else:
        raise AssertionError("reviewed linear frames require the optional structural solver")
    loading_example = runpy.run_path("examples/frame_loading.py")
    loading_frame, loading_context = loading_example["reference_frame"]()
    temperature = loading_example["temperature"]()
    unresolved_temperature = prepare_uniform_thermal_frame(
        loading_frame, temperature.model_copy(update={"temperature_k": None}), loading_context
    )
    assert (
        solve_loaded_frame(loading_frame, (unresolved_temperature,), loading_context)["status"]
        == "needs_data"
    )
    unresolved_pressure = evaluate_pressure_section(
        loading_example["pressure_section"](loading_frame).model_copy(update={"internal_pressure": None}),
        loading_context,
    )
    pressure_contribution = prepare_pressure_frame(
        loading_frame,
        unresolved_pressure,
        loading_example["pressure_mapping"](unresolved_pressure),
        loading_context,
    )
    assert (
        solve_loaded_frame(loading_frame, (pressure_contribution,), loading_context)["status"] == "needs_data"
    )
    try:
        prepare_uniform_thermal_frame(loading_frame, temperature, loading_context)
    except ImportError as error:
        assert "matintegrity" in str(error)
    else:
        raise AssertionError("free thermal strain uses the separately installed material owner")
    lifecycle_example = runpy.run_path("examples/lifecycle_study.py")
    lifecycle_study, lifecycle_context = lifecycle_example["make_case"]()
    lifecycle = evaluate_lifecycle(lifecycle_study, lifecycle_context, engine=engine)
    assert lifecycle["status"] == "computed" and lifecycle["comparisons"]["least_present_cost_ids"] == [
        "replace"
    ]
    assert lifecycle["comparisons"]["nondominated_ids"] == ["keep", "replace"]
    assert (
        evaluate_lifecycle(
            lifecycle_study.model_copy(update={"annual_discount_rate": None}),
            lifecycle_context,
            engine=engine,
        )["status"]
        == "needs_data"
    )
    power_frames = tuple(
        SignalFrame(
            stream_id="core_power",
            clock_id=lifecycle_context.clock_id,
            time_s=time,
            values={"power": 1000.0},
            units={"power": "W"},
            valid={"power": True},
            origin="synthetic",
        )
        for time in (0.0, 1.0)
    )
    power_integral = integrate_power_frames(
        power_frames,
        PowerIntegration(
            channel="power",
            maximum_sample_interval_s=1.0,
            sign_convention="nonnegative_consumption",
            evidence_id="reference",
        ),
        lifecycle_context,
    )
    assert power_integral["status"] == "computed" and power_integral["values"]["import_energy_j"] == 1000.0
    variable = UncertainVariable(id="flow", unit="m3/s", layer="input", evidence_id="reference")
    joint = EnsembleRequest(
        id="core_joint_cases",
        variables=(variable,),
        method="joint_cases",
        dependence="paired_rows",
        interpretation="scenario",
        evidence_id="reference",
        definition_reviewed=True,
        cases=(
            JointCase(id="known", weight=0.75, values={"flow": 0.01}, evidence_id="reference"),
            JointCase(id="missing", weight=0.25, values={"flow": None}, evidence_id="reference"),
        ),
    )
    pump_node = registry_node(
        engine,
        id="pump",
        model_id="equipment.pump_power",
        base_inputs=dict(
            volume_flow_m3_s=0.01,
            density_kg_m3=1000.0,
            head_m=40.0,
            pump_efficiency=0.75,
            motor_efficiency=0.9,
        ),
        bindings={"flow": "volume_flow_m3_s"},
        variables=(variable,),
        metrics=(
            EnsembleMetric(id="electrical_power_w", unit="W", meaning="quantity", evidence_id="reference"),
        ),
    )
    ensemble = evaluate_ensemble(generate_ensemble(joint, lifecycle_context), (pump_node,))
    assert ensemble["status"] == "needs_data"
    assert ensemble["summaries"]["pump"]["electrical_power_w"]["unresolved_weight"] == 0.25
    sampling = EnsembleRequest(
        id="core_sampler",
        variables=(variable,),
        method="sobol",
        samples=8,
        seed=1,
        dependence="independent",
        interpretation="epistemic",
        evidence_id="reference",
        definition_reviewed=True,
    )
    assert generate_ensemble(sampling, lifecycle_context)["status"] == "needs_data"
    resolved = variable.model_copy(
        update={"marginal": Marginal(family="uniform", parameters=dict(lower=0.001, upper=0.01))}
    )
    try:
        generate_ensemble(sampling.model_copy(update={"variables": (resolved,)}), lifecycle_context)
    except ImportError:
        pass
    else:
        raise AssertionError("resolved sampling should require the absent SciPy dependency")
    fit_case = runpy.run_path("examples/fit_uncertainty.py")
    fit_observations, fit_request, _, _ = fit_case["make_case"]()
    fit_unreviewed = fit_joint_distribution(
        fit_observations, fit_request.model_copy(update={"definition_reviewed": False})
    )
    assert fit_unreviewed["status"] == "needs_data" and "scipy" not in fit_unreviewed["runtime"]
    try:
        fit_joint_distribution(fit_observations, fit_request)
    except ImportError:
        pass
    else:
        raise AssertionError("reviewed distribution fitting should require the absent SciPy dependency")
    electrochemical_cases, electrochemical_context = runpy.run_path("examples/electrochemical_methods.py")[
        "make_cases"
    ]()
    request = electrochemical_cases["corrosion.faraday"]
    assert (
        evaluate_electrochemical(
            request.model_copy(update={"applicability_reviews": {}}), electrochemical_context
        )["status"]
        == "needs_data"
    )
    assert (
        evaluate_electrochemical(
            request.model_copy(update={"current_meaning": "protective_demand"}), electrochemical_context
        )["status"]
        == "out_of_scope"
    )
    try:
        evaluate_electrochemical(request, electrochemical_context)
    except ImportError:
        pass
    else:
        raise AssertionError("reviewed electrochemical calculation requires the absent material owner")
    creep_cases, creep_sequence, creep_context = runpy.run_path("examples/creep_methods.py")["make_cases"]()
    creep_request = creep_cases["creep.norton"]
    assert (
        evaluate_creep(creep_request.model_copy(update={"applicability_reviews": {}}), creep_context)[
            "status"
        ]
        == "needs_data"
    )
    assert (
        evaluate_creep(creep_request.model_copy(update={"evaluation_temperature_k": 1200.0}), creep_context)[
            "status"
        ]
        == "out_of_scope"
    )
    missing_block = creep_sequence.blocks[-1].model_copy(update={"rupture_life_s": None})
    assert (
        evaluate_creep_sequence(
            creep_sequence.model_copy(update={"blocks": (*creep_sequence.blocks[:-1], missing_block)}),
            creep_context,
        )["status"]
        == "needs_data"
    )
    try:
        evaluate_creep(creep_request, creep_context)
    except ImportError:
        pass
    else:
        raise AssertionError("reviewed creep calculation requires the absent material owner")
    period_cases, period_context = runpy.run_path("examples/period_risk.py")["make_cases"]()
    period_request = period_cases["independent_or"]
    assert (
        evaluate_period_logic(
            period_request.model_copy(update={"event_logic_reviewed": False}), period_context
        )["status"]
        == "withheld"
    )
    try:
        evaluate_period_logic(period_request, period_context)
    except ImportError:
        pass
    else:
        raise AssertionError("reviewed period Boolean calculation requires the absent HRA owner")
    pressure_cases, pressure_frames, pressure_spec, pressure_context = runpy.run_path(
        "examples/pressure_tests.py"
    )["make_cases"]()
    assert (
        evaluate_pressure_test(
            pressure_cases["inspection.pressure_test_load"].model_copy(update={"applicability_reviews": {}}),
            pressure_context,
        )["status"]
        == "needs_data"
    )
    assert (
        evaluate_gas_decay_series(
            pressure_frames, pressure_spec.model_copy(update={"rigid_volume_m3": None}), pressure_context
        )["status"]
        == "needs_data"
    )
    try:
        evaluate_pressure_test(pressure_cases["inspection.pressure_test_load"], pressure_context)
    except ImportError:
        pass
    else:
        raise AssertionError("reviewed pressure-test calculation requires the absent material owner")
    weld_cases, weld_frames, weld_spec, _, _, weld_context = runpy.run_path("examples/welding_methods.py")[
        "make_cases"
    ]()
    assert (
        evaluate_welding(
            weld_cases["welding.arc_energy"].model_copy(update={"applicability_reviews": {}}), weld_context
        )["status"]
        == "needs_data"
    )
    incomplete_weld = evaluate_weld_waveform(
        weld_frames, weld_spec.model_copy(update={"efficiency": None}), weld_context
    )
    assert incomplete_weld["status"] == "needs_data"
    assert incomplete_weld["electrical_diagnostic"]["values"]["import_energy_j"] == 600.0
    try:
        evaluate_welding(weld_cases["welding.arc_energy"], weld_context)
    except ImportError:
        pass
    else:
        raise AssertionError("reviewed welding calculation requires the absent material owner")
    alarm_frames, alarm_policy, _, _, _, alarm_context = runpy.run_path("examples/response_trace.py")[
        "make_case"
    ]()
    unreviewed_alarm = replay_alarm(
        alarm_frames, alarm_policy.model_copy(update={"policy_reviewed": False}), alarm_context
    )
    assert unreviewed_alarm.status == "needs_data"
    unreviewed_alarm.check_integrity()
    try:
        replay_alarm(alarm_frames, alarm_policy, alarm_context)
    except ImportError:
        pass
    else:
        raise AssertionError("reviewed alarm replay requires the absent monitoring owner")
    physical_cases, _, _, physical_context = runpy.run_path("examples/physical_reliability.py")[
        "make_cases"
    ]()
    for physical_request in physical_cases.values():
        assert (
            evaluate_physical_reliability(
                physical_request.model_copy(update={"applicability_reviews": {}}), physical_context
            )["status"]
            == "needs_data"
        )
        try:
            evaluate_physical_reliability(physical_request, physical_context)
        except ImportError:
            pass
        else:
            raise AssertionError("reviewed physical reliability requires the absent material owner")
    for example, evaluator in (
        ("fabrication_methods.py", evaluate_fabrication),
        ("hardness_methods.py", evaluate_hardness),
        ("characterization.py", evaluate_characterization),
        ("inspection_statistics.py", evaluate_inspection_statistics),
        ("cyclic_fatigue.py", evaluate_cyclic_fatigue),
        ("corrosion_wear.py", evaluate_corrosion_wear),
        ("profile_capacity.py", evaluate_profile_capacity),
        ("hydrogen_transport.py", evaluate_hydrogen_transport),
        ("material_quality.py", evaluate_material_quality),
    ):
        material_cases, material_context = runpy.run_path(f"examples/{example}")["make_cases"]()
        for material_request in material_cases.values():
            unreviewed = evaluator(
                material_request.model_copy(update={"applicability_reviews": {}}), material_context
            )
            assert (
                unreviewed["status"] if isinstance(unreviewed, dict) else unreviewed.status
            ) == "needs_data"
            try:
                evaluator(material_request, material_context)
            except ImportError:
                pass
            else:
                raise AssertionError("reviewed material facade requires the absent material owner")
    for name in (
        "source_archive",
        "copper_tensile",
        "copper_hardness",
        "corrosion_data",
        "material_source",
        "material_process_data",
    ):
        __import__(f"pipeline_integrity.{name}")
    monitoring_request, monitoring_splits, monitoring_context = runpy.run_path(
        "examples/monitoring_exchange.py"
    )["make_case"]()
    monitoring_record = evaluate_monitoring(monitoring_request, monitoring_splits, monitoring_context)
    assert monitoring_record["status"] == "needs_data" and monitoring_record["native_receipt"] is None
    verify_monitoring_record(monitoring_record)
    for method, family in (
        ("dynamic_pca", "dynamic_subspace"),
        ("diffusion", "neural"),
        ("deep_graph_infomax", "temporal_neural"),
    ):
        request, splits, context = runpy.run_path("examples/monitoring_exchange.py")["make_case"](
            method=method, family=family
        )
        record = evaluate_monitoring(request, splits, context)
        assert record["status"] == "needs_data" and record["native_receipt"] is None
        verify_monitoring_record(record)
    knowledge_requests, knowledge_context = runpy.run_path("examples/knowledge_exchange.py")["make_cases"]()
    for knowledge_request in knowledge_requests.values():
        knowledge_record = evaluate_knowledge(knowledge_request, knowledge_context)
        assert knowledge_record["status"] == "needs_data" and knowledge_record["native_receipt"] is None
        verify_knowledge_record(knowledge_record)
    pool_record = evaluate_pool_request(PoolRequest("UNRESOLVED_SOURCE", pool_config(), monitoring_context))
    assert pool_record["status"] == "needs_data" and pool_record["native_called"] is False
    station_request = runpy.run_path("examples/station_exchange.py")["make_case"]()
    station_record = evaluate_station(station_request)
    assert station_record["status"] == "needs_data" and station_record["native_called"] is False
    assert verify_station(station_record)["valid"]
    radial_request, radial_context = runpy.run_path("examples/pipe_soil_transient.py")["make_case"]()
    radial_record = evaluate_pipe_soil_transient(radial_request, radial_context)
    assert radial_record["status"] == "needs_data" and radial_record["native_receipt"] is None
    verify_pipe_soil_transient_record(radial_record)
    soil_request, soil_context = runpy.run_path("examples/soil_cross_section.py")["make_case"]()
    soil_record = evaluate_soil_cross_section(soil_request, soil_context)
    assert soil_record["status"] == "needs_data"
    assert verify_soil_cross_section_record(soil_record)["valid"]
    __import__("pipeline_integrity.soil_data")
    fem_request, fem_context = runpy.run_path("examples/soil_fem.py")["make_case"]()
    fem_record = evaluate_soil_fem(fem_request, fem_context)
    assert fem_record["status"] == "needs_data"
    assert verify_soil_fem_record(fem_record)["valid"]
    gas_meter_request, gas_meter_context = runpy.run_path("examples/gas_meter.py")["make_case"]()
    gas_meter_record = evaluate_gas_meter(gas_meter_request, gas_meter_context)
    assert gas_meter_record["status"] == "needs_data"
    assert verify_gas_meter_record(gas_meter_record, gas_meter_request, gas_meter_context)["valid"]
    __import__("pipeline_integrity.meter_data")
    _, _, measurement_record = runpy.run_path("examples/metrology.py")["example"]()
    assert measurement_record["status"] == "computed"
    assert verify_measurement_uncertainty(measurement_record)
    calibration_request, calibration_context, calibration_provider = runpy.run_path(
        "examples/calibration.py"
    )["make_case"]()
    calibration_record = fit_calibration(calibration_request, calibration_context, calibration_provider)
    assert calibration_record["status"] == "needs_data" and calibration_record["native_called"] is False
    assert verify_calibration_record(calibration_record, calibration_provider)["valid"]
    __import__("pipeline_integrity.calibration_data")
    __import__("pipeline_integrity.totalization_data")
    total_request, total_context = runpy.run_path("examples/totalization.py")["make_case"](reviewed=True)
    total_record = totalize_measurements(total_request, total_context)
    assert total_record["status"] == "computed"
    assert verify_totalization_record(total_record)["valid"]
    spatial_request, spatial_context = runpy.run_path("examples/spatial_risk.py")["make_case"]()
    spatial_record = evaluate_spatial_risk(spatial_request, spatial_context)
    assert spatial_record["status"] == "needs_data"
    assert verify_spatial_risk_record(spatial_record)["valid"]
    lifting_example = runpy.run_path("examples/lifting.py")["make_case"]
    lifting_request, lifting_context = lifting_example()
    lifting_pending = evaluate_lifting(lifting_request, lifting_context)
    assert lifting_pending["status"] == "needs_data" and not lifting_pending["native_solver_called"]
    assert verify_lifting_record(lifting_pending)
    lifting_request, lifting_context = lifting_example(reviewed=True)
    lifting_record = evaluate_lifting(lifting_request, lifting_context)
    assert lifting_record["status"] == "computed"
    assert lifting_record["acceptance"] == "within_supplied_limits"
    assert verify_lifting_record(lifting_record, request=lifting_request, context=lifting_context)
    compressor_request, compressor_context = runpy.run_path("examples/compressor_train.py")["make_case"]()
    compressor_pending = evaluate_compressor_train(compressor_request, compressor_context)
    assert compressor_pending["status"] == "needs_data" and not compressor_pending["native_called"]
    assert verify_compressor_train_record(compressor_pending)["valid"]
    phase_request, phase_context = runpy.run_path("examples/phase_change_exchange.py")["make_case"]()
    phase_pending = evaluate_phase_change(phase_request, phase_context)
    assert phase_pending["status"] == "needs_data" and not phase_pending["native_called"]
    assert verify_phase_change_record(phase_pending)["valid"]
    __import__("pipeline_integrity.thermal_process_data")
    invalid_context = spatial_context.model_copy(update={"metadata": {"invalid": float("inf")}})
    try:
        invalid_context.model_dump_json()
    except ValueError:
        pass
    else:
        raise AssertionError("cloned nonfinite metadata must not become null")
    thermal_requests, thermal_context = runpy.run_path("examples/buried_thermal.py")["make_cases"]()
    for thermal_request in thermal_requests.values():
        thermal_record = evaluate_buried_thermal(
            thermal_request.model_copy(update={"applicability_reviews": {}}), thermal_context
        )
        assert thermal_record["status"] == "needs_data" and thermal_record["calculation"] is None
        verify_buried_thermal_record(thermal_record)
        try:
            evaluate_buried_thermal(thermal_request, thermal_context)
        except ImportError:
            pass
        else:
            raise AssertionError("reviewed steady buried thermal calculation requires the absent ht library")
    assert all(name not in sys.modules for name in ("h2station", "dynamiclh2poolx", "saga", "pymupdf"))
    assert all(
        name not in sys.modules for name in ("fipy", "torch", "h5py", "openpyxl", "skfem", "pypdf", "odrpack")
    )
    result = {
        "schema": "pipeline_integrity.core_installation.v1",
        "python": platform.python_version(),
        "version": pipeline_integrity.__version__,
        "installed_wheel_used": installed,
        "wheel_sha256": hashlib.sha256(wheel.read_bytes()).hexdigest(),
        "optional_dependencies_absent": missing_optional,
        "registered_models": len(engine.models()),
        "dataset_catalog_count": len(catalog()),
        "capability_count": len(capabilities()),
        "engineering_method_source_bindings": engineering_progress["implemented"],
        "pump_and_workflow": "passed",
        "unit_conversion": "passed",
        "lifecycle_cost_emission_and_partial_input_without_optional_libraries": "passed",
        "power_integration_without_optional_data_libraries": "passed",
        "paired_uncertainty_and_unresolved_weight_without_scipy": "passed",
        "unresolved_marginal_without_scipy": "needs_data",
        "resolved_sampler_requires_scipy": "explicit_import_error",
        "unreviewed_distribution_fit_without_scipy": "needs_data",
        "reviewed_distribution_fit_requires_scipy": "explicit_import_error",
        "electrochemical_unreviewed_without_owner": "needs_data",
        "protective_current_not_dissolution_without_owner": "out_of_scope",
        "reviewed_electrochemical_requires_owner": "explicit_import_error",
        "creep_unreviewed_or_missing_block_without_owner": "needs_data",
        "creep_temperature_support_without_owner": "out_of_scope",
        "reviewed_creep_requires_owner": "explicit_import_error",
        "unreviewed_common_period_without_owner": "withheld",
        "reviewed_period_logic_requires_owner": "explicit_import_error",
        "unreviewed_pressure_test_without_owner": "needs_data",
        "unresolved_gas_history_without_owner_or_properties": "needs_data",
        "reviewed_pressure_test_requires_owner": "explicit_import_error",
        "unreviewed_welding_without_owner_or_hdf": "needs_data",
        "partial_weld_signed_electrical_diagnostic_without_owner": "passed",
        "reviewed_welding_requires_owner": "explicit_import_error",
        "unreviewed_alarm_without_monitoring_owner": "needs_data",
        "reviewed_alarm_requires_monitoring_owner": "explicit_import_error",
        "unreviewed_physical_reliability_without_owner": "needs_data",
        "reviewed_physical_reliability_requires_owner": "explicit_import_error",
        "unreviewed_fabrication_hardness_characterization_without_owner": "needs_data",
        "reviewed_fabrication_hardness_characterization_requires_owner": "explicit_import_error",
        "copper_source_readers_import_without_optional_libraries": "passed",
        "corrosion_source_reader_and_guard_import_without_optional_libraries": "passed",
        "unreviewed_inspection_cyclic_corrosion_wear_without_owner": "needs_data",
        "reviewed_inspection_cyclic_corrosion_wear_requires_owner": "explicit_import_error",
        "unreviewed_profile_hydrogen_material_quality_without_owner": "needs_data",
        "reviewed_profile_hydrogen_material_quality_requires_owner": "explicit_import_error",
        "material_process_readers_import_without_optional_libraries": "passed",
        "unreviewed_monitoring_without_owner_or_optional_estimators": "needs_data",
        "unreviewed_dynamic_neural_graph_monitoring_without_torch": "needs_data",
        "unreviewed_knowledge_without_owner_or_document_library": "needs_data",
        "unreviewed_pool_without_owner_or_properties": "needs_data",
        "unreviewed_station_without_owner_or_properties": "needs_data",
        "unreviewed_pipe_soil_transient_without_material_owner": "needs_data",
        "unreviewed_soil_cross_section_without_fipy": "needs_data",
        "soil_source_readers_import_without_hdf_or_workbook_libraries": "passed",
        "unreviewed_soil_fem_without_skfem_or_scipy": "needs_data",
        "correlated_measurement_budget_without_optional_libraries": "passed",
        "unreviewed_gas_meter_without_fluids_or_coolprop": "needs_data",
        "meter_source_readers_import_without_workbook_or_document_library": "passed",
        "unreviewed_full_covariance_eiv_calibration_without_scipy_or_odrpack": "needs_data",
        "unreviewed_spatial_risk_without_owner_libraries": "needs_data",
        "calibration_and_totalization_source_readers_import_without_data_libraries": "passed",
        "joint_reading_time_totalization_without_optional_libraries": "passed",
        "unreviewed_lifting_without_native_solve": "needs_data",
        "declared_pose_lifting_without_optional_libraries": "passed",
        "unreviewed_compressor_train_without_owner_scipy_or_coolprop": "needs_data",
        "unreviewed_phase_change_without_owner_or_coolprop": "needs_data",
        "thermal_process_readers_import_without_workbook_or_document_library": "passed",
        "cloned_nonfinite_metadata_refused_before_json_null_conversion": "passed",
        "unreviewed_buried_thermal_without_ht": "needs_data",
        "reviewed_buried_thermal_requires_ht": "explicit_import_error",
        "unreviewed_frame_without_structural_solver": "needs_data",
        "reviewed_frame_requires_structural_solver": "explicit_import_error",
        "unresolved_thermal_or_pressure_contribution_without_optional_engines": "needs_data",
        "thermal_strain_preparation_requires_material_owner": "explicit_import_error",
        "noninitiation_toughness_without_material_owner": "needs_data",
        "mode_i_requires_material_owner": "explicit_import_error",
        "unresolved_pressure_section_without_material_engine": "needs_data",
        "pressure_section_requires_material_owner": "explicit_import_error",
        "control_and_instrument_contracts_without_native_engine": "passed",
        "pid_requires_native_owner": "explicit_import_error",
        "transmitter_requires_native_owner": "explicit_import_error",
        "calibrated_tank_mass_without_fluids_or_coolprop": "passed",
        "missing_tank_observation_without_optional_geometry": "needs_data",
        "native_tank_geometry_requires_fluids": "explicit_import_error",
        "equipment_exchange_without_owner_or_scipy": "passed",
        "release_contract_without_owner_or_prism": "passed",
        "inspection_contracts_without_optional_reader_or_solver": "passed",
        "inspection_planning_contract_without_materials_or_solver": "passed",
        "inspection_planning_requires_material_engine": "explicit_import_error",
        "nde_source_reader_and_missing_window_without_optional_engines": "passed",
        "nde_metrology_requires_material_engine": "explicit_import_error",
        "water_quality_contract_without_wntr": "passed",
        "inverse_contracts_without_scipy_or_wntr": "passed",
        "acoustic_contracts_without_scipy_or_pandas": "passed",
        "risk_contracts_without_hra_or_prism": "passed",
        "human_response_clock_contract_without_hra": "passed",
        "human_response_requires_hra": "explicit_import_error",
        "consequence_contracts_without_prism_or_slabx": "passed",
        "valve_motion_contracts_without_owner_or_scipy": "passed",
        "unresolved_fatigue_without_material_or_rainflow": "passed",
        "resolved_fatigue_requires_material_engine": "explicit_import_error",
        "gas_elevation_contract_without_native_or_thermo": "out_of_scope",
        "gas_physics_requires_native_engine": "explicit_import_error",
        "scheduling_contract_and_grid_without_ortools": "passed",
        "scheduling_requires_solver": "explicit_import_error",
        "acoustic_transient_without_optional_dependencies": "passed",
        "missing_optional_dependency": "explicit_import_error",
        "passed": True,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
