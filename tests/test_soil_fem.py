"""Native P1 physical references, monotonicity failures and stored-record attacks."""

import math
import runpy
from copy import deepcopy
from pathlib import Path

import pytest

from pipeline_integrity.contracts import digest
from pipeline_integrity.soil_fem import (
    _geometry,
    _run,
    boundary_metadata,
    evaluate_soil_fem,
    to_signal_frames,
    verify_soil_fem_record,
)

MAKE = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/soil_fem.py"))["make_case"]


def reseal(record):
    if record.get("native") is not None:
        record["native_digest"] = digest(record["native"])
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})


@pytest.fixture(scope="module")
def robin():
    q, c = MAKE(mode="robin", shear=0.75, reviewed=True)
    return q, c, evaluate_soil_fem(q, c)


def test_unreviewed_is_lazy_and_verified(monkeypatch):
    monkeypatch.setattr(
        "pipeline_integrity.soil_fem._backend", lambda: pytest.fail("optional backend touched")
    )
    q, c = MAKE()
    result = evaluate_soil_fem(q, c)
    assert result["status"] == "needs_data" and not result["native_called"]
    assert verify_soil_fem_record(result, q, c)["valid"]


@pytest.mark.parametrize(
    "field",
    [
        "conductivity_W_m_K",
        "volumetric_heat_capacity_J_m3_K",
        "temperature_support_K",
        "initial_node_temperature_K",
    ],
)
def test_missing_transient_fields_stop_before_optional_import(monkeypatch, field):
    q, c = MAKE(mode="robin", reviewed=True)
    value = (
        tuple(None for _ in q.initial_node_temperature_K) if field == "initial_node_temperature_K" else None
    )
    monkeypatch.setattr(
        "pipeline_integrity.soil_fem._backend", lambda: pytest.fail("optional backend touched")
    )
    result = evaluate_soil_fem(q.model_copy(update={field: value}), c)
    assert result["status"] == "needs_data" and verify_soil_fem_record(result)["valid"]


@pytest.mark.parametrize(
    "field,value",
    [
        ("conductivity_W_m_K", float("nan")),
        ("volumetric_heat_capacity_J_m3_K", float("inf")),
        ("conductivity_W_m_K", False),
        ("maximum_step_s", 1e-12),
        ("input_units", {}),
    ],
)
def test_invalid_clones_are_not_missing_or_repaired(field, value):
    q, c = MAKE(mode="robin", reviewed=True)
    with pytest.raises(ValueError):
        evaluate_soil_fem(q.model_copy(update={field: value}), c)


@pytest.mark.parametrize("field", ["asset_id", "clock_id", "evidence", "metadata"])
def test_common_context_mismatch_rejected(field):
    q, c = MAKE(mode="robin", reviewed=True)
    changed = "other" if field in {"asset_id", "clock_id"} else () if field == "evidence" else {}
    with pytest.raises(ValueError):
        evaluate_soil_fem(q, c.model_copy(update={field: changed}))


@pytest.mark.parametrize("shear", [0.0, 0.3, 0.75])
def test_skew_linear_physical_flux_and_native_reactions(shear):
    q, c = MAKE(shear=shear, reviewed=True)
    result = evaluate_soil_fem(q, c)
    assert result["status"] == "computed"
    row = result["native"]["rows"][0]
    assert (
        max(
            abs(t - (300 + 2 * p[1]))
            for t, p in zip(row["node_temperature_K"], q.mesh.vertices_m, strict=True)
        )
        < 1e-10
    )
    assert abs(row["boundary_physical_gradient_power_into_soil_W"]["ground"] - 24) < 1e-9
    ground = next(b for b in q.boundaries if b.id == "ground")
    assert abs(sum(row["dirichlet_node_reaction_W"][str(v)] for v in ground.node_ids) - 24) < 1e-9
    assert row["boundary_weak_power_into_soil_W"]["ground"] is None
    assert row["boundary_weak_power_into_soil_W"]["far"] is None
    assert result["native"]["geometry"]["cavity_count"] == 2
    assert abs(sum(result["native"]["geometry"]["triangle_areas_m2"]) - 22) < 1e-10
    assert verify_soil_fem_record(result, q, c)["valid"]


