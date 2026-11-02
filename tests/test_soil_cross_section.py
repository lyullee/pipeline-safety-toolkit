"""Actual FiPy references plus strict mesh, evidence, clock and ledger boundaries."""

import builtins
import math
import runpy
from copy import deepcopy
from pathlib import Path

import pytest

from pipeline_integrity.contracts import digest
from pipeline_integrity.soil_cross_section import (
    boundary_metadata,
    evaluate_soil_cross_section,
    to_signal_frames,
    validate_mesh,
    verify_soil_cross_section_record,
)

MAKE_CASE = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/soil_cross_section.py"))[
    "make_case"
]


@pytest.fixture(scope="module")
def uniform():
    request, context = MAKE_CASE(reviewed=True)
    return request, context, evaluate_soil_cross_section(request, context)


def _reseal(record):
    if record.get("native") is not None:
        record["native_digest"] = digest(record["native"])
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})


def test_default_withheld_before_optional_import(monkeypatch):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith(("fipy", "numpy")):
            pytest.fail("optional backend imported for an unreviewed request")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    request, context = MAKE_CASE()
    result = evaluate_soil_cross_section(request, context)
    assert result["status"] == "needs_data" and not result["native_called"]
    assert verify_soil_cross_section_record(result, request, context)["valid"]


@pytest.mark.parametrize(
    "field",
    [
        "conductivity_W_m_K",
        "volumetric_heat_capacity_J_m3_K",
        "temperature_support_K",
        "initial_temperature_K",
    ],
)
def test_unknown_properties_are_withheld(monkeypatch, field):
    request, context = MAKE_CASE(reviewed=True)
    value = tuple(None for _ in request.initial_temperature_K) if field == "initial_temperature_K" else None
    request = request.model_copy(update={field: value})
    monkeypatch.setattr(
        "pipeline_integrity.soil_cross_section._backend", lambda: pytest.fail("backend must not be touched")
    )
    assert evaluate_soil_cross_section(request, context)["status"] == "needs_data"


@pytest.mark.parametrize("mode", ["clock", "asset", "evidence", "metadata"])
def test_common_context_binding(mode):
    request, context = MAKE_CASE(reviewed=True)
    if mode in {"clock", "asset"}:
        context = context.model_copy(update={mode + "_id": "other"})
    elif mode == "evidence":
        context = context.model_copy(update={"evidence": ()})
    else:
        context = context.model_copy(deep=True)
        context.metadata["soil_cross_section_boundary"]["clock_offset_s"] += 1
    with pytest.raises(ValueError):
        evaluate_soil_cross_section(request, context)


@pytest.mark.parametrize(
    "mode",
    [
        "interior_face",
        "duplicate_boundary",
        "missing_exterior",
        "ground_height",
        "bad_vertex",
        "duplicate_cell",
        "unused_vertex",
        "no_pipe_hole",
    ],
)
def test_mesh_topology_refusals(mode):
    request, _ = MAKE_CASE(reviewed=True)
    mesh = request.mesh.model_copy(deep=True)
    boundaries = request.boundaries
    if mode == "interior_face":
        interior = next(
            i for i in range(len(mesh.face_vertex_ids)) if sum(i in cell for cell in mesh.cell_face_ids) == 2
        )
        groups = deepcopy(mesh.boundary_face_groups)
        groups["pipe_A"] = (interior, *groups["pipe_A"][1:])
        mesh = mesh.model_copy(update={"boundary_face_groups": groups})
    elif mode == "duplicate_boundary":
        groups = deepcopy(mesh.boundary_face_groups)
        groups["ground"] += groups["pipe_A"][:1]
        mesh = mesh.model_copy(update={"boundary_face_groups": groups})
    elif mode == "missing_exterior":
        groups = deepcopy(mesh.boundary_face_groups)
        groups["far"] = groups["far"][1:]
        mesh = mesh.model_copy(update={"boundary_face_groups": groups})
    elif mode == "ground_height":
        mesh = mesh.model_copy(update={"ground_surface_y_m": 0.1})
    elif mode == "bad_vertex":
        mesh = mesh.model_copy(update={"face_vertex_ids": ((0, 99999), *mesh.face_vertex_ids[1:])})
    elif mode == "duplicate_cell":
        mesh = mesh.model_copy(update={"cell_face_ids": (*mesh.cell_face_ids, mesh.cell_face_ids[0])})
    elif mode == "unused_vertex":
        mesh = mesh.model_copy(update={"vertices_m": (*mesh.vertices_m, (100.0, 0.0))})
    else:
        boundaries = tuple(
            b.model_copy(update={"role": "pipe", "component_asset_id": "outer-anchor"})
            if b.id == "ground"
            else b.model_copy(update={"role": "far_field", "component_asset_id": None})
            if b.id == "pipe_A"
            else b
            for b in boundaries
        )
    with pytest.raises(ValueError):
        validate_mesh(mesh, boundaries)


