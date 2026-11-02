"""Catalog-level qualification of elastic-pipe acoustic wave speed."""

import math

import pytest

from pipeline_integrity import evaluate
from pipeline_integrity.contracts import RunContext


CASES = (
    (998.0, 2.2e9, 2.0e11, 0.30, 0.010, 0.0),
    (998.0, 2.2e9, 2.0e11, 0.30, 0.010, 1.0),
    (1025.0, 2.0e9, 1.8e11, 0.60, 0.018, 0.85),
    (850.0, 1.4e9, 7.0e10, 0.12, 0.004, 2.0),
    (1200.0, 3.0e9, 2.1e11, 1.00, 0.030, 0.35),
)


def _context():
    return RunContext(asset_id="celerity-reference", scenario_id="elastic-pipe-wave-speed", clock_id="steady-state", origin="synthetic")


@pytest.mark.parametrize("rho,k_bulk,e_pipe,diameter,wall,restraint", CASES)
def test_catalog_wave_speed_matches_compliance_solution(rho,k_bulk,e_pipe,diameter,wall,restraint):
    result = evaluate(
        "transient.wave_speed",
        {
            "density_kg_m3": rho,
            "fluid_bulk_modulus_pa": k_bulk,
            "pipe_elastic_modulus_pa": e_pipe,
            "inner_diameter_m": diameter,
            "wall_thickness_m": wall,
            "restraint_coefficient": restraint,
        },
        _context(),
    )
    compliance = 1.0 / k_bulk + restraint * diameter / (e_pipe * wall)
    expected_speed = math.sqrt(1.0 / (rho * compliance))
    rigid_speed = math.sqrt(k_bulk / rho)
    assert result.status == "computed"
    assert result.model_id == "transient.wave_speed"
    assert result.values["wave_speed_m_s"] == pytest.approx(expected_speed, rel=2e-14)
    assert result.values["rigid_wall_sound_speed_m_s"] == pytest.approx(rigid_speed, rel=2e-14)
    assert result.values["wave_speed_m_s"] <= rigid_speed