def test_no_Dirichlet_corner_or_initial_averaging():
    q, c = MAKE(reviewed=True)
    ground = next(b for b in q.boundaries if b.id == "ground")
    bad = ground.model_copy(
        update={"nodal_temperature_K": (tuple(v + 1 for v in ground.nodal_temperature_K[0]),)}
    )
    with pytest.raises(ValueError, match="corner"):
        evaluate_soil_fem(
            q.model_copy(update={"boundaries": tuple(bad if b.id == "ground" else b for b in q.boundaries)}),
            c,
        )
    q, c = MAKE(mode="uniform", reviewed=True)
    with pytest.raises(ValueError, match="initial P1 Dirichlet"):
        evaluate_soil_fem(
            q.model_copy(
                update={"initial_node_temperature_K": tuple(301.0 for _ in q.initial_node_temperature_K)}
            ),
            c,
        )


@pytest.mark.parametrize("shear,refinement", [(0.0, 1), (0.3, 1), (0.75, 1), (0.3, 2), (0.75, 2), (0.75, 4)])
def test_delaunay_lumped_Robin_passes_actual_matrix_and_temperature_bounds(shear, refinement):
    q, c = MAKE(mode="robin", shear=shear, refinement=refinement, reviewed=True)
    result = evaluate_soil_fem(q, c)
    assert result["status"] == "computed"
    for row in result["native"]["rows"]:
        assert row["mass_diagonal"] and row["operator_comparison_passed"] and row["temperature_bound_passed"]
        assert min(row["node_temperature_K"]) >= 300 - 1e-9
        assert max(row["node_temperature_K"]) <= 310 + 1e-9
        if row["step_energy_residual_J"] is not None:
            assert abs(row["step_energy_residual_J"]) < 1e-8


@pytest.mark.parametrize("shear", [0.0, 0.3, 0.75])
def test_consistent_mass_failure_is_preserved_and_cannot_export(shear):
    q, c = MAKE(
        mode="robin",
        shear=shear,
        reviewed=True,
        mass_scheme="consistent",
        triangulation="convex_fan",
        robin_quadrature="consistent",
    )
    result = evaluate_soil_fem(q, c)
    assert result["status"] == "diagnostic_rejected" and result["native"]["status"] == "computed"
    assert max(max(r["node_temperature_K"]) for r in result["native"]["rows"]) > 310.1
    assert any(not r["mass_diagonal"] for r in result["native"]["rows"])
    assert verify_soil_fem_record(result, q, c)["valid"]
    with pytest.raises(ValueError):
        to_signal_frames(result, c)


@pytest.mark.parametrize("shear", [0.3, 0.75])
def test_lumped_mass_alone_does_not_hide_obtuse_fan_overshoot(shear):
    q, c = MAKE(
        mode="robin",
        shear=shear,
        refinement=2,
        reviewed=True,
        triangulation="convex_fan",
        robin_quadrature="consistent",
    )
    result = evaluate_soil_fem(q, c)
    assert result["status"] == "diagnostic_rejected"
    assert any(r["maximum_positive_operator_offdiagonal_W_K"] > 0.01 for r in result["native"]["rows"])
    assert max(max(r["node_temperature_K"]) for r in result["native"]["rows"]) > 310.001


def test_positive_and_negative_flux_have_source_aware_bounds_and_storage():
    for sign in (1.0, -1.0):
        q, c = MAKE(mode="flux", shear=0.75, reviewed=True)
        q = q.model_copy(
            update={
                "boundaries": tuple(
                    b.model_copy(
                        update={
                            "heat_flux_into_soil_W_m2": tuple(
                                tuple(sign * v for v in row) for row in b.heat_flux_into_soil_W_m2
                            )
                        }
                    )
                    if b.kind == "heat_flux"
                    else b
                    for b in q.boundaries
                )
            }
        )
        result = evaluate_soil_fem(q, c)
        assert result["status"] == "computed"
        row = result["native"]["rows"][-1]
        # Shear preserves horizontal faces but lengthens the cavity's vertical sides.
        perimeter = 2 + 2 * math.sqrt(1 + 0.75**2)
        expected = sign * 10 * perimeter * q.mesh.axial_extrusion_length_m * 0.2
        assert math.isclose(row["stored_energy_change_J"], expected, abs_tol=1e-8)
        assert row["temperature_bound_passed"]
        if sign > 0:
            assert max(row["node_temperature_K"]) > 300
            assert row["source_aware_upper_bound_K"] > 300


