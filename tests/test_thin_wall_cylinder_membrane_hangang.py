"""Thin-wall closed-end cylinder membrane stress reference cases.

Inputs are synthetic. The owner supplies the supported membrane model; tests
check its exact scaling and reject use beyond its declared thin-wall boundary.
"""

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.material_exchange import evaluate_material_job, prepare_material_job


CASES = (
    (5.0e6, 0.50, 0.010, 2.0e8),
    (8.0e6, 0.75, 0.025, 3.2e8),
    (2.4e6, 0.40, 0.040, 1.2e8),
    (12.0e6, 1.20, 0.120, 4.0e8),
    (1.0e6, 0.25, 0.025, 5.0e7),
)


def _context():
    return RunContext(
        asset_id="synthetic_thin_wall_cylinder",
        scenario_id="closed_end_membrane_references",
        clock_id="synthetic-static",
        origin="synthetic",
        evidence=(EvidenceRef(
            id="shell-inputs", citation="Independently authored membrane-cylinder inputs", origin="synthetic", license_id="MIT"
        ),),
    )


def _run(pressure, radius, thickness, allowable):
    context = _context()
    job = prepare_material_job(
        "mechanics.thin_cylinder",
        {
            "pressure_difference_Pa": pressure,
            "mean_radius_m": radius,
            "wall_thickness_m": thickness,
            "allowable_hoop_stress_Pa": allowable,
        },
        context,
        location_id="synthetic_pipe_wall",
        evidence_ids=("shell-inputs",),
    )
    return evaluate_material_job(job, context)


@pytest.mark.parametrize("pressure,radius,thickness,allowable", CASES)
def test_thin_wall_cylinder_stress_capacity_and_margin_scaling(pressure, radius, thickness, allowable):
    receipt = _run(pressure, radius, thickness, allowable)
    hoop = pressure * radius / thickness
    values = receipt.native.payload["values"]
    assert receipt.native.status == "computed"
    assert values["hoop_stress_Pa"] == pytest.approx(hoop, rel=2e-12, abs=1e-6)
    assert values["axial_stress_Pa"] == pytest.approx(hoop / 2.0, rel=2e-12, abs=1e-6)
    assert values["allowable_pressure_difference_Pa"] == pytest.approx(
        allowable * thickness / radius, rel=2e-12, abs=1e-6
    )
    assert values["hoop_margin_Pa"] == pytest.approx(allowable - hoop, rel=2e-12, abs=1e-6)
    assert receipt.field_or_design_acceptance is False


def test_membrane_owner_withholds_thick_wall_geometry_from_this_thin_wall_model():
    receipt = _run(1.0e6, 0.10, 0.011, 2.0e8)
    assert receipt.native.status == "out_of_scope"
    assert receipt.native.payload["values"] == {}
