"""Physical reference, topology, coordinate, provenance and load-quality tests for optional frames."""

import json
import math
import runpy
from copy import deepcopy

import numpy as np
import pytest
from pydantic import ValidationError

import pipeline_integrity.structural as structural
from pipeline_integrity.contracts import SignalFrame, digest
from pipeline_integrity.structural import (
    FrameCombination,
    FrameLineLoad,
    FrameNode,
    FrameRequest,
    FrameSignalLoad,
    FrameSupport,
    frame_signal_frame,
    solve_frame,
    solve_frame_signal_snapshot,
)

EXAMPLE = runpy.run_path("examples/pipe_frame.py")


@pytest.fixture
def native():
    pytest.importorskip("Pynite")


def base():
    return EXAMPLE["make_case"](), EXAMPLE["context"]()


def test_public_factory_withholds_without_loading_optional_owner(monkeypatch):
    def forbidden(*args):
        pytest.fail("unreviewed public factory loaded native dependency")

    monkeypatch.setattr(structural, "version", forbidden)
    q, c = base()
    assembly = structural.build_native_frame(q.model_copy(update={"supports_reviewed": False}), c)
    assert assembly.native_model is None and assembly.record["status"] == "needs_data"
    assert assembly.record["schema"] == "pipeline_integrity.frame_assembly.v1"
    assert assembly.record["dof_order"] == [f"{n.id}:{d}" for n in q.nodes for d in structural.DOFS]


def test_public_factory_native_input_mutation_is_detected(native):
    q, c = base()
    assembly = structural.build_native_frame(q, c)
    assert assembly.check_integrity()
    assert assembly.record["native_backend"]["source_tree_sha256"]
    assembly.native_model.nodes["tip"].NodeLoads.append(("FX", 1.0, q.load_cases[0]))
    with pytest.raises(ValueError, match="inputs changed"):
        assembly.check_integrity()


def test_public_equilibrium_audit_retains_existing_force_and_moment_result():
    q, c = base()
    reactions = {n.id: [0.0, 0.0, 0.0, 0.0, 0.0, 0.0] for n in q.nodes}
    # Compute the balancing root action by statics, independently of PyNite.
    root = np.asarray(q.nodes[0].position_m)
    points = {n.id: np.asarray(n.position_m) for n in q.nodes}
    resultant, moment = np.zeros(3), np.zeros(3)
    factors = q.combinations[0].factors
    for load in q.node_loads:
        axis = structural.FORCES.index(load.direction)
        vector = np.zeros(3)
        vector[axis % 3] = load.value * factors.get(load.case_id, 0)
        if axis < 3:
            resultant += vector
            moment += np.cross(points[load.node_id] - root, vector)
        else:
            moment += vector
    reactions[q.nodes[0].id] = (-resultant).tolist() + (-moment).tolist()
    audit = structural.audit_frame_equilibrium(q, q.combinations[0].id, reactions, c)
    assert audit["schema"] == "pipeline_integrity.frame_equilibrium_audit.v1"
    assert audit["passed"] and audit["nodal_balance"] is None
    assert audit["static_balance"] == structural._balance(q, q.combinations[0], reactions)
    reactions[q.nodes[0].id][0] += 1
    assert not structural.audit_frame_equilibrium(q, q.combinations[0].id, reactions, c)["passed"]


def test_public_stiffness_receipt_uses_shared_native_factory_and_support_basis(native):
    q, c = base()
    reference = solve_frame(q, c)
    assembly = structural.build_native_frame(q, c)
    receipt = structural.assemble_frame_stiffness(
        assembly, q.combinations[0].id, reference_record=reference, include_geometric=True
    )
    assert receipt["schema"] == "pipeline_integrity.frame_stiffness_assembly.v1"
    assert receipt["assembly_record"] == reference["backend"]["assembly_record"]
    assert receipt["dof_order"] == assembly.record["dof_order"]
    assert receipt["fixed_dof_indices"] == assembly.record["fixed_dof_indices"]
    assert receipt["geometric_first_step"] is False
    assert receipt["prestress_audit"]["reference_state_matched"]


