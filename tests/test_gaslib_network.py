"""Synthetic boundaries for the evidence-gated GasLib owner adapter."""

import json
import zipfile

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.gaslib import read_gaslib
from pipeline_integrity.gaslib_network import (
    GasLibNetworkMapping,
    GasLibNodeState,
    GasLibPipeControl,
    GasLibNetworkRun,
    prepare_gaslib_network,
    solve_gaslib_network,
    verify_gaslib_network_run,
)
from pipeline_integrity.gas_network import NativeGasRun
from pipeline_integrity.contracts import digest

NETWORK = """<network xmlns="http://gaslib.zib.de/Gas" xmlns:f="http://gaslib.zib.de/Framework">
<!-- http://creativecommons.org/licenses/by/3.0/ -->
<f:nodes><source id="a"><height value="0" unit="m"/></source>
<sink id="b"><height value="0" unit="m"/></sink></f:nodes>
<f:connections><pipe id="p" from="a" to="b"><length value="4" unit="km"/>
<diameter value="100" unit="mm"/><roughness value="0.1" unit="mm"/></pipe></f:connections></network>"""
SCENARIO = """<boundaryValue xmlns="http://gaslib.zib.de/Gas">
<!-- http://creativecommons.org/licenses/by/3.0/ -->
<scenario id="s"><node id="a" type="entry"><flow bound="lower" value="1" unit="kg_per_s"/></node>
<node id="b" type="exit"><flow bound="upper" value="1" unit="kg_per_s"/></node></scenario>
</boundaryValue>"""


def make_archive(tmp_path, network=NETWORK):
    path = tmp_path / "synthetic.zip"
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("network.net", network)
        archive.writestr("scenario.scn", SCENARIO)
    return path


def make_inputs(path):
    source = read_gaslib(path)
    refs = []
    for evidence_id, label, sha, license_id in (
        ("archive", "archive", source["archive_sha256"], "CC-BY-3.0"),
        ("network", "network", source["network_member_sha256"], "CC-BY-3.0"),
        ("scenario", "scenario", source["scenario_member_sha256"], "CC-BY-3.0"),
    ):
        refs.append(EvidenceRef(id=evidence_id, citation=f"synthetic {label}", origin="synthetic", sha256=sha, license_id=license_id))
    for evidence_id in ("property", "composition", "pressure-reference", "nomination", "review", "pipe-control"):
        refs.append(EvidenceRef(id=evidence_id, citation=f"caller declaration {evidence_id}", origin="user_declared"))
    context = RunContext(asset_id="network-a", scenario_id="s", clock_id="steady-state", origin="user_declared", evidence=tuple(refs))
    mapping = GasLibNetworkMapping(
        asset_id="network-a", scenario_id="s", fluid="Methane", backend="HEOS", property_basis="CoolProp pure-fluid",
        parameter_source_id="property", source_archive_evidence_id="archive", network_member_evidence_id="network",
        scenario_member_evidence_id="scenario", composition_evidence_id="composition",
        pressure_reference_evidence_id="pressure-reference", nomination_interpretation_evidence_id="nomination",
        mapping_review_evidence_id="review", reviewed=True,
        node_states={"a": GasLibNodeState(role="boundary", pressure_pa_abs=6_000_000, temperature_k=300),
                     "b": GasLibNodeState(role="boundary", pressure_pa_abs=5_500_000, temperature_k=300)},
        pipe_controls={"p": GasLibPipeControl(segments=1, local_loss_coefficient=0, heat_into_fluid_w=0, evidence_id="pipe-control")},
    )
    return source, mapping, context


def test_adapter_preserves_geometry_and_caller_absolute_state(tmp_path):
    path = make_archive(tmp_path)
    source, mapping, context = make_inputs(path)
    spec = prepare_gaslib_network(source, path, mapping, context)
    assert spec.pipes[0].length_m == 4000
    assert spec.pipes[0].diameter_m == 0.1
    assert spec.boundaries[0].pressure_pa_abs == 6_000_000
    assert spec.fluid == "Methane" and spec.backend == "HEOS"


def test_adapter_revalidates_source_record_against_archive(tmp_path):
    path = make_archive(tmp_path)
    source, mapping, context = make_inputs(path)
    source["pipes"]["p"]["length_m"] = 1
    with pytest.raises(ValueError, match="differ"):
        prepare_gaslib_network(source, path, mapping, context)


def test_adapter_rejects_non_pipe_active_devices(tmp_path):
    network = NETWORK.replace("</f:connections>", '<valve id="v" from="a" to="b"/></f:connections>')
    path = make_archive(tmp_path, network)
    source, mapping, context = make_inputs(path)
    with pytest.raises(ValueError, match="non-pipe"):
        prepare_gaslib_network(source, path, mapping, context)


def test_adapter_rejects_elevation_difference(tmp_path):
    network = NETWORK.replace('sink id="b"><height value="0"', 'sink id="b"><height value="2"')
    path = make_archive(tmp_path, network)
    source, mapping, context = make_inputs(path)
    with pytest.raises(ValueError, match="elevation"):
        prepare_gaslib_network(source, path, mapping, context)


