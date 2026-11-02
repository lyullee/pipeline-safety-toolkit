"""Whitelisted JSON jobs for adapter APIs; native engines retain their own semantics."""

import importlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator

from .contracts import Contract, Name, RunContext, digest, json_value
from .datasets import file_digest


@dataclass(frozen=True)
class AdapterSpec:
    id: str
    module: str
    inputs: str
    evaluate: str
    verify: str
    provider_roles: tuple[str, ...] = ()
    payload_models: tuple[tuple[str, str], ...] = ()


SPECS = (
    AdapterSpec("structural.dynamic_fracture_arrest", "dynamic_fracture", "DynamicFractureArrestRequest",
                "evaluate_dynamic_fracture_arrest", "verify_dynamic_fracture_arrest_record"),
    AdapterSpec("structural.dynamic_fracture", "dynamic_fracture", "DynamicFractureRequest",
                "evaluate_dynamic_fracture", "verify_dynamic_fracture_record"),
    AdapterSpec("transient.vapor_cavities", "vapor_cavity_moc", "VaporCavityMOCRequest",
                "evaluate_vapor_cavity_moc", "verify_vapor_cavity_moc_record"),
    AdapterSpec("structural.elastic_shell_ring", "elastic_shell_ring", "ElasticShellRingRequest",
                "evaluate_elastic_shell_ring", "verify_elastic_shell_ring_record"),
    AdapterSpec("risk.stochastic_process", "stochastic_process", "StochasticProcessRequest",
                "evaluate_stochastic_process", "verify_stochastic_process_record"),
    AdapterSpec("transport.hydrate_kinetics", "hydrate_kinetics", "HydrateKineticsRequest",
                "evaluate_hydrate_kinetics", "verify_hydrate_kinetics_record"),
    AdapterSpec("control.regulation_modes", "control_modes", "RegulationModesRequest",
                "evaluate_regulation_modes", "verify_regulation_modes_record"),
    AdapterSpec("transient.discrete_gas_cavities", "cavity_moc", "CavityMOCRequest",
                "evaluate_cavity_moc", "verify_cavity_moc_record"),
    AdapterSpec("instrument.fiber_interferometry", "fiber_interferometry", "FiberInterferometryRequest",
                "evaluate_fiber_interferometry", "verify_fiber_interferometry_record"),
    AdapterSpec("construction.elastic_pull", "elastic_pull", "ElasticPullRequest",
                "evaluate_elastic_pull", "verify_elastic_pull_record"),
    AdapterSpec("risk.process_response", "headspace_response", "HeadspaceResponseRequest",
                "evaluate_headspace_response", "verify_headspace_response_record",
                ("alarm_factory","alarm_provider","motion_factory","motion_provider","human_response_client")),
    AdapterSpec("transport.particle_settling", "particle_settling", "ParticleSettlingRequest",
                "evaluate_particle_settling", "verify_particle_settling_record"),
    AdapterSpec("economics.continuous_optimization", "continuous_optimization", "ContinuousOptimizationRequest",
                "evaluate_continuous_optimization", "verify_continuous_optimization_record", ("providers",)),
    AdapterSpec("integrity.inspection_evolution", "inspection_evolution", "InspectionEvolutionRequest",
                "evaluate_inspection_evolution", "verify_inspection_evolution_record", ("client","source_root")),
    AdapterSpec("network.gas_potential_path", "gas_potential_network", "GasPotentialPathRequest",
                "evaluate_gas_potential_path", "verify_gas_potential_path_record", ("property_provider",)),
    AdapterSpec("network.gas_potential_plenums", "gas_potential_network", "GasPotentialPlenumRequest",
                "evaluate_gas_potential_plenums", "verify_gas_potential_plenums_record"),
    AdapterSpec("construction.route_generation", "route_generation", "RouteGenerationRequest",
                "evaluate_route_generation", "verify_route_generation_record"),
    AdapterSpec("tanks.headspace_drainage", "headspace_drainage", "HeadspaceDrainageRequest",
                "evaluate_headspace_drainage", "verify_headspace_drainage_record"),
    AdapterSpec("release.shared_hem_inventory", "rupture_inventory", "RuptureInventoryRequest",
                "evaluate_rupture_inventory", "verify_rupture_inventory_record"),
    AdapterSpec("structural.component_stress", "component_stress", "ComponentStressRequest",
                "evaluate_component_stress", "verify_component_stress_record"),
    AdapterSpec("transient.pump_moc", "pump_moc_exchange", "PumpMOCRequest",
                "evaluate_pump_moc", "verify_pump_moc_record"),
    AdapterSpec("thermal.soil_moisture", "soil_moisture", "SoilMoistureRequest",
                "evaluate_soil_moisture", "verify_soil_moisture_record"),
    AdapterSpec("exchange.process_signals", "signal_exchange", "ProcessSignalRequest",
                "evaluate_process_signals", "verify_process_signals_record"),
    AdapterSpec("equipment.controlled_compressor", "compressor_control", "ControlledCompressorRequest",
                "evaluate_controlled_compressor", "verify_controlled_compressor_record"),
    AdapterSpec("workflow.fixed_point", "adapter_feedback", "AdapterFeedbackRequest",
                "evaluate_adapter_feedback", "verify_adapter_feedback_record", ("providers",)),
    AdapterSpec("transport.conservative_batch", "batch_transport_solver", "ConservativeBatchRequest",
                "evaluate_conservative_batch_transport", "verify_conservative_batch_transport_record"),
    AdapterSpec("equipment.compressor_dynamics", "compressor_dynamics", "CompressorDynamicsRequest",
                "evaluate_compressor_dynamics", "verify_compressor_dynamics_record"),
    AdapterSpec("thermal.frost_growth", "frost_growth", "FrostGrowthRequest",
                "evaluate_frost_growth", "verify_frost_growth_record"),
    AdapterSpec("transport.rotating_shaft", "rotating_dynamics", "RotatingShaftRequest",
                "evaluate_rotating_shaft", "verify_rotating_shaft_record"),
    AdapterSpec("thermal.spatial_phase_change", "spatial_phase_change", "SpatialPhaseRequest",
                "evaluate_spatial_phase_change", "verify_spatial_phase_change_record"),
    AdapterSpec("network.inventory_transfers", "inventory_transfers", "InventoryTransferRequest",
                "evaluate_inventory_transfers", "verify_inventory_transfer_record"),
    AdapterSpec("tanks.hysteretic_breathing", "hysteretic_tank", "HystereticTankRequest",
                "evaluate_hysteretic_tank", "verify_hysteretic_tank_record"),
    AdapterSpec("thermal.adaptive_heated_transport", "adaptive_heated_transport", "AdaptiveHeatedTransportRequest",
                "evaluate_adaptive_heated_transport", "verify_adaptive_heated_transport_record", ("property_provider",)),
    AdapterSpec("leak.acoustic_multipath", "acoustic_multipath", "AcousticMultipathRequest",
                "evaluate_acoustic_multipath", "verify_acoustic_multipath_record"),
    AdapterSpec("leak.acoustic_multipath_fit", "acoustic_multipath", "AcousticMultipathFitRequest",
                "evaluate_acoustic_multipath_fit", "verify_acoustic_multipath_fit_record"),
    AdapterSpec("instrument.gas_basis", "gas_basis", "GasBasisRequest",
                "evaluate_gas_basis", "verify_gas_basis_record"),
    AdapterSpec("instrument.meter_gas_basis", "gas_basis", "MeterGasBasisRequest",
                "evaluate_meter_gas_basis", "verify_meter_gas_basis_record"),
    AdapterSpec("risk.headspace_response", "headspace_response", "HeadspaceResponseRequest",
                "evaluate_headspace_response", "verify_headspace_response_record",
                ("alarm_factory","alarm_provider","motion_factory","motion_provider","human_response_client")),
    AdapterSpec("thermal.fluid_soil_feedback", "fluid_soil_feedback", "FluidSoilFeedbackRequest",
                "evaluate_fluid_soil_feedback", "verify_fluid_soil_feedback_record", ("client","owner_source_root")),
    AdapterSpec("network.energy_mixing", "energy_network", "EnergyNetworkRequest",
                "evaluate_energy_network", "verify_energy_network_record"),
    AdapterSpec("thermal.property_liquid_wall_transient", "liquid_thermal_chain", "PropertyLiquidThermalRequest",
                "evaluate_property_liquid_thermal_chain", "verify_property_liquid_thermal_chain_record", ("property_provider",)),
    AdapterSpec("instrument.clock_epochs", "clock_epochs", "ClockEpochRequest",
                "evaluate_clock_epochs", "verify_clock_epochs_record"),
    AdapterSpec("leak.acoustic_network_inverse", "acoustic_network", "AcousticNetworkInverseRequest",
                "evaluate_acoustic_network_inverse", "verify_acoustic_network_inverse_record"),
    AdapterSpec("structural.gap_contact", "gap_contact", "GapContactRequest",
                "evaluate_gap_contact_frame", "verify_gap_contact_frame_record"),
    AdapterSpec("knowledge.assistance", "knowledge_exchange", "KnowledgeRequest",
                "evaluate_knowledge", "verify_knowledge_adapter_record", ("client",)),
    AdapterSpec("transport.heated_slurry", "heated_slurry", "HeatedSlurryRequest",
                "evaluate_heated_slurry", "verify_heated_slurry_record", ("property_provider",),
                (("property_table","SlurryThermalTable"),)),
    AdapterSpec("leak.acoustic_network", "acoustic_network", "AcousticNetworkRequest",
                "evaluate_acoustic_network", "verify_acoustic_network_record"),
    AdapterSpec("structural.junction_pressure_momentum", "junction_loads", "JunctionLoadRequest",
                "evaluate_junction_loads", "verify_junction_load_record"),
    AdapterSpec("structural.thermal_mesh", "structural_thermal_mesh", "ThermalMeshRequest",
                "evaluate_thermal_mesh_frame", "verify_thermal_mesh_frame_record"),
    AdapterSpec("structural.thermal_section", "structural_thermal_section", "ThermalSectionRequest",
                "evaluate_thermal_section_frame", "verify_thermal_section_frame_record"),
    AdapterSpec("instrument.constrained_gls", "constrained_metrology", "ConstrainedGLSRequest",
                "evaluate_constrained_gls", "verify_constrained_gls_record"),
    AdapterSpec("instrument.constrained_gls_prediction", "constrained_metrology", "ConstrainedGLSPredictionRequest",
                "evaluate_constrained_gls_prediction", "verify_constrained_gls_prediction_record"),
    AdapterSpec("thermal.heated_operating_point", "heated_operating_point", "HeatedOperatingPointRequest",
                "evaluate_heated_operating_point", "verify_heated_operating_point_record", ("property_provider",)),
    AdapterSpec("thermal.phase_material", "phase_material", "PhaseMaterialRequest",
                "build_phase_material", "verify_phase_material_record"),
    AdapterSpec("instrument.reset_counter", "temporal_metrology", "ResetCounterRequest",
                "evaluate_reset_counter", "verify_reset_counter_record"),
    AdapterSpec("instrument.temporal_weighted_budget", "temporal_metrology", "TemporalWeightedRequest",
                "evaluate_temporal_weighted_budget", "verify_temporal_weighted_budget_record"),
    AdapterSpec("thermal.liquid_wall_transient", "liquid_thermal_chain", "LiquidThermalRequest",
                "evaluate_liquid_thermal_chain", "verify_liquid_thermal_chain_record"),
    AdapterSpec("leak.acoustic_multiband", "acoustic_multiband", "MultibandAcousticRequest",
                "evaluate_multiband_acoustics", "verify_multiband_acoustic_record"),
    AdapterSpec("tanks.quasi_steady_drainage", "tank_drainage", "DrainageRequest",
                "evaluate_tank_drainage", "verify_tank_drainage_record"),
    AdapterSpec("tanks.filling", "tank_filling", "TankFillRequest",
                "evaluate_tank_filling", "verify_tank_filling_record"),
    AdapterSpec("tanks.fill_headspace", "tank_filling", "FillHeadspaceRequest",
                "evaluate_fill_headspace", "verify_fill_headspace_record"),
    AdapterSpec("thermal.lumped_phase_storage", "lumped_phase_change", "PhaseStorageRequest",
                "evaluate_phase_storage", "verify_phase_storage_record"),
    AdapterSpec("structural.temperature_dependent_expansion", "thermal_expansion", "CurveFrameRequest",
                "evaluate_curve_thermal_frame", "verify_curve_thermal_frame_record", ("client",)),
    AdapterSpec("tanks.gas_headspace", "gas_headspace", "HeadspaceRequest",
                "evaluate_headspace", "verify_headspace_record"),
    AdapterSpec("transport.deposition_inventory", "deposition_inventory", "DepositInventoryRequest",
                "evaluate_deposit_inventory", "verify_deposit_inventory_record"),
    AdapterSpec("transport.deposition_impacts", "deposition_inventory", "DepositImpactRequest",
                "evaluate_deposit_impacts", "verify_deposit_impacts_record"),
    AdapterSpec(
        "construction.pull", "pulling", "PullingRequest", "evaluate_pulling", "verify_pulling_record"
    ),
    AdapterSpec(
        "leak.sensor_placement",
        "sensor_placement",
        "SensorPlacementRequest",
        "plan_sensor_placement",
        "verify_sensor_placement_record",
        ("backend",),
    ),
    AdapterSpec(
        "tanks.breathing",
        "tank_breathing",
        "TankBreathingRequest",
        "evaluate_tank_breathing",
        "verify_tank_breathing_record",
    ),
    AdapterSpec(
        "construction.route",
        "route_planning",
        "RoutePlanningRequest",
        "evaluate_routes",
        "verify_route_record",
    ),
    AdapterSpec(
        "leak.inverse_localization",
        "inverse_localization",
        "LocalizationRequest",
        "localize_leak",
        "verify_inverse_localization_record",
        ("provider",),
    ),
    AdapterSpec(
        "transport.pig_motion",
        "pig_motion",
        "PigMotionRequest",
        "evaluate_pig_motion",
        "verify_pig_motion_record",
    ),
    AdapterSpec(
        "economics.optimization",
        "operating_optimization",
        "OperatingOptimizationRequest",
        "optimize_operating_plans",
        "verify_operating_optimization_record",
    ),
    AdapterSpec(
        "leak.external_sensing",
        "external_sensing",
        "ExternalSensingRequest",
        "adapt_external_sensing",
        "verify_external_sensing_record",
    ),
    AdapterSpec(
        "thermal.heated_oil",
        "heated_transport",
        "HeatedTransportRequest",
        "evaluate_heated_transport",
        "verify_heated_transport_record",
        ("property_provider",),
    ),
    AdapterSpec(
        "structural.soil_pipe",
        "soil_pipe",
        "SoilPipeRequest",
        "evaluate_soil_pipe",
        "verify_soil_pipe_record",
    ),
    AdapterSpec(
        "transport.slurry",
        "slurry_transport",
        "SlurryTransportRequest",
        "evaluate_slurry_transport",
        "verify_slurry_transport_record",
    ),
    AdapterSpec(
        "structural.fracture_arrest",
        "fracture_arrest",
        "FractureArrestRequest",
        "assess_fracture_arrest",
        "verify_fracture_arrest_record",
        ("provider",),
        (("trajectory", "ArrestTrajectory"),),
    ),
    AdapterSpec(
        "transient.gas_nonisothermal",
        "gas_thermal_transient",
        "GasThermalTransientRequest",
        "evaluate_gas_thermal_transient",
        "verify_gas_thermal_transient_record",
    ),
    AdapterSpec(
        "structural.buckling",
        "buckling",
        "BucklingRequest",
        "evaluate_buckling",
        "verify_buckling_record",
        ("provider",),
    ),
    AdapterSpec(
        "transport.hydrate",
        "deposition_risk",
        "DepositionRequest",
        "assess_deposition",
        "verify_deposition_record",
        ("provider",),
        (("envelope", "DepositionEnvelope"),),
    ),
    AdapterSpec(
        "leak.negative_pressure_wave",
        "pressure_wave",
        "PressureWaveRequest",
        "evaluate_pressure_wave",
        "verify_pressure_wave_record",
    ),
    AdapterSpec(
        "equipment.compressor_map",
        "compressor_train",
        "CompressorTrainRequest",
        "evaluate_compressor_train",
        "verify_compressor_train_record",
    ),
    AdapterSpec(
        "thermal.phase_change",
        "phase_change_exchange",
        "PhaseChangeRequest",
        "evaluate_phase_change",
        "verify_phase_change_record",
    ),
)


