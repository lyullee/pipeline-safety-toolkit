"""Cyclic Ramberg-Osgood constitutive relation closed-form references.

Inputs are synthetic. The materials owner calculates strain amplitudes; this
suite verifies the elastic/plastic split and constitutive reconstruction.
"""

import math
import runpy

import pytest

from pipeline_integrity.characterization import evaluate_characterization


CASES = (
    (0.0, 200e9, 1.0e9, 0.20),
    (100e6, 200e9, 1.0e9, 0.20),
    (200e6, 200e9, 1.0e9, 0.20),
    (500e6, 210e9, 1.0e9, 0.20),
    (800e6, 205e9, 1.2e9, 0.25),
)


@pytest.mark.parametrize("stress,modulus,strength,exponent", CASES)
def test_cyclic_ramberg_osgood_elastic_plastic_split_and_inverse_closure(stress, modulus, strength, exponent):
    requests, context = runpy.run_path("examples/characterization.py")["make_cases"]()
    request = requests["materials.ramberg_osgood"].model_copy(update={
        "parameters": {
            "stress_amplitude_Pa": stress,
            "elastic_modulus_Pa": modulus,
            "cyclic_strength_coefficient_Pa": strength,
            "cyclic_strain_hardening_exponent": exponent,
        }
    })

    record = evaluate_characterization(request, context)

    elastic = stress / modulus
    plastic = (stress / strength) ** (1.0 / exponent)
    total = elastic + plastic
    values = record["values"]
    assert record["status"] == "computed"
    assert values["elastic_strain_amplitude"] == pytest.approx(elastic, rel=2e-12, abs=1e-15)
    assert values["plastic_strain_amplitude"] == pytest.approx(plastic, rel=2e-12, abs=1e-15)
    assert values["total_strain_amplitude"] == pytest.approx(total, rel=2e-12, abs=1e-15)
    assert values["total_strain_amplitude"] == pytest.approx(
        values["elastic_strain_amplitude"] + values["plastic_strain_amplitude"], rel=2e-12, abs=1e-15
    )
    if stress > 0:
        reconstructed = strength * values["plastic_strain_amplitude"] ** exponent
        assert reconstructed == pytest.approx(stress, rel=3e-12, abs=1e-6)
    else:
        assert math.isfinite(values["total_strain_amplitude"])
