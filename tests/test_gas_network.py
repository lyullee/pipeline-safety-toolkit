"""Gas exchange qualification and topology policy, without reproducing the native solver."""

import copy
from dataclasses import replace

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.gas_network import (
    GasBoundary,
    GasJunction,
    GasNetwork,
    GasPipe,
    NativeGasRun,
    solve_gas_network,
)

CONTEXT = RunContext(
    asset_id="gas",
    scenario_id="policy",
    clock_id="steady",
    origin="measured",
    evidence=(EvidenceRef(id="declared", citation="Policy spy, no physical validation", origin="synthetic"),),
)


def case(load=False):
    return GasNetwork(
        fluid="Nitrogen",
        property_basis="spy_reference",
        parameter_source_id="declared",
        boundaries=(GasBoundary(id="a", pressure_pa_abs=100100.0, temperature_k=300.0),)
        + (() if load else (GasBoundary(id="b", pressure_pa_abs=100000.0, temperature_k=300.0),)),
        junctions=(
            GasJunction(
                id="b", initial_pressure_pa_abs=100000.0, initial_temperature_k=300.0, withdrawal_kg_s=0.001
            ),
        )
        if load
        else (),
        pipes=(
            GasPipe(
                id="p",
                start_node="a",
                end_node="b",
                length_m=10.0,
                diameter_m=0.1,
                roughness_m=0.0,
                segments=1,
            ),
        ),
    )


def state(pressure):
    return dict(
        fluid="Nitrogen",
        backend="HEOS",
        pressure_pa_abs=pressure,
        temperature_k=300.0,
        density_kg_m3=1.0,
        enthalpy_j_kg=100.0,
        entropy_j_kg_k=0.0,
        phase="gas",
        viscosity_pa_s=1e-5,
        cp_j_kg_k=1000.0,
        cv_j_kg_k=700.0,
        conductivity_w_m_k=0.02,
        speed_of_sound_m_s=300.0,
        provider_version="policy-spy",
        provider_revision="policy-spy",
    )


class Spy:
    def __init__(self, change=None):
        self.change = change

    def solve(self, spec):
        states = {"a": state(100100.0), "b": state(100000.0)}
        flow = dict(
            name="s",
            left="a",
            right="b",
            mass_flow_kg_s=0.001,
            transmitted_specific_enthalpy_J_kg=100.0,
            momentum_residual_Pa=0.0,
        )
        flows = {"s": flow}
        loads = {}
        if any(j.withdrawal_kg_s > 0 for j in spec.junctions):
            flows["load"] = dict(
                name="load",
                left="b",
                right="terminal",
                mass_flow_kg_s=0.001,
                transmitted_specific_enthalpy_J_kg=100.0,
                momentum_residual_Pa=None,
            )
            loads["b"] = "load"
        solution = dict(
            node_states={
                n: dict(pressure_Pa=s["pressure_pa_abs"], specific_enthalpy_J_kg=s["enthalpy_j_kg"])
                for n, s in states.items()
            },
            link_flows=flows,
            pressure_solver_success=True,
            pressure_optimizer_success=True,
            enthalpy_solver_success=True,
            junction_conservation_success=True,
        )
        run = NativeGasRun(
            digest(spec),
            solution,
            states,
            {"s": copy.deepcopy(states["b"])},
            {"p": ["s"]},
            loads,
            {"owner": "policy-spy"},
        )
        return run if self.change is None else self.change(run)


def test_closed_snapshot_is_simulation_and_receipt_detects_nested_changes():
    result = solve_gas_network(case(), CONTEXT, backend=Spy())
    assert result.status == "computed" and result.result_origin == "simulation"
    assert result.checks["global_closed"] and not result.total_energy_or_field_validation
    result.check_integrity()
    result.native["solution"]["link_flows"]["s"]["mass_flow_kg_s"] = 0.0
    with pytest.raises(ValueError, match="changed"):
        result.check_integrity()


