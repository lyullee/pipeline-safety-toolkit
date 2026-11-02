from copy import deepcopy
from types import SimpleNamespace

import pytest

from pipeline_integrity.contracts import RunContext
from pipeline_integrity.equipment_exchange import EquipmentRequest, LH2EquipmentClient
from pipeline_integrity.release_exchange import PipeAccidentRequest, prism_release


def request(**changes):
    return PipeAccidentRequest(
        **{
            "event_id": "declared_leak",
            "port_id": "leak",
            "clock_id": "elapsed",
            "start_time_s": 123.0,
            "source_id": "synthetic_contract_case",
            "initial_pressure_pa_abs": 200000.0,
            "initial_temperature_k": 300.0,
            "wall_temperature_k": 300.0,
            "ambient_pressure_pa_abs": 101325.0,
            "ambient_temperature_k": 288.15,
            "failure_opening_diameter_m": 0.001,
            "discharge_coefficient": 0.8,
            "horizon_s": 2.0,
            "time_step_s": 2.0,
            "position_enu_m": (0.0, 0.0, 1.0),
            "direction_enu": (1.0, 0.0, 0.0),
            **changes,
        }
    )


def setup(export_mutation=lambda response: None):
    class Component:
        def export_accident_history(self, payload):
            response = {
                "provider_export_schema": "prism.external_accident_history.v1",
                "provider_source_digest": payload["provider_source_digest"],
                "provider_state_snapshot_digest": payload["state_snapshot_digest"],
                "data": {
                    "component_id": "pipe",
                    "port_id": "leak",
                    "event_id": "declared_leak",
                    "event_kind": "accidental_leak",
                    "fluid": "Hydrogen",
                    "phase_basis": "gas",
                    "duration_s": 2.0,
                    "ambient_pressure_pa_abs": 101325.0,
                    "available_mass_kg": 1.0,
                    "cumulative_mass_out_kg": 0.2,
                    "cumulative_static_enthalpy_out_j": 200.0,
                    "termination_basis": "synthetic_diagnostic_horizon",
                    "steps": [
                        {
                            "time_s": 0.0,
                            "mass_flow_kg_s": 0.1,
                            "specific_enthalpy_j_kg": 1000.0,
                            "pressure_pa_abs": 101325.0,
                            "temperature_k": 300.0,
                            "density_kg_m3": 1.0,
                        }
                    ],
                    "provider_meta": {"initial_mass_kg": 1.0, "final_mass_kg": 0.8},
                },
            }
            export_mutation(response)
            return response

    def builder(payload, properties):
        return SimpleNamespace(
            asset_id=payload["asset_id"],
            component_model=payload["component_model"],
            component=Component(),
            ready=True,
            missing_parameters=(),
            issues=(),
        )

    properties = SimpleNamespace(
        fluid="Hydrogen",
        from_ph=lambda p, h: SimpleNamespace(
            density_kg_m3=1.0, temperature_K=300.0, phase="gas", quality=None
        ),
    )
    client = LH2EquipmentClient(
        fluid="Hydrogen",
        property_basis="declared_native_reference",
        builder=builder,
        properties=properties,
        required_parameters={"dynamic_hem_pipe": ()},
    )
    handle = client.build(
        EquipmentRequest(model_id="dynamic_hem_pipe", parameters={}, parameter_source="contract_fixture"),
        RunContext(asset_id="pipe", scenario_id="declared", clock_id="elapsed", origin="measured"),
    )
    return client, handle


def test_release_clock_inventory_and_original_observation_origin_are_retained():
    client, handle = setup()
    history = client.export_accident_history(handle, request())
    assert history.status == "exchange_checked"
    assert history.audit["inventory_residual_kg"] == pytest.approx(0.0)
    assert history.audit["retained_context_end_time_s"] == 125.0
    assert history.provider_response["data"]["steps"][0]["time_s"] == 0.0
    assert history.build.context.origin == "measured"
    assert not history.physical_actuation_or_field_validation
    history.check_integrity()
    with pytest.raises(ValueError, match="clocks"):
        client.export_accident_history(handle, request(clock_id="other"))
    changed = history.model_copy(deep=True)
    changed.provider_response["data"]["cumulative_mass_out_kg"] = 0.1
    with pytest.raises(ValueError, match="changed"):
        changed.check_integrity()


