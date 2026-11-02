"""Mass conservation checks for the declared complete-mixing weld model."""

import runpy

import pytest

from pipeline_integrity.welding_exchange import evaluate_welding


CASES, _, _, _, _, CONTEXT = runpy.run_path("examples/welding_methods.py")["make_cases"]()
BASE = CASES["welding.dilution"]


def evaluate(parts, requested=("C", "Cr", "Ni")):
    parameters = dict(BASE.parameters)
    parameters["constituents"] = parts
    parameters["requested_elements"] = list(requested)
    return evaluate_welding(BASE.model_copy(update={"parameters": parameters}), CONTEXT)


def constituent(material_id, role, mass, **composition):
    return {
        "material_id": material_id,
        "role": role,
        "melted_mass_kg": mass,
        "composition_mass_fraction": composition,
    }


@pytest.mark.parametrize(
    "masses,compositions",
    [
        ((0.002, 0.001), ((0.01, 0.10, 0.0), (0.02, 0.20, 0.10))),
        ((0.003, 0.001), ((0.04, 0.12, 0.02), (0.01, 0.42, 0.08))),
        ((0.001, 0.001, 0.002), ((0.01, 0.1, 0.2), (0.02, 0.2, 0.1), (0.03, 0.3, 0.0))),
    ],
)
def test_mixed_composition_matches_mass_weighted_element_conservation(masses, compositions):
    roles = ("base", "filler", "previous_layer")
    parts = [
        constituent(f"m{i}", roles[i], mass, C=chem[0], Cr=chem[1], Ni=chem[2])
        for i, (mass, chem) in enumerate(zip(masses, compositions, strict=True))
    ]
    result = evaluate(parts)
    total = sum(masses)
    expected = {
        element: sum(mass * chem[i] for mass, chem in zip(masses, compositions, strict=True)) / total
        for i, element in enumerate(("C", "Cr", "Ni"))
    }
    assert result["status"] == "computed"
    assert result["values"]["total_melted_mass_kg"] == pytest.approx(total)
    for element, value in expected.items():
        assert result["values"]["mixed_composition_mass_fraction"][element] == pytest.approx(value)
    assert sum(result["values"]["role_mass_fractions"].values()) == pytest.approx(1.0)
    assert result["native_receipt"]["native"]["payload"]["model_id"] == "welding.dilution"


def test_element_with_missing_constituent_evidence_is_unknown_not_zero():
    parts = [
        constituent("base", "base", 3.0, C=0.01, Cr=0.10),
        constituent("filler", "filler", 1.0, C=0.02, Cr=None),
    ]
    result = evaluate(parts, requested=("C", "Cr"))
    vals = result["values"]
    assert vals["mixed_composition_mass_fraction"]["C"] == pytest.approx(0.0125)
    assert vals["mixed_composition_mass_fraction"]["Cr"] is None
    assert vals["constituents_missing_element_evidence"]["Cr"] == ["filler"]


def test_constituent_order_does_not_change_mass_balance():
    parts = [
        constituent("base", "base", 2.0, C=0.02, Cr=0.1),
        constituent("filler", "filler", 1.0, C=0.04, Cr=0.3),
        constituent("overlay", "previous_layer", 3.0, C=0.01, Cr=0.2),
    ]
    forward = evaluate(parts, requested=("C", "Cr"))["values"]
    reverse = evaluate(list(reversed(parts)), requested=("C", "Cr"))["values"]
    assert reverse["total_melted_mass_kg"] == pytest.approx(forward["total_melted_mass_kg"])
    assert reverse["role_mass_fractions"] == pytest.approx(forward["role_mass_fractions"])
    assert reverse["mixed_composition_mass_fraction"] == pytest.approx(forward["mixed_composition_mass_fraction"])


def test_common_mass_scaling_preserves_composition_and_role_fractions():
    parts = [
        constituent("base", "base", 2.0, C=0.01, Cr=0.1),
        constituent("filler", "filler", 1.0, C=0.03, Cr=0.3),
    ]
    original = evaluate(parts, requested=("C", "Cr"))["values"]
    scaled = [dict(p, melted_mass_kg=p["melted_mass_kg"] * 17.0) for p in parts]
    enlarged = evaluate(scaled, requested=("C", "Cr"))["values"]
    assert enlarged["total_melted_mass_kg"] == pytest.approx(17.0 * original["total_melted_mass_kg"])
    assert enlarged["role_mass_fractions"] == pytest.approx(original["role_mass_fractions"])
    assert enlarged["mixed_composition_mass_fraction"] == pytest.approx(original["mixed_composition_mass_fraction"])
