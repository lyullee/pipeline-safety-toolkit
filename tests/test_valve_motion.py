"""Motion-provider policy and source/clock binding, without duplicating native actuator mechanics."""

from types import SimpleNamespace

import pytest

from pipeline_integrity.contracts import RunContext
from pipeline_integrity.valve_motion import (
    ConductanceCurve,
    ValveCommand,
    ValveMotionRequest,
    check_liquid_motion_binding,
    liquid_valve_from_motion,
    motion_frames,
    simulate_valve_motion,
)

CONTEXT = RunContext(asset_id="V", scenario_id="response", clock_id="elapsed", origin="measured")
NETWORK = CONTEXT.model_copy(update={"asset_id": "network", "origin": "synthetic"})
PROVIDER = {"id": "motion-policy-spy", "version": "test"}


def request(**changes):
    fields = dict(
        component_id="V",
        initial_opening=1.0,
        full_stroke_time_s=1.0,
        sample_time_s=(0.0, 0.25, 0.5, 0.75, 1.0),
        commands=(ValveCommand(id="close", time_s=0.0, target_opening=0.0, evidence_id="observed:command"),),
        conductance=ConductanceCurve(
            opening=(0.0, 1.0), effective_area_m2=(0.0, 0.002), parameter_source_id="authored:conductance"
        ),
    )
    return ValveMotionRequest(**(fields | changes))


def actor_factory(_request):
    values = iter((0.75, 0.5, 0.25, 0.0))
    actor = SimpleNamespace(opening=1.0, target_opening=1.0)

    def advance(_dt):
        actor.opening = next(values)
        return actor.opening

    actor.advance = advance
    actor.set_target_opening = lambda target: setattr(actor, "target_opening", target)
    return actor


def test_provider_positions_curve_and_input_origin_remain_distinct():
    result = simulate_valve_motion(request(), CONTEXT, factory=actor_factory, provider=PROVIDER)
    assert result.opening == (1.0, 0.75, 0.5, 0.25, 0.0)
    assert result.effective_area_m2 == pytest.approx((0.002, 0.0015, 0.001, 0.0005, 0.0))
    assert result.command_records[0]["completion_bracket_s"] == [0.75, 1.0]
    assert result.context.origin == "measured" and result.output_origin == "simulation"
    assert result.kinematics_only and not result.hydraulic_or_field_validation
    result.check_integrity()
    frames = list(motion_frames(result))
    assert len(frames) == 5 and frames[0].origin == "simulation"
    assert frames[0].units == {"opening": "1", "target_opening": "1", "effective_CdA": "m2"}


def test_target_commands_cannot_teleport_physical_position():
    def factory(spec):
        actor = actor_factory(spec)
        actor.set_target_opening = lambda target: (
            setattr(actor, "target_opening", target),
            setattr(actor, "opening", target),
        )
        return actor

    with pytest.raises(ValueError, match="changed physical position"):
        simulate_valve_motion(request(), CONTEXT, factory=factory, provider=PROVIDER)


@pytest.mark.parametrize("reply", [True, 1.5, float("nan")])
def test_inconsistent_advance_replies_are_rejected(reply):
    def factory(spec):
        actor = actor_factory(spec)
        actor.advance = lambda dt: reply
        return actor

    with pytest.raises(ValueError, match="inconsistent actuator state"):
        simulate_valve_motion(request(), CONTEXT, factory=factory, provider=PROVIDER)


def test_motion_binding_checks_component_clock_curve_and_horizon():
    result = simulate_valve_motion(request(), CONTEXT, factory=actor_factory, provider=PROVIDER)
    valve = liquid_valve_from_motion(
        result, start_node="A", end_node="B", initial_volume_flow_m3_s=0.0, network_context=NETWORK
    )
    check_liquid_motion_binding(valve, NETWORK, 1.0)
    with pytest.raises(ValueError, match="cover the liquid analysis horizon"):
        check_liquid_motion_binding(valve, NETWORK, 1.01)
    with pytest.raises(ValueError, match="shared scenario and clock"):
        liquid_valve_from_motion(
            result,
            start_node="A",
            end_node="B",
            initial_volume_flow_m3_s=0.0,
            network_context=NETWORK.model_copy(update={"clock_id": "different"}),
        )
    valve.evidence["motion"]["opening"][0] = 0.5
    with pytest.raises(ValueError, match="command/curve evidence changed"):
        check_liquid_motion_binding(valve, NETWORK, 1.0)


def test_command_and_curve_inputs_do_not_accept_ambiguous_order_or_extrapolation():
    with pytest.raises(ValueError, match="strictly increasing"):
        request(sample_time_s=(0.0, 0.5, 0.25))
    command = ValveCommand(id="other", time_s=0.0, target_opening=0.5, evidence_id="source")
    with pytest.raises(ValueError, match="ordered clock"):
        request(commands=(*request().commands, command))
    with pytest.raises(ValueError, match="spans openings"):
        ConductanceCurve(opening=(0.0, 0.5), effective_area_m2=(0.0, 0.001), parameter_source_id="source")
    with pytest.raises(ValueError, match="sample budget"):
        request(maximum_samples=2)
