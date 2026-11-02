"""Fracture readiness, context and scalar stress-binding policy without duplicating fracture mathematics."""

import runpy
from pathlib import Path

import pytest

from pipeline_integrity.contracts import Pressure, digest
from pipeline_integrity.fracture_exchange import (
    SectionOpeningMapping,
    assess_crack_growth,
    assess_failure_points,
    assess_mode_i,
    crack_arrest_frames,
    opening_stress_from_section,
)

EXAMPLE = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/fracture_screen.py"))


@pytest.mark.parametrize("kind", ["crack_arrest", "unqualified_observation"])
def test_noninitiation_resistance_never_becomes_a_field_kic(kind):
    s = EXAMPLE["make_mode_i"]()
    s = s.model_copy(update={"resistance": s.resistance.model_copy(update={"kind": kind})})
    r = assess_mode_i(s, EXAMPLE["context"]())
    assert r["status"] == "needs_data" and r["native"] is None
    assert not r["failure_probability_calculated"]


@pytest.mark.parametrize(
    "change", [{"effective_opening_stress_pa": None}, {"geometry_factor": None}, {"temperature_k": None}]
)
def test_unknown_input_is_not_replaced_by_a_default(change):
    r = assess_mode_i(EXAMPLE["make_mode_i"]().model_copy(update=change), EXAMPLE["context"]())
    assert r["status"] == "needs_data" and r["native"] is None


@pytest.mark.parametrize(
    "change", [{"effective_opening_stress_pa": -1.0}, {"crack_depth_m": 0.01}, {"temperature_k": 350.0}]
)
def test_compression_breakthrough_or_unsupported_temperature_remain_outside_scope(change):
    r = assess_mode_i(EXAMPLE["make_mode_i"]().model_copy(update=change), EXAMPLE["context"]())
    assert r["status"] == "out_of_scope" and r["native"] is None


def test_material_context_and_review_evidence_are_bound():
    s = EXAMPLE["make_mode_i"]()
    for changed in [
        s.model_copy(
            update={"resistance": s.resistance.model_copy(update={"material_condition_id": "foreign"})}
        ),
        s.model_copy(
            update={"reviews": s.reviews.model_copy(update={"linear_elastic_regime_reviewed": False})}
        ),
    ]:
        assert assess_mode_i(changed, EXAMPLE["context"]())["status"] == "needs_data"
    with pytest.raises(ValueError):
        assess_mode_i(s, EXAMPLE["context"]().model_copy(update={"clock_id": "foreign"}))


def test_growth_requires_qualified_cycles_geometry_calibration_and_resistance():
    s = EXAMPLE["make_growth"]()
    ctx = EXAMPLE["context"]()
    for changed in [
        s.model_copy(update={"cycle_order_reviewed": False}),
        s.model_copy(update={"geometry_factor": None}),
        s.model_copy(
            update={
                "calibration": s.calibration.model_copy(
                    update={"coefficient_m_cycle_per_pa_sqrt_m_power": None}
                )
            }
        ),
    ]:
        r = assess_crack_growth(changed, ctx)
        assert r["status"] == "needs_data" and not r["cycles_converted_to_elapsed_life"]
    for changed in [
        s.model_copy(update={"geometry_factor": (1.0, 0.9)}),
        s.model_copy(update={"terminal_depth_m": s.wall_thickness_m}),
        s.model_copy(
            update={"blocks": (s.blocks[0].model_copy(update={"minimum_opening_stress_pa": -1.0}),)}
        ),
    ]:
        assert assess_crack_growth(changed, ctx)["status"] == "out_of_scope"


def section():
    ctx = EXAMPLE["context"]()
    body = dict(
        context=ctx.model_dump(mode="json"),
        status="computed",
        specification={"evaluation_radii_m": [0.05, 0.06]},
        material_records=[
            {"native": {"payload": {"values": {"hoop_stress_Pa": 1e7, "axial_stress_Pa": 2e6}}}},
            {"native": {"payload": {"values": {"hoop_stress_Pa": 8e6, "axial_stress_Pa": 2e6}}}},
        ],
    )
    body["record_digest"] = digest(body)
    return body


