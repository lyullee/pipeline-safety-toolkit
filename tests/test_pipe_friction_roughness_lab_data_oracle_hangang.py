"""Reproduce a published steady-pipe friction calibration on open lab data.

The measurements are from Santos-Ruiz et al., “Friction and Roughness:
Dataset and Software” (Zenodo record 20025373, MIT-licensed data). The test
uses the publisher-reported relative roughness and equivalent length as
calibration inputs; it is not an independent holdout or field validation.
"""

from __future__ import annotations

import csv
import hashlib
import math
import os
from pathlib import Path

import pytest

from pipeline_integrity.models.hydraulics import LiquidHeadLoss, liquid_head_loss


DIAMETER_M = 0.0486
EQUIVALENT_LENGTH_M = 112.2238
RELATIVE_ROUGHNESS = 3.4652e-4
GRAVITY_M_S2 = 9.79
DATASET_SHA256 = "91b1a2d34a89aced66a807507da387104d83b76d6d5948c6d6e03f9f99252a2c"
MAX_RELATIVE_HEAD_ERROR = 0.003


def _dataset_path() -> Path:
    configured_root = os.environ.get("PIPELINE_PHYSICS_DATA_ROOT")
    root = Path(configured_root) if configured_root else Path(__file__).resolve().parents[1] / "data"
    return root / "raw" / "pipe_friction_roughness" / "dataset.csv"


def _read_rows() -> tuple[dict[str, float], ...]:
    path = _dataset_path()
    content = path.read_bytes()
    assert hashlib.sha256(content).hexdigest() == DATASET_SHA256
    rows = tuple(
        {key: float(value) for key, value in row.items()}
        for row in csv.DictReader(content.decode("utf-8").splitlines())
    )
    assert len(rows) == 6
    return rows


def _colebrook_darcy_factor(reynolds: float, relative_roughness: float) -> float:
    """Solve the Darcy Colebrook equation independently by bisection."""

    def residual(factor: float) -> float:
        return 1.0 / math.sqrt(factor) + 2.0 * math.log10(
            relative_roughness / 3.7 + 2.51 / (reynolds * math.sqrt(factor))
        )

    low, high = 0.005, 0.15
    assert residual(low) > 0.0 > residual(high)
    for _ in range(90):
        middle = (low + high) / 2.0
        if residual(middle) > 0.0:
            low = middle
        else:
            high = middle
    return (low + high) / 2.0


@pytest.mark.parametrize("row", _read_rows(), ids=lambda row: f"Q-{row['Q']:.7f}")
def test_lab_pipe_head_loss_matches_independent_colebrook_and_measured_heads(row):
    area = math.pi * DIAMETER_M**2 / 4.0
    velocity = row["Q"] / area
    reynolds = abs(velocity) * DIAMETER_M / row["nu"]
    assert reynolds > 4_000.0
    expected_factor = _colebrook_darcy_factor(reynolds, RELATIVE_ROUGHNESS)
    expected_head_loss = (
        expected_factor
        * EQUIVALENT_LENGTH_M
        / DIAMETER_M
        * velocity**2
        / (2.0 * GRAVITY_M_S2)
    )
    measured_head_loss = row["Hin"] - row["Hout"]

    result = liquid_head_loss(
        LiquidHeadLoss(
            diameter_m=DIAMETER_M,
            length_m=EQUIVALENT_LENGTH_M,
            roughness_m=RELATIVE_ROUGHNESS * DIAMETER_M,
            volume_flow_m3_s=row["Q"],
            kinematic_viscosity_m2_s=row["nu"],
            gravity_m_s2=GRAVITY_M_S2,
            friction_method="Colebrook",
        )
    )

    assert result.status == "computed"
    assert result.values["reynolds"] == pytest.approx(reynolds, rel=2e-14)
    assert result.values["darcy_friction_factor"] == pytest.approx(expected_factor, rel=2e-11)
    assert result.values["required_inlet_minus_outlet_head_m"] == pytest.approx(
        expected_head_loss, rel=2e-11
    )
    # This tolerance allows for the measurement table's four-decimal head rounding.
    assert abs(expected_head_loss / measured_head_loss - 1.0) <= MAX_RELATIVE_HEAD_ERROR

