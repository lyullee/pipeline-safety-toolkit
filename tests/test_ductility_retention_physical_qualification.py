"""Local physical checks for explicitly matched ductility comparisons."""

import runpy

import pytest

from pipeline_integrity.characterization import evaluate_characterization

CASES = ((0.20, 0.15), (0.12, 0.12), (0.08, 0.10))


@pytest.mark.parametrize("baseline,exposed", CASES)
def test_matched_elongation_ratio_and_signed_loss(baseline, exposed):
    requests, context = runpy.run_path("examples/characterization.py")["make_cases"]()
    request = requests["materials.ductility_retention"].model_copy(
        update={
            "parameters": {
                "baseline_elongation_fraction": baseline,
                "exposed_elongation_fraction": exposed,
            }
        }
    )
    record = evaluate_characterization(request, context)
    retention = exposed / baseline
    assert record["status"] == "computed"
    assert record["values"]["ductility_retention_ratio"] == pytest.approx(retention, rel=1e-14)
    assert record["values"]["relative_ductility_loss"] == pytest.approx(1 - retention, rel=1e-14, abs=1e-14)
    assert record["material_or_test_acceptance"] is False
