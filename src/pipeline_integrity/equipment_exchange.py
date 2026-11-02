"""Construct and call owner-supplied equipment without copying hydraulic or thermodynamic engines."""

import hashlib
import inspect
import math
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, field_validator

from .contracts import Contract, Name, Nonnegative, Positive, RunContext, digest, json_value
from .models.pump_curves import ReferencePumpCurve
from .thermo import StateRequest


class PumpMapConversion(Contract):
    reference_curve: ReferencePumpCurve
    reference_density_kg_m3: Positive
    gravity_m_s2: Positive = 9.80665


def lh2dt_pump_map(conversion: PumpMapConversion):
    """Convert declared volumetric-flow/head knots to the owner's mass-flow/pressure-rise convention."""
    if not isinstance(conversion, PumpMapConversion):
        raise TypeError("PumpMapConversion is required")
    rho, gravity = conversion.reference_density_kg_m3, conversion.gravity_m_s2
    return {
        "native_parameter": "head_curve_points",
        "head_curve_points": [
            [rho * q, rho * gravity * h]
            for q, h in zip(
                conversion.reference_curve.volume_flow_m3_s, conversion.reference_curve.head_m, strict=True
            )
        ],
        "native_coordinate_units": ["kg/s", "Pa_difference"],
        "source_coordinate_units": ["m3/s", "m"],
        "reference_density_kg_m3": rho,
        "gravity_m_s2": gravity,
        "source_id": conversion.reference_curve.source_id,
        "conversion_digest": digest(conversion),
        "fluid_transfer_or_cavitation_qualification": False,
    }


class EquipmentRequest(Contract):
    model_id: Name
    parameters: dict[str, Any]
    parameter_source: Name
    parameter_policy: Literal["reviewed_fields_only", "owner_native"] = "reviewed_fields_only"

    @field_validator("parameters")
    @classmethod
    def finite_parameters(cls, value):
        return json_value(value)


class EquipmentBuildReport(Contract):
    schema_version: Literal["pipeline_integrity.equipment_build.v1"] = "pipeline_integrity.equipment_build.v1"
    owner: Literal["lh2dt_models"] = "lh2dt_models"
    status: Literal["ready", "needs_data", "rejected"]
    asset_id: Name
    model_id: Name
    parameter_digest: Name
    missing_parameters: tuple[str, ...]
    issues: tuple[str, ...]
    property_basis: Name
    fluid: Name
    context: RunContext
    physical_model_validated: Literal[False] = False
    parameter_policy: Literal["reviewed_fields_only", "owner_native"] = "reviewed_fields_only"
    unreviewed_parameter_names: tuple[str, ...] = ()
    owner_version: str | None = None
    owner_code_digest: str | None = None
    owner_code_digest_scope: str = "unavailable"


@dataclass(frozen=True)
class BuiltEquipment:
    native: Any
    report: EquipmentBuildReport


class EquipmentState(Contract):
    native: Any
    handle_digest: Name
    clock_id: Name
    time_s: Nonnegative


@dataclass(frozen=True)
class EquipmentStep:
    state: EquipmentState
    native_result: Any
    record: dict[str, Any]


class DynamicStepTolerance(Contract):
    mass_absolute_kg: Nonnegative
    energy_absolute_j: Nonnegative
    wall_temperature_absolute_k: Nonnegative
    relative: Nonnegative = Field(le=0.1)
    maximum_refinements: int = Field(default=6, ge=1, le=10)


class DynamicAdvanceError(RuntimeError):
    def __init__(self, diagnostics):
        super().__init__("owner dynamic integration did not meet declared successive-refinement agreement")
        self.diagnostics = diagnostics


def _native_json(result):
    return json_value(asdict(result) if is_dataclass(result) else result)