def test_genuine_cavities_and_uniform_invariance(uniform):
    request, context, result = uniform
    assert result["status"] == "computed"
    assert result["native"]["geometry"]["cavity_count"] == 2
    assert sum(result["native"]["geometry"]["cell_areas_m2"]) == 22.0
    assert max(abs(v - 300) for row in result["native"]["trajectory"]["temperature_K"] for v in row) < 1e-10
    assert result["audit"]["energy_budget_passed"]
    assert verify_soil_cross_section_record(result, request, context)["valid"]


def test_embedded_two_dimensional_linear_solution():
    request, context = MAKE_CASE(reviewed=True, mode="linear")
    result = evaluate_soil_cross_section(request, context)
    assert result["status"] == "computed"
    assert (
        max(
            abs(v - request.initial_temperature_K[i])
            for row in result["native"]["trajectory"]["temperature_K"]
            for i, v in enumerate(row)
        )
        < 1e-9
    )
    powers = result["native"]["trajectory"]["boundary_heat_rate_into_soil_W"]
    assert math.isclose(powers["ground"][-1], 24.0, abs_tol=1e-9)
    assert math.isclose(powers["far"][-1], -24.0, abs_tol=1e-9)


def test_flux_sign_and_extrusion_scaling():
    records = []
    for length in (1.0, 3.0):
        request, context = MAKE_CASE(reviewed=True, mode="flux_sign", length_m=length)
        result = evaluate_soil_cross_section(request, context)
        records.append(result)
        assert result["status"] == "computed"
        assert math.isclose(result["audit"]["final_stored_energy_change_J"], 40 * length * 0.2, abs_tol=1e-7)
        assert math.isclose(
            result["native"]["trajectory"]["boundary_heat_rate_into_soil_W"]["pipe_A"][-1],
            40 * length,
            abs_tol=1e-9,
        )
    assert (
        records[0]["native"]["trajectory"]["temperature_K"]
        == records[1]["native"]["trajectory"]["temperature_K"]
    )


def test_actual_two_pipe_interaction_reciprocity_and_superposition():
    answers = {}
    for mode in ("interaction_A", "interaction_B", "interaction_both"):
        request, context = MAKE_CASE(reviewed=True, mode=mode)
        result = evaluate_soil_cross_section(request, context)
        assert result["status"] == "computed" and result["audit"]["energy_budget_passed"]
        answers[mode] = {
            k: v[-1] for k, v in result["native"]["trajectory"]["boundary_heat_rate_into_soil_W"].items()
        }
    assert answers["interaction_A"]["pipe_B"] < -0.1
    assert math.isclose(answers["interaction_A"]["pipe_B"], answers["interaction_B"]["pipe_A"], abs_tol=1e-7)
    for name in answers["interaction_both"]:
        assert math.isclose(
            answers["interaction_both"][name],
            answers["interaction_A"][name] + answers["interaction_B"][name],
            abs_tol=1e-6,
        )


def test_convection_is_cooling_and_conserves_storage():
    request, context = MAKE_CASE(reviewed=True, mode="convection")
    result = evaluate_soil_cross_section(request, context)
    assert result["status"] == "computed"
    assert result["audit"]["final_stored_energy_change_J"] < 0
    assert all(v[-1] < 0 for v in result["native"]["trajectory"]["boundary_heat_rate_into_soil_W"].values())
    assert verify_soil_cross_section_record(result, request, context)["valid"]


