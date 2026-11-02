"""Lamé thick-cylinder stress profiles with independent mechanics oracles.

The model owner computes the stresses. These synthetic cases independently
check traction boundaries, radial variation, axial end restraint and von Mises.
"""

import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, Pressure, RunContext
from pipeline_integrity.material_exchange import PressureSection, evaluate_pressure_section


CASES = (
    (5.2e6, 1.0e5, True),
    (5.2e6, 1.0e5, False),
    (1.0e5, 2.1e6, True),
    (8.0e6, 0.4e6, True),
)


def _context():
    return RunContext(
        asset_id="synthetic_thick_cylinder",
        scenario_id="lame_reference_cases",
        clock_id="synthetic-static",
        origin="synthetic",
        evidence=(
            EvidenceRef(id="geometry", citation="Independently authored annular geometry", origin="synthetic", license_id="MIT"),
            EvidenceRef(id="pressure", citation="Independently authored absolute pressure states", origin="synthetic", license_id="MIT"),
        ),
    )


@pytest.mark.parametrize("internal_pa,external_pa,closed_ends", CASES)
def test_lame_pressure_shell_matches_traction_and_multiaxial_stress_oracles(internal_pa, external_pa, closed_ends):
    a, b = 0.05, 0.065
    radii = (a, math.sqrt(a * b), b)
    context = _context()
    specification = PressureSection(
        asset_id=context.asset_id,
        location_id="synthetic_shell_wall",
        clock_id=context.clock_id,
        time_s=0.0,
        inner_radius_m=a,
        outer_radius_m=b,
        evaluation_radii_m=radii,
        internal_pressure=Pressure(value=internal_pa, unit="Pa", reference="absolute"),
        external_pressure=Pressure(value=external_pa, unit="Pa", reference="absolute"),
        pressure_loaded_closed_ends=closed_ends,
        geometry_evidence_id="geometry",
        pressure_evidence_id="pressure",
        absolute_stress_tolerance_pa=1e-4,
    )

    record = evaluate_pressure_section(specification, context)

    assert record["status"] == "computed"
    assert record["boundary_traction_residuals"]["inner_radial_traction_pa"] == pytest.approx(0.0, abs=1e-4)
    assert record["boundary_traction_residuals"]["outer_radial_traction_pa"] == pytest.approx(0.0, abs=1e-4)
    A = (internal_pa * a**2 - external_pa * b**2) / (b**2 - a**2)
    B = (internal_pa - external_pa) * a**2 * b**2 / (b**2 - a**2)
    for radius, material_record in zip(radii, record["material_records"], strict=True):
        values = material_record["native"]["payload"]["values"]
        hoop = A + B / radius**2
        radial = A - B / radius**2
        axial = A if closed_ends else 0.0
        expected_vm = math.sqrt(((hoop-radial)**2 + (radial-axial)**2 + (axial-hoop)**2) / 2.0)
        assert values["hoop_stress_Pa"] == pytest.approx(hoop, rel=2e-12, abs=1e-5)
        assert values["radial_stress_Pa"] == pytest.approx(radial, rel=2e-12, abs=1e-5)
        assert values["axial_stress_Pa"] == pytest.approx(axial, rel=2e-12, abs=1e-5)
        assert values["von_mises_Pa"] == pytest.approx(expected_vm, rel=2e-12, abs=1e-5)
