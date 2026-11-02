"""Plane-stress principal and von Mises invariant check on synthetic inputs.

The pinned materials owner evaluates the request; independent scalar-tensor
invariants in this test are the reference.
"""

import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.material_exchange import evaluate_material_job, prepare_material_job


CASES = (
    (120e6, 0.0, 0.0),
    (0.0, 0.0, 80e6),
    (100e6, 100e6, 0.0),
    (-60e6, 140e6, 35e6),
    (220e6, -90e6, -45e6),
)


def _context():
    return RunContext(
        asset_id="synthetic_plane_stress",
        scenario_id="multiaxial_invariant_cases",
        clock_id="synthetic-static",
        origin="synthetic",
        evidence=(EvidenceRef(
            id="tensor", citation="Independently authored stress tensor components", origin="synthetic", license_id="MIT"
        ),),
    )


@pytest.mark.parametrize("sx,sy,txy", CASES)
def test_plane_stress_principal_values_and_von_mises_match_tensor_invariants(sx, sy, txy):
    context = _context()
    job = prepare_material_job(
        "mechanics.plane_stress",
        {"stress_x_Pa": sx, "stress_y_Pa": sy, "shear_xy_Pa": txy},
        context,
        location_id="synthetic_shell_point",
        evidence_ids=("tensor",),
    )

    receipt = evaluate_material_job(job, context)

    mean = (sx + sy) / 2.0
    radius = math.hypot((sx - sy) / 2.0, txy)
    expected_max = mean + radius
    expected_min = mean - radius
    expected_vm = math.sqrt(sx**2 - sx * sy + sy**2 + 3.0 * txy**2)
    values = receipt.native.payload["values"]
    assert receipt.native.status == "computed"
    assert values["principal_max_Pa"] == pytest.approx(expected_max, rel=2e-12, abs=1e-6)
    assert values["principal_min_Pa"] == pytest.approx(expected_min, rel=2e-12, abs=1e-6)
    assert values["von_mises_Pa"] == pytest.approx(expected_vm, rel=2e-12, abs=1e-6)
    assert receipt.field_or_design_acceptance is False