def test_public_audit_rejects_incomplete_reactions_and_duplicate_evidence():
    q, c = base()
    with pytest.raises(ValueError):
        structural.audit_frame_equilibrium(q, q.combinations[0].id, {}, c)
    with pytest.raises(ValueError):
        structural.build_native_frame(q, c.model_copy(update={"evidence": c.evidence * 2}))


def test_unreviewed_and_unknown_load_do_not_import_solver(monkeypatch):
    def forbidden(*args):
        raise AssertionError("optional engine must not be loaded")

    monkeypatch.setattr(structural, "version", forbidden)
    request, context = base()
    for field in (
        "geometry_and_connectivity_reviewed",
        "supports_reviewed",
        "stiffness_and_loads_reviewed",
        "linear_small_displacement_applicability_reviewed",
    ):
        result = solve_frame(request.model_copy(update={field: False}), context)
        assert result["status"] == "needs_data" and not result["combinations"]
        assert result["record_digest"] == digest({k: v for k, v in result.items() if k != "record_digest"})
    unknown = request.model_copy(
        update={"node_loads": (request.node_loads[0].model_copy(update={"value": None}),)}
    )
    assert solve_frame(unknown, context)["status"] == "needs_data"


@pytest.mark.parametrize("direction", ["FY", "FZ", "FX", "MX", "MZ"])
def test_cantilever_three_dimensional_reference(native, direction):
    request, context = base()
    load = request.node_loads[0].model_copy(update={"direction": direction})
    result = solve_frame(request.model_copy(update={"node_loads": (load,)}), context)
    assert result["status"] == "computed"
    out = result["combinations"]["service"]
    tip = out["nodes"]["tip"]
    e, nu = request.materials[0].young_modulus_pa, request.materials[0].poisson_ratio
    s, length, p = request.sections[0], 4.0, load.value
    if direction == "FY":
        assert tip["translation_m"][1] == pytest.approx(p * length**3 / (3 * e * s.iz_m4), rel=1e-11)
        assert tip["rotation_rad"][2] == pytest.approx(p * length**2 / (2 * e * s.iz_m4), rel=1e-11)
    elif direction == "FZ":
        assert tip["translation_m"][2] == pytest.approx(p * length**3 / (3 * e * s.iy_m4), rel=1e-11)
    elif direction == "FX":
        assert tip["translation_m"][0] == pytest.approx(p * length / (e * s.area_m2), rel=1e-11)
    elif direction == "MX":
        assert tip["rotation_rad"][0] == pytest.approx(
            p * length / (e / (2 * (1 + nu)) * s.torsion_constant_m4), rel=1e-11
        )
    else:
        assert tip["translation_m"][1] == pytest.approx(p * length**2 / (2 * e * s.iz_m4), rel=1e-11)
    assert out["static_balance"]["passed"]


def test_uniform_load_and_two_span_discretization(native):
    request, context = base()
    line = FrameLineLoad(
        member_id="pipe",
        global_direction="FY",
        start_n_m=-100.0,
        end_n_m=-100.0,
        case_id="point",
        evidence_id="reference",
    )
    full = request.model_copy(update={"node_loads": (), "line_loads": (line,)})
    result = solve_frame(full, context)["combinations"]["service"]
    ei = request.materials[0].young_modulus_pa * request.sections[0].iz_m4
    assert result["nodes"]["tip"]["translation_m"][1] == pytest.approx(-100 * 4**4 / (8 * ei), rel=1e-11)
    assert result["nodes"]["anchor"]["reaction_force_n"][1] == pytest.approx(400)
    assert result["nodes"]["anchor"]["reaction_moment_nm"][2] == pytest.approx(800)
    nodes = request.nodes + (FrameNode(id="joint", position_m=(2.0, 0.0, 0.0)),)
    first = request.members[0].model_copy(update={"j_node": "joint"})
    second = first.model_copy(update={"id": "continuation", "i_node": "joint", "j_node": "tip"})
    split = full.model_copy(
        update={
            "nodes": nodes,
            "members": (first, second),
            "line_loads": (line, line.model_copy(update={"member_id": "continuation"})),
        }
    )
    split_result = solve_frame(split, context)["combinations"]["service"]
    assert split_result["nodes"]["tip"]["translation_m"] == pytest.approx(
        result["nodes"]["tip"]["translation_m"], abs=1e-12
    )
    assert split_result["static_balance"]["passed"]