def test_previous_knot_hold_and_linear_forcing_have_distinct_energy():
    answers = {}
    for hold in ("linear", "previous_knot_hold"):
        request, context = MAKE_CASE(reviewed=True, mode="flux_sign")
        boundaries = tuple(
            b.model_copy(
                update={
                    "heat_flux_into_soil_W_m2": (
                        b.heat_flux_into_soil_W_m2[0],
                        tuple(0.0 for _ in b.heat_flux_into_soil_W_m2[0]),
                        tuple(0.0 for _ in b.heat_flux_into_soil_W_m2[0]),
                    )
                }
            )
            if b.id == "pipe_A"
            else b
            for b in request.boundaries
        )
        request = request.model_copy(update={"history_interpolation": hold, "boundaries": boundaries})
        result = evaluate_soil_cross_section(request, context)
        assert result["status"] == "computed"
        answers[hold] = result["audit"]["final_stored_energy_change_J"]
    assert math.isclose(answers["linear"], 2.0, abs_tol=1e-7)
    assert math.isclose(answers["previous_knot_hold"], 8.0, abs_tol=1e-7)


def test_surface_outside_property_support_prevents_downstream_use():
    request, context = MAKE_CASE(reviewed=True, mode="flux_sign")
    boundaries = tuple(
        b.model_copy(
            update={
                "heat_flux_into_soil_W_m2": tuple(
                    tuple(100.0 for _ in row) for row in b.heat_flux_into_soil_W_m2
                )
            }
        )
        if b.id == "pipe_A"
        else b
        for b in request.boundaries
    )
    request = request.model_copy(update={"boundaries": boundaries})
    result = evaluate_soil_cross_section(request, context)
    assert result["status"] == "out_of_scope" and result["native"]["status"] == "computed"
    assert not result["audit"]["temperature_support_passed"]
    with pytest.raises(ValueError):
        to_signal_frames(result, context)


@pytest.mark.parametrize(
    "mode",
    [
        "clock",
        "initial",
        "surface",
        "power",
        "cumulative",
        "geometry",
        "backend",
        "evidence",
        "producer",
        "status",
    ],
)
def test_self_rehashed_corruptions_are_refused(uniform, mode):
    request, context, original = uniform
    record = deepcopy(original)
    if mode == "clock":
        record["native"]["trajectory"]["time_s"][-1] -= 0.001
    elif mode == "initial":
        record["native"]["trajectory"]["temperature_K"][0][0] += 1
    elif mode == "surface":
        record["native"]["trajectory"]["boundary_surface_temperature_K"]["ground"][-1][0] += 1
    elif mode == "power":
        record["native"]["trajectory"]["boundary_heat_rate_into_soil_W"]["ground"][-1] += 1
    elif mode == "cumulative":
        record["native"]["trajectory"]["cumulative_boundary_energy_into_soil_J"]["ground"][-1] += 1
    elif mode == "geometry":
        record["native"]["geometry"]["cell_centers_m"][0][0] += 0.1
    elif mode == "backend":
        record["native"]["backend"]["version"] = "0"
    elif mode == "evidence":
        record["context"]["evidence"][0]["id"] = "other"
        record["input_digest"] = digest({"request": record["request"], "context": record["context"]})
    elif mode == "producer":
        record["adapter_sha256"] = "0" * 64
    else:
        record["status"] = "partial"
    _reseal(record)
    assert not verify_soil_cross_section_record(record, request, context)["valid"]


def test_signal_export_retains_clock_origin_units_and_unknown_first_rate(uniform):
    _, context, result = uniform
    frames = to_signal_frames(result, context)
    assert [f.time_s for f in frames] == [20.0, 20.05, 20.1, 20.15, 20.2]
    assert all(f.clock_id == context.clock_id and f.origin == "synthetic" for f in frames)
    assert frames[0].values["boundary.pipe_A.backward_euler_power_into_soil_W"] is None
    assert not frames[0].valid["boundary.pipe_A.backward_euler_power_into_soil_W"]
    assert frames[-1].units["boundary.pipe_A.cumulative_energy_into_soil_J"] == "J"
    with pytest.raises(ValueError):
        to_signal_frames(result, context.model_copy(update={"clock_id": "other"}))