@pytest.mark.parametrize("problem", ["native_stop", "momentum", "energy", "load_mass"])
def test_native_success_cannot_override_independent_closure(problem):
    def change(run):
        if problem == "native_stop":
            run.solution["pressure_optimizer_success"] = False
        elif problem == "momentum":
            run.solution["link_flows"]["s"]["momentum_residual_Pa"] = None
        elif problem == "energy":
            run.solution["link_flows"]["s"]["transmitted_specific_enthalpy_J_kg"] = 1100.0
            run.transmitted_states["s"]["enthalpy_j_kg"] = 1100.0
        else:
            run.solution["link_flows"]["s"]["mass_flow_kg_s"] = 0.002
        return run

    result = solve_gas_network(case(load=problem == "load_mass"), CONTEXT, backend=Spy(change))
    assert result.status == "withheld"


@pytest.mark.parametrize("problem", ["mach", "density", "phase"])
def test_model_domain_is_separate_from_optimizer_success(problem):
    def change(run):
        if problem == "mach":
            run.solution["link_flows"]["s"]["mass_flow_kg_s"] = 1.0
        elif problem == "density":
            run.transmitted_states["s"]["density_kg_m3"] = 0.5
        else:
            run.transmitted_states["s"]["phase"] = "liquid"
        return run

    assert solve_gas_network(case(), CONTEXT, backend=Spy(change)).status == "out_of_scope"


def test_unknown_sound_speed_is_not_zero_or_fabricated():
    def change(run):
        run.transmitted_states["s"]["speed_of_sound_m_s"] = None
        return run

    assert solve_gas_network(case(), CONTEXT, backend=Spy(change)).status == "withheld"


@pytest.mark.parametrize("problem", ["identity", "topology", "state", "load", "request", "fluid"])
def test_changed_native_contract_is_rejected(problem):
    def change(run):
        if problem == "request":
            return replace(run, specification_digest="changed")
        if problem == "identity":
            run.states["a"]["temperature_k"] = 301.0
        elif problem == "topology":
            run.solution["link_flows"]["s"]["left"] = "b"
        elif problem == "state":
            run.transmitted_states["s"]["enthalpy_j_kg"] = 101.0
        elif problem == "load":
            run.solution["link_flows"]["load"]["mass_flow_kg_s"] = 0.002
        else:
            run.transmitted_states["s"]["fluid"] = "Hydrogen"
        return run

    with pytest.raises(ValueError):
        solve_gas_network(case(load=problem == "load"), CONTEXT, backend=Spy(change))


def test_declared_load_and_enthalpy_reference_shift_preserve_budgets():
    def change(run):
        for s in run.states.values():
            s["enthalpy_j_kg"] += 1e6
        for s in run.transmitted_states.values():
            s["enthalpy_j_kg"] += 1e6
        for s in run.solution["node_states"].values():
            s["specific_enthalpy_J_kg"] += 1e6
        for f in run.solution["link_flows"].values():
            f["transmitted_specific_enthalpy_J_kg"] += 1e6
        return run

    a = solve_gas_network(case(load=True), CONTEXT, backend=Spy())
    b = solve_gas_network(case(load=True), CONTEXT, backend=Spy(change))
    assert a.status == b.status == "computed"
    assert a.checks["global_static_energy_residual_w"] == b.checks["global_static_energy_residual_w"] == 0.0


def test_elevation_needs_the_missing_owner_energy_model_without_importing_it():
    spec = case()
    b = spec.boundaries[1].model_copy(update={"elevation_m": 1.0})
    result = solve_gas_network(spec.model_copy(update={"boundaries": (spec.boundaries[0], b)}), CONTEXT)
    assert result.status == "out_of_scope" and result.native is None


def test_graph_resource_budget_and_missing_evidence():
    spec = case()
    with pytest.raises(ValueError, match="segment budget"):
        solve_gas_network(
            spec.model_copy(
                update={
                    "maximum_segments": 1,
                    "pipes": spec.pipes + (spec.pipes[0].model_copy(update={"id": "parallel"}),),
                }
            ),
            CONTEXT,
            backend=Spy(),
        )
    with pytest.raises(ValueError, match="budget"):
        solve_gas_network(
            spec.model_copy(
                update={"maximum_nodes": 2, "pipes": (spec.pipes[0].model_copy(update={"segments": 4}),)}
            ),
            CONTEXT,
            backend=Spy(),
        )
    with pytest.raises(ValueError, match="evidence"):
        solve_gas_network(spec, CONTEXT.model_copy(update={"evidence": ()}), backend=Spy())