def test_partial_triangular_load_against_independent_green_function(native):
    from scipy.integrate import quad

    request, context = base()
    load = FrameLineLoad(
        member_id="pipe",
        global_direction="FY",
        start_n_m=0.0,
        end_n_m=-300.0,
        start_fraction=0.2,
        end_fraction=0.8,
        case_id="point",
        evidence_id="reference",
    )
    result = solve_frame(request.model_copy(update={"node_loads": (), "line_loads": (load,)}), context)
    ei = request.materials[0].young_modulus_pa * request.sections[0].iz_m4
    expected = quad(lambda x: -300 * (x - 0.8) / 2.4 * x**2 * (12 - x) / (6 * ei), 0.8, 3.2)[0]
    assert result["status"] == "computed"
    assert result["combinations"]["service"]["nodes"]["tip"]["translation_m"][1] == pytest.approx(
        expected, abs=1e-12
    )


def test_simply_supported_member_midspan(native):
    request, context = base()
    supports = (
        FrameSupport(node_id="anchor", fixed_dofs=("DX", "DY", "DZ", "RX"), evidence_id="reference"),
        FrameSupport(node_id="tip", fixed_dofs=("DY", "DZ"), evidence_id="reference"),
    )
    line = FrameLineLoad(
        member_id="pipe",
        global_direction="FY",
        start_n_m=-100.0,
        end_n_m=-100.0,
        case_id="point",
        evidence_id="reference",
    )
    result = solve_frame(
        request.model_copy(update={"supports": supports, "node_loads": (), "line_loads": (line,)}), context
    )
    assert result["status"] == "computed"
    out = result["combinations"]["service"]
    midspan = out["members"]["pipe"]["samples"][2]
    ei = request.materials[0].young_modulus_pa * request.sections[0].iz_m4
    assert midspan["local_translation_m"][1] == pytest.approx(-5 * 100 * 4**4 / (384 * ei), rel=1e-10)
    assert out["nodes"]["anchor"]["reaction_force_n"][1] == pytest.approx(200)


def test_translation_spring_and_imposed_movement(native):
    request, context = base()
    k = 100000.0
    spring = FrameSupport(node_id="tip", translation_springs_n_m={"DY": k}, evidence_id="reference")
    result = solve_frame(request.model_copy(update={"supports": request.supports + (spring,)}), context)
    assert result["status"] == "computed"
    ei = request.materials[0].young_modulus_pa * request.sections[0].iz_m4
    out = result["combinations"]["service"]
    expected = -1000 / (3 * ei / 4**3 + k)
    assert out["nodes"]["tip"]["translation_m"][1] == pytest.approx(expected, rel=1e-10)
    assert out["nodes"]["tip"]["reaction_force_n"][1] == pytest.approx(-k * expected, rel=1e-10)
    settlement = FrameSupport(
        node_id="tip", fixed_dofs=("DY",), imposed_translations_m={"DY": -0.001}, evidence_id="reference"
    )
    zero = FrameCombination(id="settlement", factors={"point": 0.0}, evidence_id="reference")
    imposed = solve_frame(
        request.model_copy(
            update={"supports": request.supports + (settlement,), "node_loads": (), "combinations": (zero,)}
        ),
        context,
    )
    assert imposed["status"] == "computed"
    assert imposed["combinations"]["settlement"]["nodes"]["tip"]["translation_m"][1] == -0.001
    assert imposed["combinations"]["settlement"]["nodes"]["tip"]["reaction_force_n"][1] == pytest.approx(
        -0.001 * 3 * ei / 4**3
    )


