"""TOFD symmetric-tip depth geometry and wall-ligament references.

This checks a geometric time-of-flight transformation with synthetic inputs;
it is not a probability-of-detection or flaw-acceptance assessment.
"""

import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.material_exchange import evaluate_material_job, prepare_material_job


CASES = (
    (0.030, 6000.0, 1.0e-6, 0.020),
    (0.040, 5900.0, 2.0e-6, 0.030),
    (0.100, 5800.0, 3.0e-6, 0.020),
    (0.020, 6000.0, 0.0, 0.020),
)


def _context():
    return RunContext(
        asset_id="synthetic_tofd_specimen",
        scenario_id="symmetric_tip_path_geometry",
        clock_id="synthetic-instrument-clock",
        origin="synthetic",
        evidence=(EvidenceRef(
            id="tof-geometry", citation="Independently authored probe/time/path geometry", origin="synthetic", license_id="MIT"
        ),),
    )


@pytest.mark.parametrize("separation,speed,delay,wall", CASES)
def test_tofd_symmetric_tip_depth_and_remaining_ligament(separation, speed, delay, wall):
    context = _context()
    job = prepare_material_job(
        "inspection.tofd_depth",
        {
            "probe_index_separation_m": separation,
            "longitudinal_sound_speed_m_s": speed,
            "delay_from_lateral_wave_s": delay,
            "wall_thickness_m": wall,
        },
        context,
        location_id="synthetic_pipe_wall",
        evidence_ids=("tof-geometry",),
    )
    receipt = evaluate_material_job(job, context)

    half_path_difference = speed * delay / 2.0
    expected_depth = math.sqrt(half_path_difference * (separation + half_path_difference))
    values = receipt.native.payload["values"]
    expected_status = "computed" if expected_depth <= wall else "out_of_scope"
    assert receipt.native.status == expected_status
    assert values["tip_depth_m"] == pytest.approx(expected_depth, rel=2e-12, abs=1e-12)
    assert values["remaining_backwall_ligament_m"] == pytest.approx(wall - expected_depth, rel=2e-12, abs=1e-12)
    recovered_half_path = (-separation + math.sqrt(separation**2 + 4.0 * expected_depth**2)) / 2.0
    recovered_delay = 2.0 * recovered_half_path / speed
    assert recovered_delay == pytest.approx(delay, rel=3e-12, abs=1e-15)
