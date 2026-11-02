"""Thermal expansion restraint limit checks through the materials owner.

Synthetic single-axis input cases isolate sign, linear scaling and the free /
partial / fully restrained mechanical limits. No design threshold is implied.
"""

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.material_exchange import evaluate_material_job, prepare_material_job


CASES = (
    (200e9, 12e-6, 80.0, 0.0),
    (200e9, 12e-6, 80.0, 1.0),
    (70e9, 23e-6, 120.0, 0.35),
    (195e9, 11.5e-6, -45.0, 0.8),
    (100e9, 0.0, 150.0, 1.0),
)


def _context():
    return RunContext(
        asset_id="synthetic_thermally_restrained_bar",
        scenario_id="uniaxial_restraint_reference",
        clock_id="synthetic-static",
        origin="synthetic",
        evidence=(EvidenceRef(
            id="thermal-mechanics", citation="Independently authored elastic thermal inputs", origin="synthetic", license_id="MIT"
        ),),
    )


@pytest.mark.parametrize("young,alpha,delta_t,restraint", CASES)
def test_uniaxial_thermal_restraint_stress_and_free_strain_match_closed_form(young, alpha, delta_t, restraint):
    context = _context()
    job = prepare_material_job(
        "mechanics.thermal_restraint",
        {
            "young_modulus_Pa": young,
            "expansion_coefficient_per_K": alpha,
            "temperature_change_K": delta_t,
            "restraint_fraction": restraint,
        },
        context,
        location_id="synthetic_axial_member",
        evidence_ids=("thermal-mechanics",),
    )
    receipt = evaluate_material_job(job, context)

    values = receipt.native.payload["values"]
    assert receipt.native.status == "computed"
    assert values["free_thermal_strain"] == pytest.approx(alpha * delta_t, rel=2e-12, abs=1e-15)
    assert values["restrained_uniaxial_stress_Pa"] == pytest.approx(
        -restraint * young * alpha * delta_t, rel=2e-12, abs=1e-6
    )
    if restraint == 0.0:
        assert values["restrained_uniaxial_stress_Pa"] == 0.0
