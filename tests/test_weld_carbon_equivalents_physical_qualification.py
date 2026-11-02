"""Declared steel mass-percent carbon-equivalent arithmetic checks."""

import runpy

import pytest

from pipeline_integrity.welding_exchange import evaluate_welding


CASES, _, _, _, _, CONTEXT = runpy.run_path("examples/welding_methods.py")["make_cases"]()
BASE = CASES["welding.carbon_equivalents"]
ELEMENTS = ("C", "Mn", "Cr", "Mo", "V", "Ni", "Cu", "Si", "B")


def evaluate(values):
    parameters = dict(BASE.parameters)
    parameters.update(values)
    return evaluate_welding(BASE.model_copy(update={"parameters": parameters}), CONTEXT)


def expected(c):
    return {
        "CE_IIW": c["C"] + c["Mn"] / 6 + (c["Cr"] + c["Mo"] + c["V"]) / 5 + (c["Ni"] + c["Cu"]) / 15,
        "Pcm": c["C"] + c["Si"] / 30 + (c["Mn"] + c["Cu"] + c["Cr"]) / 20 + c["Ni"] / 60 + c["Mo"] / 15 + c["V"] / 10 + 5 * c["B"],
    }


@pytest.mark.parametrize(
    "composition",
    [
        dict(C=0.08, Mn=1.2, Cr=0.2, Mo=0.1, V=0.03, Ni=0.2, Cu=0.1, Si=0.25, B=0.001),
        dict(C=0.2, Mn=0.0, Cr=0.0, Mo=0.0, V=0.0, Ni=0.0, Cu=0.0, Si=0.0, B=0.0),
        dict(C=0.0, Mn=1.5, Cr=0.5, Mo=0.25, V=0.1, Ni=0.4, Cu=0.2, Si=0.3, B=0.002),
        dict(C=0.12, Mn=0.8, Cr=1.0, Mo=0.4, V=0.06, Ni=1.2, Cu=0.35, Si=0.22, B=0.0015),
    ],
)
def test_ce_iiw_and_pcm_match_independent_mass_percent_formulas(composition):
    result = evaluate(composition)
    assert result["status"] == "computed"
    assert result["values"]["CE_IIW"] == pytest.approx(expected(composition)["CE_IIW"], rel=1e-13)
    assert result["values"]["Pcm"] == pytest.approx(expected(composition)["Pcm"], rel=1e-13)
    assert result["native_receipt"]["native"]["payload"]["model_id"] == "welding.carbon_equivalents"


def test_both_indices_are_homogeneous_in_all_declared_element_percentages():
    composition = {key: float(BASE.parameters[key]) for key in ELEMENTS}
    base = evaluate(composition)
    scaled_composition = {key: value * 0.4 for key, value in composition.items()}
    scaled = evaluate(scaled_composition)
    assert scaled["values"]["CE_IIW"] == pytest.approx(0.4 * base["values"]["CE_IIW"])
    assert scaled["values"]["Pcm"] == pytest.approx(0.4 * base["values"]["Pcm"])


def test_zero_composition_has_zero_indices():
    result = evaluate(dict.fromkeys(ELEMENTS, 0.0))
    assert result["values"]["CE_IIW"] == pytest.approx(0.0)
    assert result["values"]["Pcm"] == pytest.approx(0.0)


def test_declared_chemistry_above_one_hundred_mass_percent_is_rejected():
    invalid = dict.fromkeys(ELEMENTS, 0.0)
    invalid.update(C=60.0, Mn=50.0)
    with pytest.raises(ValueError, match="mass percent"):
        evaluate(invalid)
