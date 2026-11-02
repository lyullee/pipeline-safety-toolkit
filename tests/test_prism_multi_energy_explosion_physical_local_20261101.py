"""Analytic bounded-domain oracle for PRISM's integrated multi-energy blast radii."""

from __future__ import annotations

import math

import pytest

from ignition import multi_energy_radii


P_ATM_PA = 101_325.0


@pytest.mark.parametrize(
    ("combustion_energy_j", "obstructed_fraction"),
    [
        (1.0e5, 0.02),
        (1.0e6, 0.05),
        (1.0e7, 0.12),
        (1.0e8, 0.30),
        (1.0e9, 0.50),
    ],
)
def test_integrated_multi_energy_radii_match_independent_cube_root_reference(
    combustion_energy_j, obstructed_fraction
):
    r03, r01 = multi_energy_radii(combustion_energy_j, obstructed_fraction)
    scaled_length = (obstructed_fraction * combustion_energy_j / P_ATM_PA) ** (1.0 / 3.0)
    assert r03 == pytest.approx(1.5 * scaled_length, rel=2e-14)
    assert r01 == pytest.approx(3.0 * scaled_length, rel=2e-14)
    assert r01 / r03 == pytest.approx(2.0, rel=2e-14)


def test_blast_zone_radii_follow_one_third_power_energy_and_obstruction_scaling():
    base = multi_energy_radii(2.0e7, 0.08)
    eightfold_energy = multi_energy_radii(16.0e7, 0.08)
    eightfold_obstruction = multi_energy_radii(2.0e7, 0.64)
    for initial, energy_scaled, obstruction_scaled in zip(
        base, eightfold_energy, eightfold_obstruction, strict=True
    ):
        assert energy_scaled == pytest.approx(2.0 * initial, rel=2e-14)
        assert obstruction_scaled == pytest.approx(2.0 * initial, rel=2e-14)


def test_zero_available_combustion_energy_produces_zero_blast_contours():
    assert multi_energy_radii(0.0, 0.2) == (0.0, 0.0)


def test_integrated_chain_call_site_is_pinned_to_the_qualified_kernel():
    from pathlib import Path

    owner_root = Path(__file__).resolve().parents[2] / "tmp" / "analysis" / "repos" / "prism" / "lh2-tno"
    worker = (owner_root / "src" / "lh2_tno" / "chain_worker.py").read_text(encoding="utf-8")
    assert "ignmod.multi_energy_radii(fm*fire.H2_HEAT_OF_COMBUSTION,ignition['obstructed_energy_fraction'])" in worker
    assert "multi_energy_blast_zones" in worker
