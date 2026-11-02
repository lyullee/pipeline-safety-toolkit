"""Independent thin-wall asymptotic comparison against the Lamé solution.

Both calculations are delegated to their source-pinned materials owner. The
reference computes differential-pressure stresses and error order separately.
"""

import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, Pressure, RunContext
from pipeline_integrity.material_exchange import evaluate_material_job, prepare_material_job


RADIUS_M = 0.50
EXTERNAL_ABSOLUTE_PA = 101_325.0
THICKNESS_RATIOS = (0.10, 0.05, 0.025, 0.0125)
PRESSURE_DIFFERENCES_PA = (1.0e6, 3.0e6, 9.0e6)


def _context():
    return RunContext(
        asset_id="synthetic_thin_lame_limit",
        scenario_id="membrane_asymptotic_convergence",
        clock_id="synthetic-static",
        origin="synthetic",
        evidence=(
            EvidenceRef(id="geometry", citation="Independently authored concentric pipe geometry", origin="synthetic", license_id="MIT"),
            EvidenceRef(id="loads", citation="Independently authored absolute pressure boundary conditions", origin="synthetic", license_id="MIT"),
        ),
    )


def _evaluate(model, parameters, context, evidence_ids):
    job = prepare_material_job(
        model, parameters, context, location_id="synthetic_pipe_section", evidence_ids=evidence_ids
    )
    receipt = evaluate_material_job(job, context)
    assert receipt.native.status == "computed"
    return receipt.native.payload["values"]


@pytest.mark.parametrize("pressure_difference", PRESSURE_DIFFERENCES_PA)
def test_thin_wall_membrane_stresses_converge_to_thick_wall_lame_limit(pressure_difference):
    context = _context()
    results = []
    for ratio in THICKNESS_RATIOS:
        thickness = RADIUS_M * ratio
        inner = RADIUS_M - thickness / 2.0
        outer = RADIUS_M + thickness / 2.0
        internal_absolute = EXTERNAL_ABSOLUTE_PA + pressure_difference
        lame = _evaluate(
            "mechanics.lame_cylinder",
            {
                "inner_radius_m": inner,
                "outer_radius_m": outer,
                "evaluation_radius_m": inner,
                "internal_pressure_Pa": internal_absolute,
                "external_pressure_Pa": EXTERNAL_ABSOLUTE_PA,
                "closed_ends": True,
            },
            context,
            ("geometry", "loads"),
        )
        membrane = _evaluate(
            "mechanics.thin_cylinder",
            {
                "pressure_difference_Pa": pressure_difference,
                "mean_radius_m": RADIUS_M,
                "wall_thickness_m": thickness,
                "allowable_hoop_stress_Pa": 1.0e9,
            },
            context,
            ("geometry", "loads"),
        )

        # Remove the common external absolute-pressure stress state to compare
        # only the differential-pressure response represented by the membrane model.
        lame_hoop_difference = lame["hoop_stress_Pa"] + EXTERNAL_ABSOLUTE_PA
        lame_axial_difference = lame["axial_stress_Pa"] + EXTERNAL_ABSOLUTE_PA
        a2, b2 = inner**2, outer**2
        denominator = b2 - a2
        independent_hoop = pressure_difference * (a2 + b2) / denominator
        independent_axial = pressure_difference * a2 / denominator
        membrane_hoop = pressure_difference * RADIUS_M / thickness
        membrane_axial = membrane_hoop / 2.0
        assert lame_hoop_difference == pytest.approx(independent_hoop, rel=2e-12, abs=1e-5)
        assert lame_axial_difference == pytest.approx(independent_axial, rel=2e-12, abs=1e-5)
        assert membrane["hoop_stress_Pa"] == pytest.approx(membrane_hoop, rel=2e-12, abs=1e-5)
        assert membrane["axial_stress_Pa"] == pytest.approx(membrane_axial, rel=2e-12, abs=1e-5)
        assert lame_hoop_difference / membrane_hoop - 1.0 == pytest.approx(ratio**2 / 4.0, abs=2e-12)
        assert lame_axial_difference / membrane_axial - 1.0 == pytest.approx(-ratio + ratio**2 / 4.0, abs=2e-12)
        results.append((lame_hoop_difference, lame_axial_difference))

    hoop_relative_errors = [ratio**2 / 4.0 for ratio in THICKNESS_RATIOS]
    assert hoop_relative_errors[1] / hoop_relative_errors[0] == pytest.approx(0.25)
    assert all(a > b for a, b in zip(hoop_relative_errors, hoop_relative_errors[1:]))
    assert all(math.isfinite(v) for pair in results for v in pair)
