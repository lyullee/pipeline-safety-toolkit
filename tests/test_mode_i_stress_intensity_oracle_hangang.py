"""Static mode-I stress-intensity and inverse critical-depth references.

Synthetic values exercise an owner-backed LEFM relation only; no toughness
qualification, code acceptance or propagation model is implied.
"""

import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.material_exchange import evaluate_material_job, prepare_material_job


CASES = (
    (0.002, 100e6, 1.00, 50e6),
    (0.008, 180e6, 1.12, 75e6),
    (0.015, 250e6, 0.95, 90e6),
    (0.004, 0.0, 1.08, 60e6),
    (0.025, 320e6, 1.25, 110e6),
)


def _context():
    return RunContext(
        asset_id="synthetic_static_crack",
        scenario_id="mode_i_stress_intensity_reference",
        clock_id="synthetic-static",
        origin="synthetic",
        evidence=(EvidenceRef(
            id="crack-reference", citation="Independently authored LEFM scalar inputs", origin="synthetic", license_id="MIT"
        ),),
    )


def _run(depth, stress, geometry, toughness, context):
    job = prepare_material_job(
        "fracture.stress_intensity",
        {
            "crack_depth_m": depth,
            "tensile_stress_Pa": stress,
            "geometry_factor": geometry,
            "fracture_toughness_Pa_sqrt_m": toughness,
        },
        context,
        location_id="synthetic_crack_tip",
        evidence_ids=("crack-reference",),
    )
    receipt = evaluate_material_job(job, context)
    assert receipt.native.status == "computed"
    return receipt.native.payload["values"]


@pytest.mark.parametrize("depth,stress,geometry,toughness", CASES)
def test_mode_i_stress_intensity_and_inverse_critical_depth(depth, stress, geometry, toughness):
    context = _context()
    values = _run(depth, stress, geometry, toughness, context)
    expected_k = geometry * stress * math.sqrt(math.pi * depth)
    expected_critical = None if stress == 0.0 else (toughness / (geometry * stress)) ** 2 / math.pi

    assert values["stress_intensity_Pa_sqrt_m"] == pytest.approx(expected_k, rel=2e-12, abs=1e-6)
    assert values["toughness_margin_Pa_sqrt_m"] == pytest.approx(toughness - expected_k, rel=2e-12, abs=1e-6)
    if expected_critical is None:
        assert values["critical_depth_m"] is None
    else:
        assert values["critical_depth_m"] == pytest.approx(expected_critical, rel=2e-12, abs=1e-12)
        at_critical = _run(expected_critical, stress, geometry, toughness, context)
        assert at_critical["stress_intensity_Pa_sqrt_m"] == pytest.approx(toughness, rel=3e-12, abs=1e-5)
        assert at_critical["toughness_margin_Pa_sqrt_m"] == pytest.approx(0.0, abs=1e-5)