def test_boundary_face_order_is_preserved_without_sorting_or_spatial_mix():
    q, c = MAKE(mode="robin", shear=0.3, reviewed=True)
    q = q.model_copy(
        update={
            "boundaries": tuple(
                b.model_copy(
                    update={
                        "convection_W_m2_K": tuple(
                            tuple(0.5 + i / 10 for i in range(len(row))) for row in b.convection_W_m2_K
                        )
                    }
                )
                for b in q.boundaries
            )
        }
    )
    first = evaluate_soil_fem(q, c)
    mesh = q.mesh.model_copy(
        update={
            "boundary_face_groups": {k: tuple(reversed(v)) for k, v in q.mesh.boundary_face_groups.items()}
        }
    )
    changed = q.model_copy(
        update={
            "mesh": mesh,
            "boundaries": tuple(
                b.model_copy(
                    update={
                        "convection_W_m2_K": tuple(tuple(reversed(row)) for row in b.convection_W_m2_K),
                        "ambient_temperature_K": tuple(
                            tuple(reversed(row)) for row in b.ambient_temperature_K
                        ),
                    }
                )
                for b in q.boundaries
            ),
        }
    )
    second = evaluate_soil_fem(changed, c)
    assert first["status"] == second["status"] == "computed"
    for a, b in zip(
        first["native"]["rows"][-1]["node_temperature_K"],
        second["native"]["rows"][-1]["node_temperature_K"],
        strict=True,
    ):
        assert abs(a - b) < 1e-10


@pytest.mark.parametrize(
    "mutation",
    [
        "node",
        "mean",
        "physical",
        "weak",
        "cum",
        "backend",
        "geometry",
        "matrix",
        "operator",
        "probability",
        "allocation",
        "clock",
        "producer",
        "qualification",
        "storage_quadrature",
    ],
)
def test_rehashed_native_corruptions_fail_semantic_reconstruction(robin, mutation):
    q, c, original = robin
    record = deepcopy(original)
    native = record["native"]
    row = native["rows"][-1]
    if mutation == "node":
        row["node_temperature_K"][8] += 0.1
    elif mutation == "mean":
        row["cell_mean_from_P1_K"][0] += 0.1
    elif mutation == "physical":
        row["boundary_physical_gradient_power_into_soil_W"]["ground"] += 1
    elif mutation == "weak":
        row["boundary_weak_power_into_soil_W"]["ground"] += 1
    elif mutation == "cum":
        row["cumulative_total_boundary_energy_J"] += 1
    elif mutation == "backend":
        native["backend"]["source_tree_sha256"] = "0" * 64
    elif mutation == "geometry":
        native["geometry"]["cavity_count"] = 3
    elif mutation == "matrix":
        native["stiffness_digest"] = "0" * 64
    elif mutation == "operator":
        row["operator_comparison_passed"] = False
    elif mutation == "probability":
        native["physical_gradient_power_is_conservative_weak_reaction"] = True
    elif mutation == "allocation":
        native["dirichlet_group_reaction_allocatable"]["ground"] = True
    elif mutation == "clock":
        row["time_s"] += 0.01
    elif mutation == "producer":
        record["producer_sha256"] = "0" * 64
    elif mutation == "storage_quadrature":
        row["independent_P1_quadrature_energy_change_J"] += 1
    else:
        record["qualification"] = "field-approved"
    reseal(record)
    assert not verify_soil_fem_record(record, q, c)["valid"]


def test_consistently_replayed_wrong_interior_state_cannot_be_promoted():
    q, c = MAKE(mode="uniform", reviewed=True)
    result = evaluate_soil_fem(q, c)
    states = [r["node_temperature_K"] for r in result["native"]["rows"]]
    boundary = {v for b in q.boundaries for v in b.node_ids}
    interior = [i for i in range(len(q.mesh.vertices_m)) if i not in boundary]
    states[-1][interior[0]] += 0.1
    result["native"] = _run(q, _geometry(q), replay=states)
    assert not result["native"]["rows"][-1]["local_equations_passed"]
    result["status"] = "computed"
    reseal(result)
    assert not verify_soil_fem_record(result, q, c)["valid"]


def test_signal_semantics_and_unknown_shared_corner_reactions():
    q, c = MAKE(reviewed=True)
    result = evaluate_soil_fem(q, c)
    frame = to_signal_frames(result, c)[0]
    assert frame.time_s == 20 and frame.clock_id == c.clock_id
    assert frame.values["boundary.ground.steady_weak_W"] is None
    assert not frame.valid["boundary.ground.steady_weak_W"]
    assert frame.units["node.0.P1_temperature_K"] == "K"
    assert frame.units["cell.0.mean_from_P1_K"] == "K"
    q, c = MAKE(mode="robin", reviewed=True)
    frames = to_signal_frames(evaluate_soil_fem(q, c), c)
    assert frames[0].values["boundary.ground.backward_euler_weak_W"] is None
    assert not frames[0].valid["boundary.ground.backward_euler_weak_W"]
    assert frames[-1].values["total.cumulative_boundary_J"] < 0
    with pytest.raises(ValueError):
        to_signal_frames(result, c.model_copy(update={"clock_id": "wrong"}))


