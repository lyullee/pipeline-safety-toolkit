"""Offset-yield intersection references for authored monotonic tensile curves.

The test independently intersects each piecewise-linear measured segment with
the elastic offset line; no material data or acceptance limit is embedded.
"""

import runpy

import pytest

from pipeline_integrity.characterization import evaluate_characterization


CASES = (
    (200e9, 0.002, (0.0, 0.001, 0.003, 0.006), (0.0, 100e6, 300e6, 500e6)),
    (70e9, 0.001, (0.0, 0.0005, 0.0015, 0.003), (0.0, 25e6, 55e6, 100e6)),
    (195e9, 0.001, (0.0, 0.0008, 0.0016, 0.0025), (0.0, 80e6, 160e6, 210e6)),
    (100e9, 0.002, (0.0, 0.001, 0.003, 0.005), (0.0, 50e6, 250e6, 250e6)),
    (210e9, 0.002, (0.0, 0.001, 0.002, 0.004), (0.0, 210e6, 420e6, 300e6)),
)


def _requests():
    return runpy.run_path("examples/characterization.py")["make_cases"]()


@pytest.mark.parametrize("modulus,offset,strains,stresses", CASES)
def test_offset_yield_owner_matches_independent_piecewise_segment_intersection(modulus, offset, strains, stresses):
    requests, context = _requests()
    request = requests["materials.offset_yield"].model_copy(update={
        "parameters": {
            "engineering_strain": list(strains),
            "engineering_stress_Pa": list(stresses),
            "elastic_modulus_Pa": modulus,
            "offset_strain": offset,
        }
    })

    record = evaluate_characterization(request, context)

    residuals = [strain - stress / modulus - offset for strain, stress in zip(strains, stresses, strict=True)]
    bracket = next(
        i for i, (left, right) in enumerate(zip(residuals[:-1], residuals[1:], strict=True)) if left < 0 <= right
    )
    slope = (stresses[bracket + 1] - stresses[bracket]) / (strains[bracket + 1] - strains[bracket])
    expected_strain = (stresses[bracket] - slope * strains[bracket] + modulus * offset) / (modulus - slope)
    expected_stress = modulus * (expected_strain - offset)
    values = record["values"]
    assert record["status"] == "computed"
    assert values["bracket_indices"] == [bracket, bracket + 1]
    assert values["intersection_strain"] == pytest.approx(expected_strain, rel=2e-12, abs=1e-15)
    assert values["offset_yield_stress_Pa"] == pytest.approx(expected_stress, rel=2e-12, abs=1e-5)
    assert record["material_or_test_acceptance"] is False


def test_offset_yield_withholds_curve_without_a_bracketed_crossing():
    requests, context = _requests()
    request = requests["materials.offset_yield"].model_copy(update={
        "parameters": {
            "engineering_strain": [0.0, 0.001, 0.002, 0.003],
            "engineering_stress_Pa": [0.0, 180e6, 360e6, 540e6],
            "elastic_modulus_Pa": 200e9,
            "offset_strain": 0.002,
        }
    })
    record = evaluate_characterization(request, context)
    assert record["status"] == "needs_data"
    assert record["values"] == {}