def test_partial_native_history_requires_explicit_export_opt_in(monkeypatch):
    from fipy.solvers.scipy import LinearLUSolver

    original = LinearLUSolver._solve_
    calls = 0

    def interrupted(self, *args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("authored interruption after one actual native step")
        return original(self, *args, **kwargs)

    monkeypatch.setattr(LinearLUSolver, "_solve_", interrupted)
    request, context = MAKE_CASE(reviewed=True, mode="convection")
    result = evaluate_soil_cross_section(request, context)
    assert result["status"] == "partial" and len(result["native"]["trajectory"]["time_s"]) == 2
    assert verify_soil_cross_section_record(result, request, context)["valid"]
    with pytest.raises(ValueError):
        to_signal_frames(result, context)
    frames = to_signal_frames(result, context, allow_partial=True)
    assert len(frames) == 2 and frames[-1].operating_mode == "soil-native-partial-prefix"


def test_initial_support_refusal_is_not_a_native_call(monkeypatch):
    request, context = MAKE_CASE(reviewed=True)
    request = request.model_copy(
        update={"initial_temperature_K": tuple(500.0 for _ in request.initial_temperature_K)}
    )
    monkeypatch.setattr(
        "pipeline_integrity.soil_cross_section._backend", lambda: pytest.fail("backend must not be touched")
    )
    result = evaluate_soil_cross_section(request, context)
    assert result["status"] == "out_of_scope" and not result["native_called"]


def test_step_budget_and_units_are_not_silently_repaired():
    request, context = MAKE_CASE(reviewed=True)
    with pytest.raises(ValueError):
        evaluate_soil_cross_section(request.model_copy(update={"maximum_step_s": 1e-12}), context)
    with pytest.raises(ValueError):
        evaluate_soil_cross_section(request.model_copy(update={"input_units": {}}), context)
    assert boundary_metadata(request) == context.metadata["soil_cross_section_boundary"]


def test_balanced_dirichlet_power_forgery_cannot_be_exported(uniform):
    from pipeline_integrity.soil_cross_section import _audit

    request, context, original = uniform
    forged = deepcopy(original)
    trajectory = forged["native"]["trajectory"]
    for name, power in (("ground", 100.0), ("far", -100.0)):
        trajectory["boundary_heat_rate_into_soil_W"][name] = [power] * len(trajectory["time_s"])
        trajectory["cumulative_boundary_energy_into_soil_J"][name] = [power * t for t in trajectory["time_s"]]
    forged["audit"] = _audit(request, forged["native"])
    assert forged["audit"]["energy_budget_passed"]
    _reseal(forged)
    assert not verify_soil_cross_section_record(forged, request, context)["valid"]
    with pytest.raises(ValueError):
        to_signal_frames(forged, context)


def test_balanced_robin_surface_and_power_forgery_is_refused():
    from pipeline_integrity.soil_cross_section import _audit

    request, context = MAKE_CASE(reviewed=True)
    request = request.model_copy(
        update={
            "boundaries": tuple(
                b.model_copy(
                    update={
                        "kind": "convection",
                        "temperature_K": None,
                        "ambient_temperature_K": b.temperature_K,
                        "convection_W_m2_K": tuple(tuple(1.0 for _ in row) for row in b.temperature_K),
                    }
                )
                for b in request.boundaries
            )
        }
    )
    record = evaluate_soil_cross_section(request, context)
    trajectory = record["native"]["trajectory"]
    for name, value, power in (("ground", 299.0, 12.0), ("far", 300 + 3 / 7, -12.0)):
        trajectory["boundary_surface_temperature_K"][name] = [
            [value] * len(request.mesh.boundary_face_groups[name]) for _ in trajectory["time_s"]
        ]
        trajectory["boundary_heat_rate_into_soil_W"][name] = [power] * len(trajectory["time_s"])
        trajectory["cumulative_boundary_energy_into_soil_J"][name] = [power * t for t in trajectory["time_s"]]
    record["audit"] = _audit(request, record["native"])
    assert record["audit"]["energy_budget_passed"]
    _reseal(record)
    assert not verify_soil_cross_section_record(record, request, context)["valid"]
    with pytest.raises(ValueError):
        to_signal_frames(record, context)


def test_globally_conservative_interior_temperature_forgery_fails_local_equations(uniform):
    from pipeline_integrity.soil_cross_section import _audit

    request, context, original = uniform
    forged = deepcopy(original)
    interior = [
        i
        for i, cell in enumerate(request.mesh.cell_face_ids)
        if all(sum(face in c for c in request.mesh.cell_face_ids) == 2 for face in cell)
    ]
    assert len(interior) >= 2
    for row in forged["native"]["trajectory"]["temperature_K"][1:]:
        row[interior[0]] += 1.0
        row[interior[1]] -= 1.0
    forged["audit"] = _audit(request, forged["native"])
    assert forged["audit"]["energy_budget_passed"]
    _reseal(forged)
    assert not verify_soil_cross_section_record(forged, request, context)["valid"]


@pytest.mark.parametrize("field", ["conductivity", "initial", "boundary", "metadata"])
def test_nonfinite_cloned_inputs_are_invalid_and_never_become_missing(monkeypatch, field):
    request, context = MAKE_CASE(reviewed=True)
    if field == "conductivity":
        request = request.model_copy(update={"conductivity_W_m_K": float("nan")})
    elif field == "initial":
        request = request.model_copy(
            update={"initial_temperature_K": (float("nan"), *request.initial_temperature_K[1:])}
        )
    elif field == "boundary":
        boundary = request.boundaries[0]
        rows = boundary.temperature_K
        boundary = boundary.model_copy(update={"temperature_K": ((float("nan"), *rows[0][1:]), *rows[1:])})
        request = request.model_copy(update={"boundaries": (boundary, *request.boundaries[1:])})
    else:
        context = context.model_copy(deep=True)
        context.metadata["invalid"] = float("nan")
    monkeypatch.setattr(
        "pipeline_integrity.soil_cross_section._backend",
        lambda: pytest.fail("invalid input must not import backend"),
    )
    with pytest.raises(ValueError):
        evaluate_soil_cross_section(request, context)


@pytest.mark.parametrize("shear", [0.3, 0.75])
def test_unqualified_skew_is_withheld_before_backend_even_if_caller_reviews_it(monkeypatch, shear):
    request, context = MAKE_CASE(reviewed=True, mode="linear")
    mesh = request.mesh.model_copy(
        update={
            "vertices_m": tuple((x + shear * y, y) for x, y in request.mesh.vertices_m),
            "maximum_reviewed_nonorthogonality_deg": 85.0,
        }
    )
    request = request.model_copy(update={"mesh": mesh})
    monkeypatch.setattr(
        "pipeline_integrity.soil_cross_section._backend",
        lambda: pytest.fail("unsupported geometry must not import backend"),
    )
    result = evaluate_soil_cross_section(request, context)
    assert result["status"] == "out_of_scope" and not result["native_called"]
    assert verify_soil_cross_section_record(result, request, context)["valid"]
    with pytest.raises(ValueError):
        to_signal_frames(result, context)


def test_nonconforming_zero_area_slit_is_not_a_pipe_cavity():
    from pipeline_integrity.soil_cross_section import SoilCrossSectionMesh

    vertices = []
    vertex_ids = {}
    faces = []
    face_ids = {}
    cells = []

    def vertex(point):
        if point not in vertex_ids:
            vertex_ids[point] = len(vertices)
            vertices.append(point)
        return vertex_ids[point]

    for xmin, xmax, ymin, ymax in (
        (0, 1, -1, 0),
        (1, 3, -1, 0),
        (3, 4, -1, 0),
        (0, 1, -2, -1),
        (1, 2, -2, -1),
        (2, 3, -2, -1),
        (3, 4, -2, -1),
    ):
        points = (
            (float(xmin), float(ymin)),
            (float(xmax), float(ymin)),
            (float(xmax), float(ymax)),
            (float(xmin), float(ymax)),
        )
        cell = []
        for a, b in zip(points, (*points[1:], points[0]), strict=True):
            key = tuple(sorted((vertex(a), vertex(b))))
            if key not in face_ids:
                face_ids[key] = len(faces)
                faces.append(key)
            cell.append(face_ids[key])
        cells.append(tuple(cell))
    groups = {"ground": [], "far": [], "pipe_A": []}
    for face, (a, b) in enumerate(faces):
        if sum(face in cell for cell in cells) != 1:
            continue
        p, q = vertices[a], vertices[b]
        name = (
            "ground"
            if p[1] == q[1] == 0
            else "pipe_A"
            if p[1] == q[1] == -1 and 1 <= p[0] <= 3 and 1 <= q[0] <= 3
            else "far"
        )
        groups[name].append(face)
    mesh = SoilCrossSectionMesh(
        vertices_m=tuple(vertices),
        face_vertex_ids=tuple(faces),
        cell_face_ids=tuple(cells),
        boundary_face_groups={key: tuple(ids) for key, ids in groups.items()},
        axial_extrusion_length_m=2.0,
        ground_surface_y_m=0.0,
        maximum_reviewed_nonorthogonality_deg=85.0,
    )
    request, _ = MAKE_CASE(reviewed=True)
    boundaries = tuple(b for b in request.boundaries if b.id != "pipe_B")
    with pytest.raises(ValueError, match="hanging|overlapping|positive polygon"):
        validate_mesh(mesh, boundaries)