def mapping(**changes):
    return SectionOpeningMapping(
        **(
            dict(
                radius_m=0.05,
                component="hoop_stress_Pa",
                crack_face_pressure=Pressure(value=1e5, unit="Pa", reference="absolute"),
                additional_opening_stress_pa=-1e6,
                interpretation="Authored scalar normal-traction mapping",
                evidence_id="authored",
                scalar_mapping_reviewed=True,
            )
            | changes
        )
    )


def test_section_total_stress_needs_an_explicit_face_traction_and_mapping():
    ctx = EXAMPLE["context"]()
    r = opening_stress_from_section(section(), mapping(), ctx)
    assert r["value_pa"] == 9.1e6
    assert (
        opening_stress_from_section(section(), mapping(crack_face_pressure=None), ctx)["status"]
        == "needs_data"
    )
    assert (
        opening_stress_from_section(section(), mapping(scalar_mapping_reviewed=False), ctx)["status"]
        == "needs_data"
    )
    with pytest.raises(ValueError):
        opening_stress_from_section(section(), mapping(radius_m=0.055), ctx)
    corrupt = section()
    corrupt["material_records"][0]["native"]["payload"]["values"]["hoop_stress_Pa"] = 3e7
    with pytest.raises(ValueError):
        opening_stress_from_section(corrupt, mapping(), ctx)


def test_mode_i_cannot_copy_an_unbound_or_changed_opening_stress_receipt():
    ctx = EXAMPLE["context"]()
    r = opening_stress_from_section(section(), mapping(), ctx)
    s = EXAMPLE["make_mode_i"]().model_copy(
        update={
            "effective_opening_stress_pa": r["value_pa"],
            "opening_stress_definition": r["mapping"]["interpretation"],
            "opening_stress_receipt_digest": r["record_digest"],
            "temperature_k": None,
        }
    )
    assert assess_mode_i(s, ctx, opening_stress_receipt=r)["status"] == "needs_data"
    for other in [None, r | {"value_pa": 1.0}]:
        with pytest.raises(ValueError):
            assess_mode_i(s, ctx, opening_stress_receipt=other)


def test_failure_points_need_same_load_location_and_solution_evidence():
    p = EXAMPLE["failure_points"]()
    p["points"][0]["load_case_id"] = "foreign"
    with pytest.raises(ValueError):
        assess_failure_points(p, EXAMPLE["context"](), location_id="annulus", evidence_ids=("authored",))
    p = EXAMPLE["failure_points"]()
    p["curve"]["evidence"][0]["source_id"] = "unknown"
    with pytest.raises(ValueError):
        assess_failure_points(p, EXAMPLE["context"](), location_id="annulus", evidence_ids=("authored",))


def test_unreviewed_machine_series_cannot_claim_primary_laboratory_origin(tmp_path):
    p = tmp_path / "authored.zip"
    p.write_bytes(b"authored test bytes")
    with pytest.raises(ValueError, match="reviewed source archive"):
        crack_arrest_frames(p, "test")


def test_unqualified_opening_receipt_propagates_readiness_without_a_native_call():
    ctx = EXAMPLE["context"]()
    r = opening_stress_from_section(section(), mapping(crack_face_pressure=None), ctx)
    s = EXAMPLE["make_mode_i"]().model_copy(
        update={
            "effective_opening_stress_pa": None,
            "opening_stress_definition": r["mapping"]["interpretation"],
            "opening_stress_receipt_digest": r["record_digest"],
        }
    )
    result = assess_mode_i(s, ctx, opening_stress_receipt=r)
    assert result["status"] == "needs_data" and result["native"] is None