@pytest.mark.parametrize(
    ("field", "value", "issue"),
    [
        ("fluid", "ParaHydrogen", "provider_fluid_mismatch"),
        ("event_id", "other", "provider_event_id_mismatch"),
        ("phase_basis", "two_phase", "provider_phase_history_shape_mismatch"),
        ("cumulative_mass_out_kg", 0.4, "left_held_mass_integral_mismatch"),
    ],
)
def test_invalid_owner_semantics_cannot_reach_a_receiving_release_builder(field, value, issue):
    client, handle = setup(lambda response: response["data"].update({field: value}))
    history = client.export_accident_history(handle, request())
    assert history.status == "withheld" and issue in history.issues
    with pytest.raises(ValueError, match="withheld"):
        prism_release(history, gas_adapter=lambda *args, **kwargs: pytest.fail("must not be called"))


def test_gas_branch_uses_vapour_state_and_keeps_unscaled_impact_coordinates():
    def mixed(response):
        data = response["data"]
        data["phase_basis"] = "two_phase"
        data["steps"] = [
            {
                "time_s": 0.0,
                "total_mass_flow_kg_s": 0.1,
                "gas_mass_flow_kg_s": 0.02,
                "ground_liquid_mass_flow_kg_s": 0.08,
                "airborne_liquid_mass_flow_kg_s": 0.0,
                "specific_enthalpy_j_kg": 1000.0,
                "gas_pressure_pa_abs": 101325.0,
                "gas_temperature_k": 20.0,
                "gas_density_kg_m3": 4.0,
                "droplet_class_outcomes": [{"impact_position_m": [1.0, 0.0, 0.0]}],
            }
        ]

    client, handle = setup(mixed)
    client.properties.from_ph = lambda p, h: SimpleNamespace(quality=0.2)
    client.properties.saturated_vapor = lambda p: SimpleNamespace(
        density_kg_m3=1.3, temperature_K=20.0, phase="twophase"
    )
    history = client.export_accident_history(
        handle,
        request(droplet_flight_time_s=0.2, impact_position_enu_m=(2.0, 0.0, 0.0), droplet_diameter_m=0.001),
    )
    assert history.status == "withheld"
    assert {"provider_changed_impact_position", "gas_branch_property_state_mismatch"} <= set(history.issues)


def test_declared_budget_and_isolation_evidence_are_required():
    for changes in (
        {"time_step_s": 0.001},
        {"direction_enu": (0.0, 0.0, 0.0)},
        {"isolation_time_s": 1.0},
        {"post_isolation_observation_s": 0.2},
        {"droplet_flight_time_s": 0.1},
    ):
        with pytest.raises(ValueError):
            request(**changes)
    client, handle = setup()
    history = client.export_accident_history(
        handle, request(time_step_s=1.0, isolation_time_s=1.0, post_isolation_observation_s=1.0)
    )
    assert "provider_clock_does_not_retain_isolation_event" in history.issues


def test_receiving_builder_cannot_relabel_property_or_promote_measurements_to_validated_predictions():
    client, handle = setup()
    history = client.export_accident_history(handle, request())
    received = {}

    class Packet:
        def __init__(self, payload):
            self.__dict__.update(payload)
            self.provider_export_schema = "prism.resolved_release.v1"
            self.property_basis = SimpleNamespace(
                fluid="Hydrogen", energy_reference="CoolProp-HEOS:Hydrogen:native"
            )
            self.duration_s = 2.0
            self.cumulative_mass_out_kg = 0.2

        def model_dump(self, *, mode):
            return {**received, "property_basis": vars(self.property_basis)}

    def adapter(response, **kwargs):
        received.update(deepcopy(kwargs))
        return Packet(kwargs)

    handoff = prism_release(history, gas_adapter=adapter)
    record = handoff.record()
    assert received["input_origin"] == "model_prediction"
    assert record["context"]["origin"] == "measured" and record["provider_time_origin_in_context_s"] == 123.0
    assert not record["initiating_frequency_or_consequence_calculated"]
    handoff.native.property_basis.fluid = "ParaHydrogen"
    with pytest.raises(ValueError, match="changed"):
        handoff.record()