def test_adapter_requires_composition_evidence(tmp_path):
    path = make_archive(tmp_path)
    source, mapping, context = make_inputs(path)
    context = context.model_copy(update={"evidence": tuple(ref for ref in context.evidence if ref.id != "composition")})
    with pytest.raises(ValueError, match="evidence"):
        prepare_gaslib_network(source, path, mapping, context)


def test_adapter_requires_context_scenario_match(tmp_path):
    path = make_archive(tmp_path)
    source, mapping, context = make_inputs(path)
    context = context.model_copy(update={"scenario_id": "other"})
    with pytest.raises(ValueError, match="scenario"):
        prepare_gaslib_network(source, path, mapping, context)


@pytest.mark.parametrize("tamper", ["boundary", "fluid", "evidence", "review"])
def test_run_verifier_rejects_mapping_resealed_away_from_owner_specification(tmp_path, tamper):
    path = make_archive(tmp_path)
    source, mapping, context = make_inputs(path)

    class Spy:
        def __init__(self, after_solve=None):
            self.after_solve = after_solve

        def solve(self, spec):
            states = {
                "a": dict(fluid="Methane", backend="HEOS", pressure_pa_abs=6_000_000.0, temperature_k=300.0,
                          density_kg_m3=1.0, enthalpy_j_kg=100.0, entropy_j_kg_k=0.0, phase="gas",
                          viscosity_pa_s=1e-5, cp_j_kg_k=1000.0, cv_j_kg_k=700.0, conductivity_w_m_k=0.02,
                          speed_of_sound_m_s=300.0, provider_version="spy", provider_revision="spy"),
                "b": dict(fluid="Methane", backend="HEOS", pressure_pa_abs=5_500_000.0, temperature_k=300.0,
                          density_kg_m3=1.0, enthalpy_j_kg=100.0, entropy_j_kg_k=0.0, phase="gas",
                          viscosity_pa_s=1e-5, cp_j_kg_k=1000.0, cv_j_kg_k=700.0, conductivity_w_m_k=0.02,
                          speed_of_sound_m_s=300.0, provider_version="spy", provider_revision="spy"),
            }
            flow = dict(name="s", left="a", right="b", mass_flow_kg_s=0.0,
                        transmitted_specific_enthalpy_J_kg=100.0, momentum_residual_Pa=0.0)
            solution = dict(
                node_states={"a": dict(pressure_Pa=6_000_000.0, specific_enthalpy_J_kg=100.0),
                             "b": dict(pressure_Pa=5_500_000.0, specific_enthalpy_J_kg=100.0)},
                link_flows={"s": flow}, pressure_solver_success=True, pressure_optimizer_success=True,
                enthalpy_solver_success=True, junction_conservation_success=True,
            )
            run = NativeGasRun(
                digest(spec), solution, states, {"s": states["b"]}, {"p": ["s"]}, {}, {"owner": "spy"}
            )
            if self.after_solve is not None:
                self.after_solve()
            return run

    original_archive_sha = source["archive_sha256"]

    def mutate_caller_inputs_during_owner_solve():
        mapping.node_states["a"] = mapping.node_states["a"].model_copy(
            update={"pressure_pa_abs": 6_100_000.0}
        )
        source["archive_sha256"] = "f" * 64

    record = solve_gaslib_network(
        source, path, mapping, context, backend=Spy(mutate_caller_inputs_during_owner_solve)
    )
    assert verify_gaslib_network_run(record)
    assert record.source_archive_sha256 == original_archive_sha
    assert record.mapping.node_states["a"].pressure_pa_abs == 6_000_000
    if tamper == "boundary":
        changed_mapping = record.mapping.model_copy(
            update={"node_states": {**record.mapping.node_states, "a": record.mapping.node_states["a"].model_copy(
                update={"pressure_pa_abs": 6_100_000.0}
            )}}
        )
        message = "boundary mapping"
    elif tamper == "fluid":
        changed_mapping = record.mapping.model_copy(update={"fluid": "Nitrogen"})
        message = "thermodynamic mapping"
    elif tamper == "review":
        changed_mapping = record.mapping.model_copy(update={"reviewed": False})
        message = "caller-reviewed mapping"
    else:
        changed_mapping = record.mapping.model_copy(
            update={"pipe_controls": {"p": record.mapping.pipe_controls["p"].model_copy(
                update={"evidence_id": "absent-evidence"}
            )}}
        )
        message = "mapping evidence"
    body = record.model_dump(mode="json", exclude={"record_digest"})
    body["mapping"] = changed_mapping.model_dump(mode="json")
    forged = GasLibNetworkRun.model_validate_json(json.dumps({**body, "record_digest": digest(body)}))
    with pytest.raises(ValueError, match=message):
        verify_gaslib_network_run(forged)