class AdapterJob(Contract):
    schema_version: Literal["pipeline_integrity.adapter_job.v1"] = "pipeline_integrity.adapter_job.v1"
    id: Name
    adapter_id: Name
    parameters: dict[str, Any]
    context: RunContext
    payloads: dict[Name, dict[str, Any]] = Field(default_factory=dict)
    bindings: dict[Name, Name] = Field(default_factory=dict)

    @field_validator("parameters", "payloads")
    @classmethod
    def serializable_job_values(cls, value):
        return json_value(value)


def adapter_specs():
    """List static names only, without importing adapter or native engine modules."""
    return tuple(json_value(asdict(spec)) for spec in SPECS)


def _spec(identity):
    selected = [s for s in SPECS if s.id == identity]
    if len(selected) != 1:
        raise ValueError("unknown allowlisted adapter ID")
    return selected[0]


def _module(spec):
    return importlib.import_module("pipeline_integrity." + spec.module)


def describe_adapter(identity):
    spec = _spec(identity)
    module = _module(spec)
    return {
        "adapter": json_value(asdict(spec)),
        "parameters_schema": getattr(module, spec.inputs).model_json_schema(),
        "payload_schemas": {
            role: getattr(module, model).model_json_schema() for role, model in spec.payload_models
        },
        "provider_binding_meaning": "explicit caller-registered Python objects only; JSON cannot import code",
        "schema_generation_is_numerical_validation": False,
    }


