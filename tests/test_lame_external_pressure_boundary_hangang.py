"""External-pressure dominated thick-cylinder Lamé references.

The fixture is entirely synthetic. It targets compressive outer traction and
checks the registered material owner over the full radial wall profile.
"""

import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.material_exchange import evaluate_material_job, prepare_material_job


CASES = (
    (0.15e6, 12.0e6, 0.050, 0.065, True),
    (0.10e6, 8.0e6, 0.100, 0.130, False),
    (2.00e6, 15.0e6, 0.300, 0.360, True),
    (0.50e6, 3.00e6, 0.080, 0.100, True),
)


def _context():
    return RunContext(
        asset_id="synthetic_external_pressure_shell",
        scenario_id="external_pressure_lame_profile",
        clock_id="synthetic-static",
        origin="synthetic",
        evidence=(
            EvidenceRef(id="geometry", citation="Independently authored thick annulus", origin="synthetic", license_id="MIT"),
            EvidenceRef(id="pressure", citation="Independently authored absolute inner and outer pressure states", origin="synthetic", license_id="MIT"),
        ),
    )


def _run(context, radius, inner, outer, p_inner, p_outer, closed_ends):
    job = prepare_material_job(
        "mechanics.lame_cylinder",
        {
            "inner_radius_m": inner,
            "outer_radius_m": outer,
            "evaluation_radius_m": radius,
            "internal_pressure_Pa": p_inner,
            "external_pressure_Pa": p_outer,
            "closed_ends": closed_ends,
        },
        context,
        location_id="synthetic_outer-pressure-wall",
        evidence_ids=("geometry", "pressure"),
    )
    receipt = evaluate_material_job(job, context)
    assert receipt.native.status == "computed"
    return receipt.native.payload["values"]


@pytest.mark.parametrize("p_inner,p_outer,inner,outer,closed_ends", CASES)
def test_external_pressure_lame_compression_profile_and_both_boundary_tractions(
    p_inner, p_outer, inner, outer, closed_ends
):
    context = _context()
    radii = (inner, math.sqrt(inner * outer), outer)
    denominator = outer**2 - inner**2
    A = (p_inner * inner**2 - p_outer * outer**2) / denominator
    B = (p_inner - p_outer) * inner**2 * outer**2 / denominator

    for radius in radii:
        values = _run(context, radius, inner, outer, p_inner, p_outer, closed_ends)
        hoop = A + B / radius**2
        radial = A - B / radius**2
        axial = A if closed_ends else 0.0
        vm = math.sqrt(((hoop-radial)**2 + (radial-axial)**2 + (axial-hoop)**2) / 2.0)
        assert values["hoop_stress_Pa"] == pytest.approx(hoop, rel=3e-12, abs=1e-5)
        assert values["radial_stress_Pa"] == pytest.approx(radial, rel=3e-12, abs=1e-5)
        assert values["axial_stress_Pa"] == pytest.approx(axial, rel=3e-12, abs=1e-5)
        assert values["von_mises_Pa"] == pytest.approx(vm, rel=3e-12, abs=1e-5)

    inner_state = _run(context, inner, inner, outer, p_inner, p_outer, closed_ends)
    outer_state = _run(context, outer, inner, outer, p_inner, p_outer, closed_ends)
    assert inner_state["radial_stress_Pa"] == pytest.approx(-p_inner, rel=2e-12, abs=1e-5)
    assert outer_state["radial_stress_Pa"] == pytest.approx(-p_outer, rel=2e-12, abs=1e-5)
    assert p_outer > p_inner