def test_actual_interrupted_prefix_requires_opt_in(monkeypatch):
    import skfem

    original = skfem.solve
    calls = 0

    def interrupted(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("authored interruption after one actual solve")
        return original(*args, **kwargs)

    monkeypatch.setattr(skfem, "solve", interrupted)
    q, c = MAKE(mode="robin", reviewed=True)
    result = evaluate_soil_fem(q, c)
    assert result["status"] == "partial" and len(result["native"]["rows"]) == 2
    assert verify_soil_fem_record(result, q, c)["valid"]
    with pytest.raises(ValueError):
        to_signal_frames(result, c)
    frames = to_signal_frames(result, c, allow_partial=True)
    assert len(frames) == 2 and frames[-1].operating_mode == "soil-FEM-partial-prefix"


def test_source_property_support_is_not_fixed_by_clipping(robin):
    q, c, _ = robin
    q = q.model_copy(update={"temperature_support_K": (300.0, 309.0)})
    result = evaluate_soil_fem(q, c)
    assert (
        result["status"] == "out_of_scope" and result["native"]["rows"][0]["node_temperature_K"][0] == 310.0
    )
    with pytest.raises(ValueError):
        to_signal_frames(result, c)
    assert boundary_metadata(q) == c.metadata["soil_fem_boundary"]


def test_steady_unknown_reference_is_withheld_before_native(monkeypatch):
    q, c = MAKE(reviewed=True)
    boundaries = tuple(
        b.model_copy(update={"kind": "adiabatic", "node_ids": (), "nodal_temperature_K": None})
        for b in q.boundaries
    )
    monkeypatch.setattr(
        "pipeline_integrity.soil_fem._backend", lambda: pytest.fail("underconstrained native solve")
    )
    result = evaluate_soil_fem(q.model_copy(update={"boundaries": boundaries}), c)
    assert result["status"] == "needs_data" and not result["native_called"]


def test_reviewed_missing_optional_dependency_is_explicit(monkeypatch):
    def absent():
        raise ImportError("install the separately selected soil-fem extra")

    monkeypatch.setattr("pipeline_integrity.soil_fem._backend", absent)
    q, c = MAKE(reviewed=True)
    with pytest.raises(ImportError):
        evaluate_soil_fem(q, c)


@pytest.mark.parametrize("mixed", [False, True])
def test_total_Dirichlet_and_mixed_Robin_storage_sources(mixed):
    q, c = MAKE(mode="robin" if mixed else "uniform", shear=0.75, reviewed=True)
    if mixed:
        from pipeline_integrity.soil_fem import SoilFEMBoundary

        boundaries = []
        for b in q.boundaries:
            if b.id == "ground":
                nodes = tuple(
                    sorted({v for f in q.mesh.boundary_face_groups[b.id] for v in q.mesh.face_vertex_ids[f]})
                )
                b = SoilFEMBoundary(
                    id=b.id,
                    role=b.role,
                    kind="temperature",
                    node_ids=nodes,
                    nodal_temperature_K=tuple(tuple(300.0 for _ in nodes) for _ in q.time_s),
                    evidence_id=b.evidence_id,
                )
            else:
                b = b.model_copy(
                    update={
                        "ambient_temperature_K": tuple(
                            tuple(330.0 for _ in row) for row in b.ambient_temperature_K
                        )
                    }
                )
            boundaries.append(b)
        q = q.model_copy(
            update={
                "initial_node_temperature_K": tuple(300.0 for _ in q.initial_node_temperature_K),
                "boundaries": tuple(boundaries),
            }
        )
    else:
        q = q.model_copy(
            update={
                "boundaries": tuple(
                    b.model_copy(
                        update={
                            "nodal_temperature_K": tuple(
                                tuple(t for _ in b.node_ids) for t in (300.0, 302.0, 301.0)
                            )
                        }
                    )
                    for b in q.boundaries
                )
            }
        )
    result = evaluate_soil_fem(q, c)
    assert result["status"] == "computed" and verify_soil_fem_record(result, q, c)["valid"]
    for row in result["native"]["rows"][1:]:
        assert row["total_dirichlet_weak_power_W"] is not None
        assert abs(row["stored_energy_change_J"] - row["independent_P1_quadrature_energy_change_J"]) < 1e-8
        natural = sum(
            row["boundary_weak_power_into_soil_W"][b.id] for b in q.boundaries if b.kind != "temperature"
        )
        assert abs(row["total_boundary_weak_power_W"] - row["total_dirichlet_weak_power_W"] - natural) < 1e-8