def test_release_and_anisotropic_section_rotation(native):
    request, context = base()
    section = request.sections[0].model_copy(update={"iy_m4": request.sections[0].iy_m4 * 2})
    member = request.members[0].model_copy(update={"rotation_deg": 90.0})
    result = solve_frame(request.model_copy(update={"sections": (section,), "members": (member,)}), context)
    assert result["status"] == "computed"
    original = solve_frame(request, context)
    assert result["combinations"]["service"]["nodes"]["tip"]["translation_m"][1] == pytest.approx(
        original["combinations"]["service"]["nodes"]["tip"]["translation_m"][1] / 2
    )
    pin = request.members[0].model_copy(update={"rotation_releases": ("Rzi",)})
    supports = request.supports + (request.supports[0].model_copy(update={"node_id": "tip"}),)
    line = FrameLineLoad(
        member_id="pipe",
        global_direction="FY",
        start_n_m=-100.0,
        end_n_m=-100.0,
        case_id="point",
        evidence_id="reference",
    )
    released = solve_frame(
        request.model_copy(
            update={"members": (pin,), "supports": supports, "node_loads": (), "line_loads": (line,)}
        ),
        context,
    )
    assert released["status"] == "computed"
    out = released["combinations"]["service"]
    assert out["nodes"]["anchor"]["reaction_force_n"][1] == pytest.approx(3 * 400 / 8)
    assert abs(out["members"]["pipe"]["native_local_end_actions"][5]) < 1e-9


def test_global_coordinate_rotation_and_translation(native):
    request, context = base()
    rotation, _ = np.linalg.qr(np.array([[1.0, 2, 3], [4.0, -1, 2], [1.0, 4, -2]]))
    if np.linalg.det(rotation) < 0:
        rotation[:, 0] *= -1
    shift = np.array([10.0, -20, 5])
    nodes = tuple(
        n.model_copy(
            update={"position_m": tuple(float(v) for v in rotation @ np.array(n.position_m) + shift)}
        )
        for n in request.nodes
    )
    force = rotation @ np.array([0.0, -1000, 0])
    loads = tuple(
        request.node_loads[0].model_copy(update={"direction": direction, "value": float(value)})
        for direction, value in zip(("FX", "FY", "FZ"), force, strict=True)
    )
    transformed = solve_frame(
        request.model_copy(
            update={"nodes": nodes, "node_loads": loads, "moment_reference_point_m": tuple(shift)}
        ),
        context,
    )
    assert transformed["status"] == "computed"
    original = solve_frame(request, context)
    d = original["combinations"]["service"]["nodes"]["tip"]["translation_m"]
    assert transformed["combinations"]["service"]["nodes"]["tip"]["translation_m"] == pytest.approx(
        rotation @ d, abs=1e-12
    )


def test_branch_load_superposition_and_maxwell_betti_reciprocity(native):
    request, context = base()
    nodes = request.nodes + (
        FrameNode(id="branch", position_m=(2.0, 2.0, 1.0)),
        FrameNode(id="joint", position_m=(2.0, 0.0, 0.0)),
    )
    original = request.members[0]
    members = (
        original.model_copy(update={"j_node": "joint"}),
        original.model_copy(update={"id": "second", "i_node": "joint"}),
        original.model_copy(update={"id": "branch", "i_node": "joint", "j_node": "branch"}),
    )
    first = request.node_loads[0].model_copy(update={"case_id": "one", "value": 1.0})
    second = first.model_copy(update={"node_id": "branch", "case_id": "two", "direction": "FX"})
    combos = tuple(
        FrameCombination(id=name, factors=factors, evidence_id="reference")
        for name, factors in (
            ("a", {"one": 1.0}),
            ("b", {"two": 1.0}),
            ("combined", {"one": 2.0, "two": -0.5}),
        )
    )
    result = solve_frame(
        request.model_copy(
            update={
                "nodes": nodes,
                "members": members,
                "load_cases": ("one", "two"),
                "node_loads": (first, second),
                "combinations": combos,
            }
        ),
        context,
    )
    assert result["status"] == "computed"
    a, b, c = (result["combinations"][name] for name in ("a", "b", "combined"))
    assert a["nodes"]["branch"]["translation_m"][0] == pytest.approx(
        b["nodes"]["tip"]["translation_m"][1], abs=1e-14
    )
    for node in nodes:
        expected = 2 * np.array(a["nodes"][node.id]["translation_m"]) - 0.5 * np.array(
            b["nodes"][node.id]["translation_m"]
        )
        assert c["nodes"][node.id]["translation_m"] == pytest.approx(expected, abs=1e-13)