def decode_adapter_json(document):
    """Decode one unambiguous finite JSON document without importing adapter code."""

    def pairs(entries):
        result = {}
        for key, value in entries:
            if key in result:
                raise ValueError("duplicate JSON object key: " + key)
            result[key] = value
        return result

    def constant(value):
        raise ValueError("nonfinite JSON number: " + value)

    return json_value(json.loads(document, object_pairs_hook=pairs, parse_constant=constant))


def _check_bytes(document, maximum):
    # Character count is a cheap lower bound before allocating the UTF-8 bytes.
    if len(document) > maximum or len(document.encode("utf-8")) > maximum:
        raise ValueError("job exceeds explicit byte budget before adapter import/execution")


def _json_document(value):
    return json.dumps(json_value(value), allow_nan=False, ensure_ascii=False, separators=(",", ":"))


def _job(value, maximum_job_bytes):
    if type(maximum_job_bytes) is not int or not 0 < maximum_job_bytes <= 1_000_000_000:
        raise ValueError("explicit positive job byte budget must not exceed 1 GB")
    if isinstance(value, AdapterJob):
        document = _json_document(value)
    elif isinstance(value, str):
        document = value
    else:
        document = _json_document(value)
    _check_bytes(document, maximum_job_bytes)
    decoded = decode_adapter_json(document)
    q = AdapterJob.model_validate_json(json.dumps(decoded, allow_nan=False, ensure_ascii=False))
    # A canonical stored job must remain verifiable with the same selected budget.
    _check_bytes(_json_document(q), maximum_job_bytes)
    return q


