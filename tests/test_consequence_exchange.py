"""Policy spies exercise handoff boundaries; check_consequences.py runs the real physical chain."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from pipeline_integrity.consequence_exchange import (
    AnnualCondition,
    AnnualEvent,
    AnnualRiskRequest,
    CompressiblePlane,
    ConsequenceCase,
    PRISMConsequenceClient,
)
from pipeline_integrity.contracts import RunContext, digest
from pipeline_integrity.release_exchange import ReleaseHandoff
from pipeline_integrity.risk_exchange import RiskFrequency


class Object(SimpleNamespace):
    @classmethod
    def model_validate_json(cls, value):
        return json.loads(value, object_hook=lambda d: cls(**d))

    def model_dump(self, *, mode="json"):
        return json.loads(json.dumps(self, default=vars))

    def probabilities(self):
        return self.partition.model_dump()


class StubRelease(ReleaseHandoff):
    def record(self):
        packet = self.native.model_dump()
        if digest(packet) != self.native_digest:
            raise ValueError("test handoff changed")
        return {
            "native_packet": packet,
            "native_packet_digest": self.native_digest,
            "context": self.history.build.context.model_dump(mode="json"),
            "provider_time_origin_in_context_s": 10.0,
        }


def handoff():
    context = RunContext(asset_id="pipe", scenario_id="synthetic", clock_id="elapsed", origin="synthetic")
    packet = {
        "component_id": "pipe",
        "port_id": "exit",
        "event_id": "leak",
        "cumulative_mass_out_kg": 0.1,
        "provider_result_digest": "a" * 64,
        "property_basis": {"fluid": "Hydrogen"},
        "compressible_plane_handoff": None,
    }
    history = SimpleNamespace(
        build=SimpleNamespace(context=context), provider_response={"data": {"phase_basis": "gas"}}
    )
    return StubRelease(Object.model_validate_json(json.dumps(packet)), history, digest(packet))


def case(**changes):
    fields = dict(
        id="test_case",
        initiating_condition_id="release_initiated",
        evidence_ids=("authored:boundary_reference",),
        facility={
            "id": "facility",
            "configuration_origin": "synthetic",
            "receptors": [{"id": "R1", "position_enu_m": [1.0, 0.0, 1.0]}],
        },
        defaults={
            "weather": {"wind_from_bearing_deg": 270.0},
            "domain": {
                "cell_size_m": 1.0,
                "downwind_bounds_m": [-2.0, 2.0],
                "crosswind_bounds_m": [-2.0, 2.0],
                "height_bounds_m": [0.0, 2.0],
            },
            "evaluation_times_s": [0.0, 1.0],
            "ignition": {"partition": {"fire": 0.25, "none": 0.75}},
        },
    )
    return ConsequenceCase(**(fields | changes))


@pytest.fixture
def owner():
    class OwnerError(RuntimeError):
        pass

    state = SimpleNamespace(
        calls=0,
        plane_calls=0,
        annual_calls=[],
        error=None,
        alter=lambda d: None,
        plane_alter=lambda p: None,
        annual={
            "metric": "annual_individual_risk_rate",
            "units": "year^-1",
            "value": 0.002,
            "known_contribution": 0.002,
            "uncomputed_scenarios": [],
            "physical_validation_claim": False,
        },
    )

    def build_request(packet, defaults):
        return Object.model_validate_json(
            json.dumps(
                dict(defaults)
                | {
                    "resolved_release": packet.model_dump(),
                    "annual_frequency_per_year": None,
                    "initiation_intensity_per_s": None,
                }
            )
        )

    class Pipeline:
        def __init__(self, facility, backend):
            self.facility = facility

        def consequences(self, request):
            state.calls += 1
            if state.error:
                raise OwnerError(state.error)
            data = {
                "full_chain_executed": True,
                "physical_validation_claim": False,
                "source": {
                    "discharge_recalculated": False,
                    "equipment_dynamics_recalculated": False,
                    "provider_boundary": request.resolved_release.model_dump(),
                },
                "mass_ledger": {"released_kg": 0.1, "end_to_end_residual_kg": 0.0},
                "explosion": {"domain_truncated": False},
                "ignition_branch_probabilities": {"fire": 0.25, "none": 0.75},
                "receptors": [
                    {
                        "id": "R1",
                        "position_enu_m": [1.0, 0.0, 1.0],
                        "harm_given_observed_release_low_high": [0.2, 0.4],
                        "concentration_history": [
                            {"time_s": 0.0, "h2_mole_fraction": 0.0},
                            {"time_s": 1.0, "h2_mole_fraction": 0.05},
                        ],
                    }
                ],
            }
            state.alter(data)
            return {"data": data}

    def build_plane(*args, **kwargs):
        state.plane_calls += 1
        return SimpleNamespace(export={"plane": "declared"}, audit={"max_energy_residual_j_per_kg": 0.0})

    def attach_plane(packet, export):
        attached = packet.model_dump() | {"compressible_plane_handoff": {"from": "explicit_plane"}}
        state.plane_alter(attached)
        return attached, {"status": "attached"}

    def annual_risk(rows):
        state.annual_calls.append(rows)
        return deepcopy(state.annual)

    api = SimpleNamespace(
        Facility=Object,
        ConsequenceRequest=Object,
        build_request=build_request,
        Pipeline=Pipeline,
        BackendError=OwnerError,
        build_plane=build_plane,
        attach_plane=attach_plane,
        GasRelease=Object,
        AnnualScenario=Object,
        annual_risk=annual_risk,
    )
    client = PRISMConsequenceClient(
        None,
        api=api,
        properties=SimpleNamespace(fluid="Hydrogen"),
        provider={"id": "boundary-spy", "version": "test"},
    )
    return client, state


def annual_request(consequence, *, frequency=True):
    context = consequence.context
    source = {"annual_frequency_per_year": 0.01, "basis": "authored assumption"}
    rate = (
        RiskFrequency(
            id="leak",
            annual_frequency_per_year=0.01,
            condition_id="release_initiated",
            endpoint="loss_of_containment",
            evidence_ids=("assumed:loc",),
            context=context,
            source_payload=source,
            source_digest=digest(source),
        )
        if frequency
        else None
    )
    event = AnnualEvent(
        event_id="leak",
        frequency=rate,
        conditions=(
            AnnualCondition(
                id="condition",
                probability=1.0,
                occupancy_probability=0.5,
                occupancy_evidence_id="assumed:presence",
                consequence=consequence,
            ),
        ),
        partition_evidence_id="authored:partition",
        frequency_condition_independence_evidence_id="assumed:conditional_frequency",
    )
    return AnnualRiskRequest(
        assessment_id="study",
        events=(event,),
        receptor_ids=("R1",),
        initiating_event_partition_evidence_id="authored:one_event",
        time_scope_evidence_id="authored:finite_window",
    )


def test_conditional_receipt_preserves_source_clock_and_does_not_add_a_plane_or_frequency(owner):
    client, state = owner
    result = client.evaluate(handoff(), case())
    assert result.status == "computed_diagnostic" and state.plane_calls == 0
    assert result.source_record["provider_time_origin_in_context_s"] == 10.0
    assert result.native_request["annual_frequency_per_year"] is None
    assert result.native_request["occupancy_probability"] == 1.0
    assert result.context.origin == "synthetic" and not result.physical_validation_claim
    result.check_integrity()


@pytest.mark.parametrize(
    "change",
    ["source", "mass", "clock", "harm", "position", "frequency", "partition", "domain", "qualification"],
)
def test_native_misbindings_or_uncomputed_physics_are_withheld(owner, change):
    client, state = owner

    def alter(data):
        row = data["receptors"][0]
        if change == "source":
            data["source"]["provider_boundary"]["event_id"] = "different"
        elif change == "mass":
            data["mass_ledger"]["released_kg"] = 0.05
        elif change == "clock":
            row["concentration_history"][1]["time_s"] = 1.01
        elif change == "harm":
            row["harm_given_observed_release_low_high"] = [0.4, 0.2]
        elif change == "position":
            row["position_enu_m"][0] = 2.0
        elif change == "frequency":
            row["annual_current_weather_contribution_low_high"] = [0.02, 0.04]
        elif change == "partition":
            data["ignition_branch_probabilities"] = {"fire": 0.5, "none": 0.5}
        elif change == "domain":
            data["explosion"]["domain_truncated"] = True
        elif change == "qualification":
            data["physical_validation_claim"] = True

    state.alter = alter
    assert client.evaluate(handoff(), case()).status == "withheld"


def test_invalid_context_and_evaluation_budgets_stop_before_owner_execution(owner):
    client, state = owner
    with pytest.raises(ValueError, match="budget"):
        client.evaluate(handoff(), case(maximum_cells=1))
    far = case().facility
    far["receptors"][0]["position_enu_m"] = [1001.0, 0.0, 1.0]
    with pytest.raises(ValueError, match="absolute meteorological"):
        client.evaluate(handoff(), case(facility=far))
    with pytest.raises(ValueError, match="synthetic source"):
        client.evaluate(
            handoff(), case(facility=case().facility | {"configuration_origin": "field_confirmed"})
        )
    assert state.calls == 0


@pytest.mark.parametrize(
    "defaults", [{"annual_frequency_per_year": 0.1}, {"resolved_release": {}}, {"occupancy_probability": 0.5}]
)
def test_annual_weights_cannot_be_hidden_in_conditional_defaults(defaults):
    with pytest.raises(ValueError, match="initiating frequency|occupancy one"):
        case(defaults=defaults)


def test_native_backend_failure_is_retained_without_a_computed_value(owner):
    client, state = owner
    state.error = "native calculation failed"
    result = client.evaluate(handoff(), case())
    assert result.status == "withheld" and result.native_result["message"] == state.error


def test_expansion_is_explicit_and_cannot_change_the_original_packet_or_fluid(owner):
    client, state = owner
    plane = case(compressible_plane=CompressiblePlane(evidence_id="authored:explicit_plane"))
    result = client.evaluate(handoff(), plane)
    assert result.status == "computed_diagnostic" and state.plane_calls == 1
    state.plane_alter = lambda packet: packet.update(event_id="different")
    with pytest.raises(ValueError, match="original source packet"):
        client.evaluate(handoff(), plane)
    client.properties.fluid = "ParaHydrogen"
    with pytest.raises(ValueError, match="same source fluid"):
        client.evaluate(handoff(), plane)


def test_annual_accounting_delegates_each_bound_and_preserves_finite_window_scope(owner):
    client, state = owner
    result = client.annual_risk(annual_request(client.evaluate(handoff(), case())))
    assert result["metric"] == "annualized_declared_window_harm_rate"
    assert not result["complete_accident_or_facility_risk"]
    assert result["receptor_results"]["R1"]["value_low_high"] == [0.002, 0.002]
    assert len(state.annual_calls) == 4
    assert [rows[0].harm_probability_given_event for rows in state.annual_calls] == [0.2, 0.2, 0.4, 0.4]
    assert all(rows[0].occupancy_probability == 0.5 for rows in state.annual_calls)


def test_missing_frequency_is_not_zero_and_wrong_endpoints_cannot_become_loc(owner):
    client, state = owner
    consequence = client.evaluate(handoff(), case())
    state.annual.update(value=None, known_contribution=0.0, uncomputed_scenarios=["leak"])
    result = client.annual_risk(annual_request(consequence, frequency=False))
    assert (
        result["status"] == "needs_frequency" and result["receptor_results"]["R1"]["value_low_high"] is None
    )
    assert result["receptor_results"]["R1"]["uncomputed_harm_envelope_low_high"] is None
    request = annual_request(consequence)
    rate = request.events[0].frequency
    changed = RiskFrequency(
        **(rate.model_dump(exclude={"binding_digest"}) | {"endpoint": "protection_failure_combination"})
    )
    request = request.model_copy(
        update={"events": (request.events[0].model_copy(update={"frequency": changed}),)}
    )
    with pytest.raises(ValueError, match="loss-of-containment frequency"):
        client.annual_risk(request)


def test_changed_receipts_partitions_and_frequency_context_are_rejected(owner):
    client, _ = owner
    consequence = client.evaluate(handoff(), case())
    request = annual_request(consequence)
    event = request.events[0]
    bad = event.conditions[0].model_copy(update={"probability": 0.8})
    with pytest.raises(ValueError, match="sum to one"):
        client.annual_risk(
            request.model_copy(update={"events": (event.model_copy(update={"conditions": (bad,)}),)})
        )
    changed_context = consequence.context.model_copy(update={"asset_id": "different_pipe"})
    rate = event.frequency
    changed_rate = RiskFrequency(
        **(rate.model_dump(exclude={"binding_digest"}) | {"context": changed_context})
    )
    with pytest.raises(ValueError, match="context differ"):
        client.annual_risk(
            request.model_copy(update={"events": (event.model_copy(update={"frequency": changed_rate}),)})
        )
    consequence.source_record["native_packet"]["event_id"] = "different"
    with pytest.raises(ValueError, match="retained evidence changed"):
        client.annual_risk(request)


def test_uncomputed_conditions_keep_their_probability_weight_and_no_completed_value(owner):
    client, state = owner
    state.error = "unsupported physical condition"
    withheld = client.evaluate(handoff(), case())
    result = client.annual_risk(annual_request(withheld))
    assert result["status"] == "needs_consequence"
    assert result["uncomputed_conditions"] == ["leak/condition"]
    assert result["receptor_results"]["R1"]["value_low_high"] is None
    assert [rows[0].harm_probability_given_event for rows in state.annual_calls] == [0.0, 1.0, 0.0, 1.0]
    assert all(rows[0].annual_weather_probability == 1.0 for rows in state.annual_calls)
    state.annual.update(value=None, known_contribution=0.0, uncomputed_scenarios=["leak"])
    combined = client.annual_risk(annual_request(withheld, frequency=False))
    assert combined["status"] == "needs_frequency_and_consequence"
    assert combined["receptor_results"]["R1"]["uncomputed_harm_envelope_low_high"] is None


def test_initiating_condition_id_is_preserved_when_binding_a_frequency(owner):
    client, _ = owner
    result = client.evaluate(handoff(), case(initiating_condition_id="automatic_isolation_failed"))
    with pytest.raises(ValueError, match="initiating condition differ"):
        client.annual_risk(annual_request(result))
