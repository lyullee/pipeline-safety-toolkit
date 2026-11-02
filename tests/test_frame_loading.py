"""Initial-strain recovery, actual restraint, owner input binding and coupled-load provenance."""

import json
import runpy
from copy import deepcopy

import numpy as np
import pytest
from pydantic import ValidationError

from pipeline_integrity.contracts import Pressure, digest
from pipeline_integrity.frame_loading import (
    prepare_pressure_frame,
    prepare_uniform_thermal_frame,
    solve_loaded_frame,
)
from pipeline_integrity.material_exchange import evaluate_pressure_section
from pipeline_integrity.structural import (
    FrameCombination,
    FrameInitialStrain,
    FrameNode,
    FrameRequest,
    FrameSupport,
    solve_frame,
)

EXAMPLE = runpy.run_path("examples/frame_loading.py")
SPY = runpy.run_path("tests/test_material_exchange.py")["ClientSpy"]


@pytest.fixture
def native():
    pytest.importorskip("Pynite")


def initial_case(value, *, fixed=False):
    frame, context = EXAMPLE["reference_frame"]()
    strain = FrameInitialStrain(
        id="reference-strain",
        member_id="pipe",
        case_id="thermal",
        value=value,
        kind="declared_uniform_initial",
        evidence_id="reference",
        uniform_axial_applicability_reviewed=True,
    )
    supports = frame.supports + ((frame.supports[0].model_copy(update={"node_id": "tip"}),) if fixed else ())
    return frame.model_copy(update={"initial_strains": (strain,), "supports": supports}), context


@pytest.mark.parametrize("strain", [0.0, 0.001, -0.001])
@pytest.mark.parametrize("fixed", [False, True])
def test_free_and_fixed_initial_strain_force_recovery(native, strain, fixed):
    frame, context = initial_case(strain, fixed=fixed)
    result = solve_frame(frame, context)
    assert result["status"] == "computed"
    out = result["combinations"]["combined"]
    ea = frame.materials[0].young_modulus_pa * frame.sections[0].area_m2
    assert out["nodes"]["tip"]["translation_m"][0] == pytest.approx(0.0 if fixed else 4 * strain, abs=1e-12)
    for sample in out["members"]["pipe"]["samples"]:
        assert sample["elastic_axial_force_tension_positive_n"] == pytest.approx(
            -ea * strain if fixed else 0.0, abs=1e-6
        )
    assert out["static_balance"]["passed"] and out["nodal_balance"]["passed"]
    if not fixed:
        assert out["static_balance"]["force_limit_n"] < 1.001e-6
        assert max(abs(v) for v in out["members"]["pipe"]["initial_strain_adjusted_local_end_actions"]) < 1e-6
    assert out["nodes"]["anchor"]["reaction_force_n"][0] == pytest.approx(
        ea * strain if fixed else 0.0, abs=1e-6
    )


def test_axial_spring_restraint_is_solved(native):
    frame, context = initial_case(0.001)
    spring = FrameSupport(node_id="tip", translation_springs_n_m={"DX": 1e8}, evidence_id="reference")
    result = solve_frame(frame.model_copy(update={"supports": frame.supports + (spring,)}), context)
    assert result["status"] == "computed"
    out = result["combinations"]["combined"]
    ea = frame.materials[0].young_modulus_pa * frame.sections[0].area_m2
    expected = 0.004 / (1 + 1e8 * 4 / ea)
    assert out["nodes"]["tip"]["translation_m"][0] == pytest.approx(expected, rel=1e-11)
    assert out["members"]["pipe"]["samples"][0]["elastic_axial_force_tension_positive_n"] == pytest.approx(
        -1e8 * expected, abs=1e-6
    )


def test_initial_strain_load_case_factors(native):
    frame, context = initial_case(0.001)
    combinations = tuple(
        FrameCombination(id=str(factor), factors={"thermal": factor}, evidence_id="reference")
        for factor in (-1.0, 0.0, 0.5, 2.0)
    )
    result = solve_frame(frame.model_copy(update={"combinations": combinations}), context)
    assert result["status"] == "computed"
    for factor in (-1.0, 0.0, 0.5, 2.0):
        out = result["combinations"][str(factor)]
        assert out["nodes"]["tip"]["translation_m"][0] == pytest.approx(factor * 0.004, abs=1e-12)
        assert abs(out["members"]["pipe"]["samples"][0]["elastic_axial_force_tension_positive_n"]) < 1e-6


def test_heated_and_unheated_members_in_series(native):
    frame, context = initial_case(0.001, fixed=True)
    joint = FrameNode(id="joint", position_m=(2.0, 0.0, 0.0))
    first = frame.members[0].model_copy(update={"j_node": "joint"})
    second = first.model_copy(update={"id": "unheated", "i_node": "joint", "j_node": "tip"})
    result = solve_frame(
        frame.model_copy(update={"nodes": frame.nodes + (joint,), "members": (first, second)}), context
    )
    assert result["status"] == "computed"
    out = result["combinations"]["combined"]
    assert out["nodes"]["joint"]["translation_m"][0] == pytest.approx(0.001, abs=1e-12)
    ea = frame.materials[0].young_modulus_pa * frame.sections[0].area_m2
    for member in out["members"].values():
        assert member["samples"][0]["elastic_axial_force_tension_positive_n"] == pytest.approx(
            -0.0005 * ea, abs=1e-6
        )
    assert out["nodal_balance"]["passed"]