def _prepare(job, providers):
    spec = _spec(job.adapter_id)
    if set(job.bindings) - set(spec.provider_roles) or set(job.payloads) - {
        r for r, _ in spec.payload_models
    }:
        raise ValueError("job contains an undeclared provider role or auxiliary payload")
    if providers is not None and not isinstance(providers, dict):
        raise TypeError("provider registry must be an explicit alias-to-object dictionary")
    registry = {} if providers is None else providers
    native = {}
    for role, alias in job.bindings.items():
        if alias not in registry or registry[alias] is None:
            raise ValueError("provider alias is not registered by the caller: " + alias)
        native[role] = registry[alias]
    module = _module(spec)
    request = getattr(module, spec.inputs).model_validate_json(json.dumps(job.parameters, allow_nan=False))
    auxiliary = {
        role: getattr(module, model).model_validate_json(json.dumps(job.payloads[role], allow_nan=False))
        for role, model in spec.payload_models
        if role in job.payloads
    }
    if auxiliary and getattr(request, "source_mode", None) == "provider":
        raise ValueError("provider mode cannot silently substitute an auxiliary reviewed payload")
    return spec, module, request, native, auxiliary


def _identity(spec, module):
    return {
        "adapter": json_value(asdict(spec)),
        "job_api_sha256": file_digest(Path(__file__)),
        "adapter_module_sha256": file_digest(Path(module.__file__)),
        "shared_source_sha256": {
            n: file_digest(Path(__file__).with_name(n + ".py")) for n in ("contracts", "datasets")
        },
    }


