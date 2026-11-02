"""Held-out prediction qualification for one measured turbulent-pipe domain.

This uses a four-point training subset and retains the final two operating
points as untouched predictions. The dataset is MIT licensed; this test uses
the measurements as numerical inputs and does not reproduce source prose.
Qualification is limited to steady turbulent water flow in the source rig.
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
GRAVITY_M_S2 = 9.79
DATASET_SHA256 = "91b1a2d34a89aced66a807507da387104d83b76d6d5948c6d6e03f9f99252a2c"
HOLDOUT_RELATIVE_HEAD_TOLERANCE = 0.007


def _rows() -> tuple[dict[str, float], ...]:
    configured_root = os.environ.get("PIPELINE_PHYSICS_DATA_ROOT")
    root = Path(configured_root) if configured_root else Path(__file__).resolve().parents[1] / "data"
    path = root / "raw" / "pipe_friction_roughness" / "dataset.csv"
    content = path.read_bytes()
    assert hashlib.sha256(content).hexdigest() == DATASET_SHA256
    rows = tuple({key: float(value) for key, value in row.items()}
                 for row in csv.DictReader(content.decode("utf-8").splitlines()))
    assert len(rows) == 6
    return rows


def _darcy_factor(reynolds: float, relative_roughness: float) -> float:
    """Independent Colebrook root using bisection."""
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


def _terms(row: dict[str, float]) -> tuple[float, float, float]:
    area = math.pi * DIAMETER_M**2 / 4.0
    velocity = row["Q"] / area
    reynolds = velocity * DIAMETER_M / row["nu"]
    coefficient = velocity**2 / (2.0 * GRAVITY_M_S2 * DIAMETER_M)
    return reynolds, coefficient, row["Hin"] - row["Hout"]


def _fit_training_rows(
    rows: tuple[dict[str, float], ...], holdout_index: int
) -> tuple[float, float]:
    """Fit parameters after excluding the one operating point under test."""
    train = [_terms(row) for index, row in enumerate(rows) if index != holdout_index]

    def objective(log_roughness: float) -> tuple[float, float]:
        roughness = 10.0**log_roughness
        basis = [_darcy_factor(re, roughness) * coeff for re, coeff, _ in train]
        length = sum(x * y for x, (_, _, y) in zip(basis, train)) / sum(x*x for x in basis)
        residual = sum((length*x-y)**2 for x, (_, _, y) in zip(basis, train))
        return residual, length

    # Deterministic coarse-to-fine search, bounded to a broad engineering range.
    lo, hi = -7.0, -2.0
    for divisions in (5000, 2000, 2000):
        step = (hi - lo) / divisions
        candidates = [lo + i * step for i in range(divisions + 1)]
        best_log = min(candidates, key=lambda value: objective(value)[0])
        lo, hi = best_log - step, best_log + step
    _, length = objective(best_log)
    return 10.0**best_log, length


@pytest.mark.parametrize(
    "holdout_index", range(6), ids=("30Hz", "35Hz", "40Hz", "45Hz", "50Hz", "55Hz")
)
def test_leave_one_out_fit_predicts_held_out_measured_head_loss(holdout_index: int) -> None:
    rows = _rows()
    relative_roughness, equivalent_length = _fit_training_rows(rows, holdout_index)
    row = rows[holdout_index]
    reynolds, _, _ = _terms(row)
    assert reynolds > 4_000.0

    result = liquid_head_loss(
        LiquidHeadLoss(
            diameter_m=DIAMETER_M,
            length_m=equivalent_length,
            roughness_m=relative_roughness * DIAMETER_M,
            volume_flow_m3_s=row["Q"],
            kinematic_viscosity_m2_s=row["nu"],
            gravity_m_s2=GRAVITY_M_S2,
            friction_method="Colebrook",
        )
    )

    measured_head_loss = row["Hin"] - row["Hout"]
    predicted_head_loss = result.values["required_inlet_minus_outlet_head_m"]
    assert result.status == "computed"
    assert abs(predicted_head_loss / measured_head_loss - 1.0) <= HOLDOUT_RELATIVE_HEAD_TOLERANCE