class LH2EquipmentClient:
    """Use the owner's explicit asset factory, with separate build and evaluation results."""

    TWO_PORT_MODELS = frozenset(
        {
            "pipe",
            "hem_pipe",
            "virtual_pump",
            "mapped_pump",
            "check_valve",
            "stroke_limited_commanded_valve",
            "pressure_relief_valve",
            "temperature_protection_valve",
        }
    )
    REVIEWED_OPTIONAL_PARAMETERS = {
        "pipe": (),
        "hem_pipe": (),
        "dynamic_hem_pipe": ("fluid_volume_m3",),
        "valve": ("closed_leakage_CdA_m2",),
        "stroke_limited_commanded_valve": ("initial_opening", "closed_leakage_CdA_m2"),
        "check_valve": (),
        "pressure_relief_valve": ("closed_leakage_CdA_m2",),
        "temperature_protection_valve": ("closed_leakage_CdA_m2",),
        "virtual_pump": ("reference_speed_rpm", "speed_rpm"),
        "mapped_pump": ("isentropic_efficiency", "efficiency_curve_points", "NPSHr_m", "npsh_curve_points"),
    }

    def __init__(
        self, *, fluid: str, property_basis: str, builder=None, properties=None, required_parameters=None
    ):
        if (
            fluid not in {"Hydrogen", "ParaHydrogen"}
            or not isinstance(property_basis, str)
            or not property_basis.strip()
        ):
            raise ValueError(
                "the inspected equipment adapter requires explicit hydrogen fluid and property basis"
            )
        self.owner_version, self.owner_code_digest, self.owner_code_digest_scope = None, None, "unavailable"
        if builder is None:
            import lh2dt
            from lh2dt.catalog import REQUIRED_PARAMETERS
            from lh2dt.component_factory import build_asset_model
            from lh2dt.properties import HydrogenProperties

            builder, required_parameters = build_asset_model, REQUIRED_PARAMETERS
            properties = HydrogenProperties(fluid)
            self.owner_version = getattr(lh2dt, "__version__", None)
            module_file = inspect.getsourcefile(builder)
            if module_file:
                package = Path(module_file).parent
                sources = sorted(package.rglob("*.py"))
                if sources:
                    fingerprint = hashlib.sha256()
                    for source in sources:
                        fingerprint.update(source.relative_to(package).as_posix().encode("utf-8"))
                        fingerprint.update(source.read_bytes())
                    self.owner_code_digest = fingerprint.hexdigest()
                    self.owner_code_digest_scope = "owner_package_python_sources_only"
        if properties is None or required_parameters is None or properties.fluid != fluid:
            raise ValueError("a custom factory requires matching properties and its actual parameter catalog")
        self.builder, self.properties, self.required_parameters = (
            builder,
            properties,
            dict(required_parameters),
        )
        self.fluid, self.property_basis = fluid, property_basis

    def models(self):
        return {
            key: {
                "required_parameters": tuple(value),
                "parameter_units": "owner-native field conventions; curves and rpm need their explicit owner schema",
                "native_unit_exceptions": {
                    name: "rpm"
                    for name in ("speed_rpm", "reference_speed_rpm")
                    if name in set(value) | set(self.REVIEWED_OPTIONAL_PARAMETERS.get(key, ()))
                },
                "supports_this_two_port_adapter": key in self.TWO_PORT_MODELS,
                "reviewed_optional_parameters": self.REVIEWED_OPTIONAL_PARAMETERS.get(key, ()),
            }
            for key, value in sorted(self.required_parameters.items())
        }

    def build(self, request: EquipmentRequest, context: RunContext):
        if not isinstance(request, EquipmentRequest) or not isinstance(context, RunContext):
            raise TypeError("EquipmentRequest and RunContext are required")
        known = set(self.required_parameters.get(request.model_id, ())) | set(
            self.REVIEWED_OPTIONAL_PARAMETERS.get(request.model_id, ())
        )
        unknown = tuple(sorted(set(request.parameters) - known))
        report_keywords = {
            "parameter_policy": request.parameter_policy,
            "unreviewed_parameter_names": unknown,
            "owner_version": self.owner_version,
            "owner_code_digest": self.owner_code_digest,
            "owner_code_digest_scope": self.owner_code_digest_scope,
        }
        if unknown and request.parameter_policy == "reviewed_fields_only":
            return BuiltEquipment(
                native=None,
                report=EquipmentBuildReport(
                    status="rejected",
                    asset_id=context.asset_id,
                    model_id=request.model_id,
                    parameter_digest=digest(request),
                    missing_parameters=(),
                    issues=("Unreviewed parameter names are not passed silently to the owner factory.",),
                    property_basis=self.property_basis,
                    fluid=self.fluid,
                    context=context.model_copy(deep=True),
                    **report_keywords,
                ),
            )
        result = self.builder(
            {
                "asset_id": context.asset_id,
                "component_model": request.model_id,
                "model_parameters": request.parameters,
                "source": request.parameter_source,
            },
            self.properties,
        )
        if result.asset_id != context.asset_id or result.component_model != request.model_id:
            raise ValueError("the owner factory changed the asset or model identity")
        status = "ready" if result.ready else "needs_data" if result.missing_parameters else "rejected"
        report = EquipmentBuildReport(
            status=status,
            asset_id=context.asset_id,
            model_id=request.model_id,
            parameter_digest=digest(request),
            missing_parameters=tuple(result.missing_parameters),
            issues=tuple(result.issues),
            property_basis=self.property_basis,
            fluid=self.fluid,
            context=context.model_copy(deep=True),
            **report_keywords,
        )
        return BuiltEquipment(native=result.component, report=report)

    def state(self, request: StateRequest):
        if request.fluid != self.fluid or request.backend != "HEOS":
            raise ValueError("equipment state fluid or backend differs from its explicit property provider")
        pair = frozenset(request.inputs)
        methods = {
            frozenset({"P", "T"}): ("from_pT", "P", "T"),
            frozenset({"P", "Hmass"}): ("from_ph", "P", "Hmass"),
            frozenset({"P", "Smass"}): ("from_ps", "P", "Smass"),
        }
        if pair not in methods:
            raise ValueError("this owner state adapter supports P/T, P/Hmass and P/Smass")
        method, a, b = methods[pair]
        state = getattr(self.properties, method)(request.inputs[a], request.inputs[b])
        if request.single_phase_required and state.quality is not None:
            raise ValueError("the requested equipment state must be single-phase")
        return state

    def _require_handle(self, handle: BuiltEquipment):
        if (
            not isinstance(handle, BuiltEquipment)
            or handle.report.status != "ready"
            or handle.native is None
            or handle.report.fluid != self.fluid
            or handle.report.property_basis != self.property_basis
        ):
            raise ValueError("use a ready component built with this fluid/property basis")

    def export_accident_history(self, handle: BuiltEquipment, request):
        """Export the inspected dynamic pipe accident protocol with clock and property checks."""
        from .release_exchange import _export_pipe_accident

        self._require_handle(handle)
        return _export_pipe_accident(self, handle, request)

    def evaluate_two_port(self, handle: BuiltEquipment, left: StateRequest, right: StateRequest):
        self._require_handle(handle)
        if handle.report.model_id not in self.TWO_PORT_MODELS:
            raise ValueError("this component requires a different owner-native invocation protocol")
        result = handle.native.evaluate(self.state(left), self.state(right))
        if not hasattr(result, "mass_flow_kg_s") or not hasattr(result, "outlet_specific_enthalpy_J_kg"):
            raise ValueError("the owner result did not provide the inspected two-port contract")
        payload = _native_json(result)
        owner_flags = {}
        for name in ("operating_point_physical_valid", "pressure_solver_success"):
            value = getattr(result, name, None)
            if isinstance(value, bool):
                owner_flags[name] = value
        return {
            "schema": "pipeline_integrity.equipment_result.v1",
            "status": "withheld" if any(value is False for value in owner_flags.values()) else "computed",
            "owner": "lh2dt_models",
            "build": handle.report.model_dump(mode="json"),
            "left_state_request": left.model_dump(mode="json"),
            "right_state_request": right.model_dump(mode="json"),
            "owner_result_type": type(result).__qualname__,
            "owner_result": payload,
            "owner_qualification_flags": owner_flags,
            "native_result_digest": digest(payload),
            "whole_system_or_field_validation": False,
        }

    def initialize_dynamic_pipe(
        self,
        handle: BuiltEquipment,
        initial: StateRequest,
        *,
        time_s: float,
        wall_temperature_k: float | None = None,
    ):
        self._require_handle(handle)
        if handle.report.model_id != "dynamic_hem_pipe":
            raise ValueError("this initializer requires the owner's dynamic_hem_pipe")
        if isinstance(time_s, bool) or not math.isfinite(time_s) or time_s < 0:
            raise ValueError("declare a finite nonnegative initial time in the context clock")
        native = handle.native.initialize(self.state(initial), wall_temperature_K=wall_temperature_k)
        return EquipmentState(
            native=native,
            handle_digest=digest(handle.report),
            clock_id=handle.report.context.clock_id,
            time_s=time_s,
        )

    def step_dynamic_pipe(
        self,
        handle: BuiltEquipment,
        state: EquipmentState,
        left: StateRequest,
        right: StateRequest,
        *,
        timestep_s: float,
    ):
        self._require_handle(handle)
        if handle.report.model_id != "dynamic_hem_pipe":
            raise ValueError("this stepping adapter requires the owner's dynamic_hem_pipe")
        if (
            not isinstance(state, EquipmentState)
            or state.handle_digest != digest(handle.report)
            or state.clock_id != handle.report.context.clock_id
        ):
            raise ValueError("dynamic state belongs to a different asset, model, parameter set or clock")
        if isinstance(timestep_s, bool) or not math.isfinite(timestep_s) or timestep_s <= 0:
            raise ValueError("declare finite nonnegative time and positive timestep in the context clock")
        if not math.isfinite(state.time_s + timestep_s) or not state.time_s + timestep_s > state.time_s:
            raise ValueError("the time increment cannot be represented in this elapsed clock")
        result = handle.native.step(state.native, self.state(left), self.state(right), timestep_s)
        payload = _native_json(result)
        record = {
            "schema": "pipeline_integrity.dynamic_equipment_step.v1",
            "owner": "lh2dt_models",
            "build": handle.report.model_dump(mode="json"),
            "time_start_s": state.time_s,
            "time_end_s": state.time_s + timestep_s,
            "clock_id": handle.report.context.clock_id,
            "state_before": _native_json(state.native),
            "owner_step": payload,
            "owner_step_digest": digest(payload),
            "residual_scope": "owner's end-state differential equations",
            "integrated_mass_energy_ledger_qualified": False,
            "field_validation": False,
        }
        following = EquipmentState(
            native=result.state,
            handle_digest=state.handle_digest,
            clock_id=state.clock_id,
            time_s=state.time_s + timestep_s,
        )
        return EquipmentStep(state=following, native_result=result, record=record)

    def advance_dynamic_pipe(
        self,
        handle: BuiltEquipment,
        state: EquipmentState,
        left: StateRequest,
        right: StateRequest,
        *,
        interval_s: float,
        tolerance: DynamicStepTolerance,
    ):
        """Refine calls to the owner's stateless pipe step; numerical agreement is distinct from physical validation."""
        if not isinstance(tolerance, DynamicStepTolerance):
            raise TypeError("DynamicStepTolerance is required")
        coarse = self.step_dynamic_pipe(handle, state, left, right, timestep_s=interval_s)
        history = []
        fields = {
            "mass_kg": tolerance.mass_absolute_kg,
            "internal_energy_J": tolerance.energy_absolute_j,
            "wall_temperature_K": tolerance.wall_temperature_absolute_k,
        }
        for refinement in range(1, tolerance.maximum_refinements + 1):
            substeps = 2**refinement
            following = state
            for _ in range(substeps):
                fine = self.step_dynamic_pipe(
                    handle, following, left, right, timestep_s=interval_s / substeps
                )
                following = fine.state
            errors = {
                name: abs(getattr(fine.state.native, name) - getattr(coarse.state.native, name))
                for name in fields
            }
            limits = {
                name: absolute
                + tolerance.relative
                * max(abs(getattr(fine.state.native, name)), abs(getattr(coarse.state.native, name)))
                for name, absolute in fields.items()
            }
            accepted = all(errors[name] <= limits[name] for name in fields)
            history.append(
                {
                    "substeps": substeps,
                    "absolute_differences": errors,
                    "agreement_limits": limits,
                    "accepted": accepted,
                }
            )
            if accepted:
                endpoint = fine.state.model_copy(update={"time_s": state.time_s + interval_s})
                return EquipmentStep(
                    state=endpoint,
                    native_result=fine.native_result,
                    record={
                        "schema": "pipeline_integrity.dynamic_equipment_advance.v1",
                        "status": "computed",
                        "build": handle.report.model_dump(mode="json"),
                        "time_start_s": state.time_s,
                        "time_end_s": endpoint.time_s,
                        "clock_id": state.clock_id,
                        "accepted_substeps": substeps,
                        "state_before": _native_json(state.native),
                        "state_after": _native_json(endpoint.native),
                        "tolerance": tolerance.model_dump(mode="json"),
                        "refinement_history": history,
                        "last_native_step": fine.record,
                        "independent_error_bound_or_field_validation": False,
                    },
                )
            coarse = fine
        raise DynamicAdvanceError(
            {
                "status": "withheld",
                "refinement_history": history,
                "asset_id": handle.report.asset_id,
                "clock_id": state.clock_id,
                "time_start_s": state.time_s,
                "requested_interval_s": interval_s,
                "tolerance": tolerance.model_dump(mode="json"),
            }
        )