def run_adapter_job(job, *, providers=None, maximum_job_bytes=64_000_000):
    """Dispatch one explicit job; underlying status and engineering scope are preserved."""
    q = _job(job, maximum_job_bytes)
    spec, module, request, native, auxiliary = _prepare(q, providers)
    identity = _identity(spec, module)
    result = getattr(module, spec.evaluate)(request, q.context, **native, **auxiliary)
    if not isinstance(result, dict):
        raise ValueError("allowlisted adapter must return its structured result record")
    if _identity(spec, module) != identity:
        raise ValueError("adapter-job source identity changed during evaluation")
    body = {
        "schema": "pipeline_integrity.adapter_job_result.v1",
        "job": q.model_dump(mode="json"),
        "result": json_value(result),
        "identity": identity,
        "engineering_qualification_conferred_by_dispatch": False,
    }
    return dict(body, record_digest=digest(body))


def verify_adapter_job_record(record, *, job=None, providers=None, maximum_job_bytes=64_000_000):
    """Use the original adapter's replay semantics, including its native telemetry policy."""
    json_value(record)
    if (
        not isinstance(record, dict)
        or set(record)
        != {
            "schema",
            "job",
            "result",
            "identity",
            "engineering_qualification_conferred_by_dispatch",
            "record_digest",
        }
        or record.get("schema") != "pipeline_integrity.adapter_job_result.v1"
        or record.get("record_digest") != digest({k: v for k, v in record.items() if k != "record_digest"})
    ):
        raise ValueError("adapter-job record seal/schema changed")
    if not isinstance(record.get("result"), dict):
        raise ValueError("adapter-job result must retain a structured adapter record")
    q = _job(record["job"], maximum_job_bytes)
    if job is not None and _job(job, maximum_job_bytes) != q:
        raise ValueError("caller job differs from recorded input")
    spec, module, request, native, auxiliary = _prepare(q, providers)
    identity = _identity(spec, module)
    if (
        record["identity"] != identity
        or record.get("engineering_qualification_conferred_by_dispatch") is not False
    ):
        raise ValueError("adapter-job source/identity or scope changed")
    result = record["result"]
    for role, value in auxiliary.items():
        retained = result.get(role)
        if retained is not None and digest(retained) != digest(value):
            raise ValueError("retained auxiliary payload differs from the explicit job")
    checked = getattr(module, spec.verify)(result, request=request, context=q.context, **native)
    if _identity(spec, module) != identity:
        raise ValueError("adapter-job source identity changed during verification")
    if checked is not True and not (isinstance(checked, dict) and checked.get("valid") is True):
        raise ValueError("original adapter replay did not validate the record")
    return {
        "valid": True,
        "adapter_verification": json_value(checked),
        "meaning": "unsigned current-source replay; no execution identity or engineering authorization",
    }