def test_initial_strain_on_rotated_and_branched_frame(native):
    frame, context = initial_case(0.001)
    joint = FrameNode(id="joint", position_m=(2.0, 0.0, 0.0))
    nodes = (frame.nodes[0], joint, frame.nodes[1].model_copy(update={"position_m": (2.0, 2.0, 1.0)}))
    first = frame.members[0].model_copy(update={"j_node": "joint"})
    second = first.model_copy(update={"id": "turn", "i_node": "joint", "j_node": "tip"})
    strain = frame.initial_strains[0].model_copy(update={"id": "second", "member_id": "turn", "value": 0.002})
    result = solve_frame(
        frame.model_copy(
            update={
                "nodes": nodes,
                "members": (first, second),
                "initial_strains": frame.initial_strains + (strain,),
            }
        ),
        context,
    )
    assert result["status"] == "computed"
    out = result["combinations"]["combined"]
    assert out["nodes"]["tip"]["translation_m"] == pytest.approx([0.002, 0.004, 0.002], abs=1e-11)
    for member in out["members"].values():
        assert max(abs(v) for v in member["initial_strain_adjusted_global_end_actions"]) < 1e-5


def thermal_spy(strain=0.0005):
    spy = SPY()
    spy.description["output_units"] = dict(free_thermal_strain="1", restrained_uniaxial_stress_Pa="Pa")
    spy.values = dict(free_thermal_strain=strain, restrained_uniaxial_stress_Pa=0.0)
    return spy


def hydrostatic_source(frame, context, *, closed=True):
    spy = SPY()
    spy.description["output_units"] = dict(
        hoop_stress_Pa="Pa", radial_stress_Pa="Pa", axial_stress_Pa="Pa", von_mises_Pa="Pa"
    )
    spy.values = dict(
        hoop_stress_Pa=-1e5, radial_stress_Pa=-1e5, axial_stress_Pa=-1e5 if closed else 0.0, von_mises_Pa=0.0
    )
    section = EXAMPLE["pressure_section"](frame).model_copy(
        update={
            "internal_pressure": Pressure(value=1e5, unit="Pa", reference="absolute"),
            "pressure_loaded_closed_ends": closed,
        }
    )
    return evaluate_pressure_section(section, context, client=spy)


@pytest.mark.parametrize("closed", [True, False])
@pytest.mark.parametrize("fixed", [True, False])
def test_pressure_poisson_and_end_condition(native, closed, fixed):
    frame, context = EXAMPLE["reference_frame"]()
    if fixed:
        frame = frame.model_copy(
            update={"supports": frame.supports + (frame.supports[0].model_copy(update={"node_id": "tip"}),)}
        )
    section = hydrostatic_source(frame, context, closed=closed)
    contribution = prepare_pressure_frame(frame, section, EXAMPLE["pressure_mapping"](section), context)
    assert contribution.preparation_status == "ready"
    result = solve_loaded_frame(frame, (contribution,), context)
    assert result["status"] == "computed"
    out = result["combinations"]["combined"]
    e, nu = frame.materials[0].young_modulus_pa, frame.materials[0].poisson_ratio
    expected_stress = -2 * nu * 1e5 if fixed else -1e5 if closed else 0.0
    expected_strain = 0.0 if fixed else (-1 + 2 * nu) * 1e5 / e if closed else 2 * nu * 1e5 / e
    assert out["nodes"]["tip"]["translation_m"][0] == pytest.approx(4 * expected_strain, abs=1e-12)
    assert out["members"]["pipe"]["samples"][0]["mean_elastic_axial_stress_pa"] == pytest.approx(
        expected_stress, abs=1e-5
    )
    assert out["nodal_balance"]["passed"]


def test_prepared_thermal_owner_and_composition(native):
    frame, context = EXAMPLE["reference_frame"]()
    spy = thermal_spy()
    thermal = prepare_uniform_thermal_frame(frame, EXAMPLE["temperature"](), context, client=spy)
    assert spy.calls[0]["restraint_fraction"] == 0.0
    assert thermal.preparation_status == "ready" and thermal.source_records
    source = hydrostatic_source(frame, context)
    pressure = prepare_pressure_frame(frame, source, EXAMPLE["pressure_mapping"](source), context)
    result = solve_loaded_frame(frame, (thermal, pressure), context)
    assert result["status"] == "computed" and len(result["loading_contributions"]) == 2
    e, nu = frame.materials[0].young_modulus_pa, frame.materials[0].poisson_ratio
    expected = 4 * (0.0005 - (1 - 2 * nu) * 1e5 / e)
    assert result["combinations"]["combined"]["nodes"]["tip"]["translation_m"][0] == pytest.approx(
        expected, abs=1e-12
    )