def test_instability_and_large_response_keep_unqualified_status(native):
    request, context = base()
    assert solve_frame(request.model_copy(update={"supports": ()}), context)["status"] == "out_of_scope"
    large = request.node_loads[0].model_copy(update={"value": -1e8})
    result = solve_frame(request.model_copy(update={"node_loads": (large,)}), context)
    assert result["status"] == "out_of_scope" and result["combinations"]
    signal = frame_signal_frame(result, "service", context)
    assert all(value is not None for value in signal.values.values()) and not any(signal.valid.values())


def test_signal_binding_keeps_signed_units_clock_and_missing(native):
    request, context = base()
    observation = SignalFrame(
        stream_id="load-cell",
        clock_id=context.clock_id,
        time_s=1.25,
        values={"load": -2.0},
        units={"load": "kN"},
        valid={"load": True},
        origin="laboratory",
    )
    mapping = FrameSignalLoad(
        source_stream_id="load-cell", source_channel="load", source_evidence_id="reference", node_load_index=0
    )
    assert solve_frame_signal_snapshot(request, observation, mapping, context)["status"] == "needs_data"
    mapping = mapping.model_copy(update={"quasistatic_snapshot_hypothesis_reviewed": True})
    result = solve_frame_signal_snapshot(request, observation, mapping, context)
    assert result["status"] == "computed" and result["request"]["time_s"] == 1.25
    assert result["request"]["node_loads"][0]["value"] == -2000.0
    assert result["source_signal_binding"]["observation"]["origin"] == "laboratory"
    assert frame_signal_frame(result, "service", context).origin == "simulation"
    for values, valid in (({"load": None}, {"load": False}), ({"load": -2.0}, {"load": False})):
        missing = observation.model_copy(update={"values": values, "valid": valid})
        assert solve_frame_signal_snapshot(request, missing, mapping, context)["status"] == "needs_data"
    with pytest.raises(ValueError):
        solve_frame_signal_snapshot(
            request, observation.model_copy(update={"clock_id": "other"}), mapping, context
        )
    changed = deepcopy(result)
    changed["combinations"]["service"]["nodes"]["tip"]["translation_m"][1] += 1
    with pytest.raises(ValueError, match="sealing"):
        frame_signal_frame(changed, "service", context)


@pytest.mark.parametrize(
    "mutation",
    [
        "bool",
        "inf",
        "interior",
        "duplicate",
        "unknown_case",
        "missing_node",
        "spring_fixed",
        "imposed_free",
        "sample_order",
    ],
)
def test_strict_schema_and_explicit_topology(mutation):
    request, _ = base()
    values = request.model_dump(mode="json")
    if mutation in ("bool", "inf"):
        values["node_loads"][0]["value"] = True if mutation == "bool" else math.inf
    elif mutation == "interior":
        values["nodes"].append({"id": "hidden", "position_m": [2.0, 0.0, 0.0]})
    elif mutation == "duplicate":
        values["nodes"].append(values["nodes"][0])
    elif mutation == "unknown_case":
        values["combinations"][0]["factors"] = {"undeclared": 1.0}
    elif mutation == "missing_node":
        values["members"][0]["j_node"] = "absent"
    elif mutation == "spring_fixed":
        values["supports"][0]["translation_springs_n_m"] = {"DX": 1.0}
    elif mutation == "imposed_free":
        values["supports"].append(
            {"node_id": "tip", "imposed_translations_m": {"DY": 0.01}, "evidence_id": "reference"}
        )
    else:
        values["members"][0]["sample_fractions"] = [0.0, 0.75, 0.5, 1.0]
    with pytest.raises(ValidationError):
        FrameRequest.model_validate_json(json.dumps(values))


def test_context_evidence_and_backend_version_boundaries(monkeypatch):
    request, context = base()
    with pytest.raises(ValueError):
        solve_frame(request, context.model_copy(update={"clock_id": "wrong"}))
    with pytest.raises(ValueError):
        solve_frame(request, context.model_copy(update={"evidence": ()}))
    monkeypatch.setattr(structural, "version", lambda *args: "9.0.0")
    assert solve_frame(request, context)["status"] == "out_of_scope"
