from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from pipeline_integrity.contracts import RunContext
from pipeline_integrity.equipment_exchange import (
    DynamicAdvanceError,
    DynamicStepTolerance,
    EquipmentRequest,
    EquipmentState,
    LH2EquipmentClient,
)
from pipeline_integrity.thermo import StateRequest


@dataclass(frozen=True)
class NativeResult:
    mass_flow_kg_s: float = 0.1
    outlet_specific_enthalpy_J_kg: float = 10.0


class NativeComponent:
    def evaluate(self, left, right):
        return NativeResult()


def client(native=None):
    def build(request, props):
        missing = ("diameter",) if request["model_parameters"].get("diameter") is None else ()
        return SimpleNamespace(
            asset_id=request["asset_id"],
            component_model=request["component_model"],
            component=None if missing else NativeComponent() if native is None else native,
            ready=not missing,
            missing_parameters=missing,
            issues=(),
        )

    properties = SimpleNamespace(fluid="ParaHydrogen", from_pT=lambda p, t: SimpleNamespace(quality=None))
    return LH2EquipmentClient(
        fluid="ParaHydrogen",
        property_basis="declared_reference",
        builder=build,
        properties=properties,
        required_parameters={"pipe": ("diameter",), "dynamic_hem_pipe": ("diameter",)},
    )


def context(asset="one"):
    return RunContext(asset_id=asset, scenario_id="interface", clock_id="elapsed", origin="synthetic")


def test_incomplete_owner_build_does_not_become_a_usable_component():
    engine = client()
    missing = engine.build(
        EquipmentRequest(model_id="pipe", parameters={}, parameter_source="declared"), context()
    )
    assert missing.native is None and missing.report.status == "needs_data"
    unknown = engine.build(
        EquipmentRequest(
            model_id="pipe", parameters={"diameter": 0.01, "silent_typo": 1.0}, parameter_source="declared"
        ),
        context(),
    )
    assert unknown.report.status == "rejected" and unknown.report.unreviewed_parameter_names == (
        "silent_typo",
    )
    state = StateRequest(fluid="ParaHydrogen", inputs={"P": 300000.0, "T": 20.0})
    with pytest.raises(ValueError, match="ready"):
        engine.evaluate_two_port(missing, state, state)
    with pytest.raises(ValueError):
        EquipmentRequest(model_id="pipe", parameters={"diameter": float("nan")}, parameter_source="declared")


def test_owner_two_port_result_retains_the_case_and_build_digest():
    engine = client()
    handle = engine.build(
        EquipmentRequest(model_id="pipe", parameters={"diameter": 0.01}, parameter_source="declared"),
        context(),
    )
    state = StateRequest(fluid="ParaHydrogen", inputs={"P": 300000.0, "T": 20.0})
    result = engine.evaluate_two_port(handle, state, state)
    assert result["owner_result"]["mass_flow_kg_s"] == 0.1
    assert result["build"]["context"]["clock_id"] == "elapsed"
    assert result["build"]["parameter_digest"] and not result["whole_system_or_field_validation"]
    with pytest.raises(ValueError, match="fluid"):
        engine.state(state.model_copy(update={"fluid": "Hydrogen"}))


def test_dynamic_state_cannot_be_attached_to_a_different_asset_or_clock():
    engine = client()
    handle = engine.build(
        EquipmentRequest(
            model_id="dynamic_hem_pipe", parameters={"diameter": 0.01}, parameter_source="declared"
        ),
        context(),
    )
    foreign = EquipmentState(native=object(), handle_digest="a" * 64, clock_id="other_clock", time_s=0.0)
    state = StateRequest(fluid="ParaHydrogen", inputs={"P": 300000.0, "T": 20.0})
    with pytest.raises(ValueError, match="different asset"):
        engine.step_dynamic_pipe(handle, foreign, state, state, timestep_s=0.001)


def test_refinement_agreement_and_failure_preserve_the_requested_clock():
    @dataclass(frozen=True)
    class NativeState:
        mass_kg: float = 1.0
        internal_energy_J: float = 10.0
        wall_temperature_K: float = 20.0

    @dataclass(frozen=True)
    class NativeStep:
        state: NativeState

    class EulerReference:
        def initialize(self, fluid, wall_temperature_K=None):
            return NativeState()

        def step(self, state, left, right, dt):
            return NativeStep(NativeState(mass_kg=state.mass_kg * (1 - dt)))

    engine = client(EulerReference())
    handle = engine.build(
        EquipmentRequest(
            model_id="dynamic_hem_pipe", parameters={"diameter": 0.01}, parameter_source="synthetic_ODE"
        ),
        context(),
    )
    request = StateRequest(fluid="ParaHydrogen", inputs={"P": 300000.0, "T": 20.0})
    initial = engine.initialize_dynamic_pipe(handle, request, time_s=3.0)
    tolerance = DynamicStepTolerance(
        mass_absolute_kg=0.002, energy_absolute_j=0.0, wall_temperature_absolute_k=0.0, relative=0.0
    )
    advanced = engine.advance_dynamic_pipe(
        handle, initial, request, request, interval_s=0.2, tolerance=tolerance
    )
    import math

    assert advanced.state.native.mass_kg == pytest.approx(math.exp(-0.2), abs=0.002)
    assert advanced.state.time_s == 3.2 and advanced.record["accepted_substeps"] > 2
    with pytest.raises(DynamicAdvanceError) as error:
        engine.advance_dynamic_pipe(
            handle,
            initial,
            request,
            request,
            interval_s=0.2,
            tolerance=tolerance.model_copy(update={"mass_absolute_kg": 1e-20, "maximum_refinements": 1}),
        )
    assert error.value.diagnostics["status"] == "withheld"


def test_pump_curve_conversion_declares_density_and_distinguishes_head_from_pressure():
    from pipeline_integrity.equipment_exchange import PumpMapConversion, lh2dt_pump_map
    from pipeline_integrity.models.pump_curves import ReferencePumpCurve

    curve = ReferencePumpCurve(
        volume_flow_m3_s=(0.0, 0.01, 0.02), head_m=(60.0, 40.0, 20.0), source_id="synthetic_curve"
    )
    mapped = lh2dt_pump_map(PumpMapConversion(reference_curve=curve, reference_density_kg_m3=1000.0))
    assert mapped["head_curve_points"][1] == pytest.approx([10.0, 392266.0])
    assert mapped["native_coordinate_units"] == ["kg/s", "Pa_difference"]
    assert mapped["conversion_digest"] and not mapped["fluid_transfer_or_cavitation_qualification"]
    with pytest.raises(ValueError):
        PumpMapConversion(reference_curve=curve, reference_density_kg_m3=0.0)
