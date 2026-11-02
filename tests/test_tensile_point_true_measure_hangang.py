"""Engineering-to-true tensile measure transformations on synthetic inputs.

The material owner retains force/area and extension/gauge source measures;
true conversions are checked only when uniform deformation is declared.
"""

import math
import runpy

import pytest

from pipeline_integrity.characterization import evaluate_characterization


CASES = (
    (20_000.0, 1.0e-4, 0.005, 0.05, True),
    (1_000.0, 5.0e-3, 0.00025, 0.025, True),
    (50_000.0, 2.5e-4, 0.001, 0.02, True),
    (0.0, 1.0e-3, 0.0, 0.10, True),
    (4_500.0, 1.5e-4, 0.0006, 0.04, True),
)


def _base():
    return runpy.run_path("examples/characterization.py")["make_cases"]()


def _run(force, area, extension, gauge, uniform):
    requests, context = _base()
    request = requests["materials.tensile_point"].model_copy(update={
        "parameters": {
            "force_N": force,
            "initial_area_m2": area,
            "extension_m": extension,
            "gauge_length_m": gauge,
            "uniform_deformation_declared": uniform,
        }
    })
    return evaluate_characterization(request, context)


@pytest.mark.parametrize("force,area,extension,gauge,uniform", CASES)
def test_tensile_engineering_and_true_measure_closed_forms(force, area, extension, gauge, uniform):
    record = _run(force, area, extension, gauge, uniform)
    engineering_stress = force / area
    engineering_strain = extension / gauge
    values = record["values"]
    assert record["status"] == "computed"
    assert values["engineering_stress_Pa"] == pytest.approx(engineering_stress, rel=2e-12, abs=1e-9)
    assert values["engineering_strain"] == pytest.approx(engineering_strain, rel=2e-12, abs=1e-15)
    if uniform:
        assert values["true_stress_Pa"] == pytest.approx(
            engineering_stress * (1.0 + engineering_strain), rel=2e-12, abs=1e-9
        )
        assert values["true_strain"] == pytest.approx(math.log1p(engineering_strain), rel=2e-12, abs=1e-15)


def test_true_stress_and_log_strain_are_omitted_without_uniform_deformation_review():
    record = _run(20_000.0, 1.0e-4, 0.005, 0.05, False)
    assert record["status"] == "computed"
    assert record["values"]["engineering_stress_Pa"] == pytest.approx(200e6)
    assert record["values"]["engineering_strain"] == pytest.approx(0.1)
    assert "true_stress_Pa" not in record["values"]
    assert "true_strain" not in record["values"]