@pytest.mark.parametrize(
    "field,value,status",
    [
        ("temperature_k", None, "needs_data"),
        ("stress_free_temperature_k", None, "needs_data"),
        ("expansion_coefficient_per_k", None, "needs_data"),
        ("coefficient_temperature_support_k", None, "needs_data"),
        ("uniform_temperature_and_constant_coefficient_reviewed", False, "needs_data"),
        ("temperature_k", 500.0, "out_of_scope"),
        ("stress_free_temperature_k", 270.0, "out_of_scope"),
    ],
)
def test_missing_or_unsupported_thermal_does_not_call_owner(field, value, status):
    frame, context = EXAMPLE["reference_frame"]()
    spy = thermal_spy()
    specification = EXAMPLE["temperature"]().model_copy(update={field: value})
    prepared = prepare_uniform_thermal_frame(frame, specification, context, client=spy)
    assert prepared.preparation_status == status and not spy.calls
    result = solve_loaded_frame(frame, (prepared,), context)
    assert result["status"] == status and not result["combinations"]


def test_prepared_load_changes_and_duplicate_effect_are_rejected():
    frame, context = EXAMPLE["reference_frame"]()
    contribution = prepare_uniform_thermal_frame(
        frame, EXAMPLE["temperature"](), context, client=thermal_spy()
    )
    with pytest.raises(ValueError, match="duplicated"):
        solve_loaded_frame(frame, (contribution, contribution), context)
    with pytest.raises(ValueError, match="changed"):
        solve_loaded_frame(frame.model_copy(update={"time_s": 1.0}), (contribution,), context)
    changed = contribution.model_copy(
        update={"initial_strains": (contribution.initial_strains[0].model_copy(update={"value": 1.0}),)}
    )
    with pytest.raises(ValueError, match="sealing"):
        solve_loaded_frame(frame, (changed,), context)
    with pytest.raises(ValueError, match="changed"):
        solve_loaded_frame(frame, (contribution,), context.model_copy(update={"scenario_id": "other"}))


def test_pressure_record_context_time_geometry_and_review_bindings():
    frame, context = EXAMPLE["reference_frame"]()
    source = hydrostatic_source(frame, context)
    mapping = EXAMPLE["pressure_mapping"](source)
    for field in (
        "straight_uniform_cylinder_applicability_reviewed",
        "end_condition_and_total_pressure_reviewed",
        "poisson_coupling_reviewed",
    ):
        assert (
            prepare_pressure_frame(
                frame, source, mapping.model_copy(update={field: False}), context
            ).preparation_status
            == "needs_data"
        )
    with pytest.raises(ValueError, match="sample time"):
        prepare_pressure_frame(frame.model_copy(update={"time_s": 1.0}), source, mapping, context)
    with pytest.raises(ValueError, match="geometry"):
        prepare_pressure_frame(
            frame.model_copy(
                update={
                    "sections": (frame.sections[0].model_copy(update={"iy_m4": frame.sections[0].iy_m4 * 2}),)
                }
            ),
            source,
            mapping,
            context,
        )
    corrupted = deepcopy(source)
    corrupted["specification"]["pressure_loaded_closed_ends"] = False
    with pytest.raises(ValueError, match="changed"):
        prepare_pressure_frame(frame, corrupted, mapping, context)
    missing = EXAMPLE["pressure_section"](frame).model_copy(update={"internal_pressure": None})
    receipt = evaluate_pressure_section(missing, context)
    assert (
        prepare_pressure_frame(
            frame, receipt, EXAMPLE["pressure_mapping"](receipt), context
        ).preparation_status
        == "needs_data"
    )


def test_unknown_initial_strain_strictness_and_large_material_strain(native):
    frame, context = initial_case(None)
    assert solve_frame(frame, context)["status"] == "needs_data"
    frame, context = initial_case(0.1, fixed=True)
    result = solve_frame(frame, context)
    assert result["status"] == "out_of_scope" and result["combinations"]
    with pytest.raises(ValidationError):
        FrameInitialStrain.model_validate_json(
            json.dumps(
                dict(
                    id="bool",
                    member_id="pipe",
                    case_id="thermal",
                    value=True,
                    kind="thermal",
                    evidence_id="reference",
                )
            )
        )
    duplicate = frame.model_copy(
        update={"initial_strains": (frame.initial_strains[0], frame.initial_strains[0])}
    )
    with pytest.raises(ValidationError):
        FrameRequest.model_validate(duplicate.model_dump())


def test_initial_strain_effects_are_serializable_without_readjusting_signs(native):
    frame, context = initial_case(-0.001, fixed=True)
    result = solve_frame(frame, context)
    assert result["record_digest"] == digest({k: v for k, v in result.items() if k != "record_digest"})
    values = result["combinations"]["combined"]["members"]["pipe"]
    assert values["samples"][0]["elastic_axial_force_tension_positive_n"] > 0
    assert np.asarray(values["initial_strain_adjusted_local_end_actions"])[0] < 0