def test_native_evaluation_failure_is_withheld():
    class Failed:
        def solve(self, _):
            raise RuntimeError("conversion budget exhausted")

    result = solve_gas_network(case(), CONTEXT, backend=Failed())
    assert result.status == "withheld" and "budget" in result.notes[0]


def test_zero_storage_stagnant_junction_does_not_acquire_a_temperature_from_its_guess():
    spec = case(load=True)
    spec = spec.model_copy(
        update={"junctions": (spec.junctions[0].model_copy(update={"withdrawal_kg_s": 0.0}),)}
    )

    def change(run):
        run.solution["link_flows"]["s"]["mass_flow_kg_s"] = 0.0
        return run

    result = solve_gas_network(spec, CONTEXT, backend=Spy(change))
    assert result.status == "withheld" and result.checks["junction_mass_closed"]
    assert result.checks["junction_static_energy_closed"]
    assert result.checks["nodes_without_resolved_inflow"] == ["b"]


def test_pipe_flow_exchange_summary_uses_declared_orientation_and_units():
    result = solve_gas_network(case(), CONTEXT, backend=Spy())
    summary = result.checks["pipe_flow_summary"]["p"]
    assert summary["start_node"] == "a" and summary["end_node"] == "b"
    assert summary["mass_flow_signed_kg_s"] == 0.001
    assert summary["flow_direction"] == "start_to_end"
    assert summary["segment_link_ids"] == ["s"]
    assert summary["segment_mass_continuity"]
    assert result.checks["pipe_segment_mass_continuity"]


def test_inconsistent_segment_mass_flow_is_withheld_from_exchange_summary():
    spec = case().model_copy(update={"pipes": (case().pipes[0].model_copy(update={"segments": 2}),)})

    class SplitSpy(Spy):
        def solve(self, requested):
            run = super().solve(requested)
            flow = run.solution["link_flows"]["s"]
            run.solution["link_flows"] = {
                "s0": {**flow, "name": "s0", "left": "a", "right": "mid"},
                "s1": {**flow, "name": "s1", "left": "mid", "right": "b", "mass_flow_kg_s": 0.002},
            }
            run.solution["node_states"]["mid"] = {"pressure_Pa": 100050.0, "specific_enthalpy_J_kg": 100.0}
            run.states["mid"] = state(100050.0)
            transmitted = {"s0": copy.deepcopy(run.states["mid"]), "s1": copy.deepcopy(run.states["b"])}
            return replace(run, transmitted_states=transmitted, pipe_links={"p": ["s0", "s1"]})

    result = solve_gas_network(spec, CONTEXT, backend=SplitSpy())
    summary = result.checks["pipe_flow_summary"]["p"]
    assert result.status == "withheld"
    assert not result.checks["pipe_segment_mass_continuity"]
    assert summary["mass_flow_signed_kg_s"] is None
    assert summary["flow_direction"] == "unresolved"


def test_native_segment_endpoints_that_break_declared_pipe_direction_are_rejected():
    spec = case().model_copy(update={"pipes": (case().pipes[0].model_copy(update={"segments": 2}),)})

    class ReverseSplitSpy(Spy):
        def solve(self, requested):
            run = super().solve(requested)
            flow = run.solution["link_flows"]["s"]
            run.solution["link_flows"] = {
                "s0": {**flow, "name": "s0", "left": "mid", "right": "a", "mass_flow_kg_s": -0.001},
                "s1": {**flow, "name": "s1", "left": "b", "right": "mid", "mass_flow_kg_s": -0.001},
            }
            run.solution["node_states"]["mid"] = {"pressure_Pa": 100050.0, "specific_enthalpy_J_kg": 100.0}
            run.states["mid"] = state(100050.0)
            transmitted = {"s0": copy.deepcopy(run.states["a"]), "s1": copy.deepcopy(run.states["mid"])}
            return replace(run, transmitted_states=transmitted, pipe_links={"p": ["s0", "s1"]})

    with pytest.raises(ValueError, match="segment order or endpoints changed"):
        solve_gas_network(spec, CONTEXT, backend=ReverseSplitSpy())
