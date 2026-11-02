"""Measured-run holdout for turbulent water head loss in one large PVC rig.

The four run summaries are derived from the public HYDRALAB III data record.
Two steady runs calibrate a single roughness parameter; two separate steady
runs are held out for evaluation. The acceptance envelope is fixed at 7% from
the source's reported repeatability scale before this HPC execution. This is a
bounded laboratory-domain claim and does not imply field or design-code use.
"""

from __future__ import annotations

import csv
import hashlib
import math
from pathlib import Path

import CoolProp.CoolProp as CP
import pytest

from pipeline_integrity.models.hydraulics import LiquidHeadLoss, liquid_head_loss


DATA_SHA256 = "e779b3f3672fd277a76c56776a1c1e9c242f9ef65407c50d5bd432ee589c3cdc"
DATA_PATH = Path(__file__).resolve().parents[1] / "data/raw/hydralab_large_pipeline/steady_runs_240_330s.csv"
DIAMETER_M = 0.2354  # 250 mm reported diameter minus two reported 7.3 mm walls.
GRAVITY_M_S2 = 9.80665
MAX_RELATIVE_HEAD_ERROR = 0.07
SEGMENTS = (("p5", "p7", 72.0), ("p7", "p8", 23.1), ("p8", "p9", 46.0))
TRAIN_RUNS = (4020, 4025)
HOLDOUT_RUNS = (4031, 4036)


def _rows() -> dict[int, dict[str, float]]:
    content = DATA_PATH.read_bytes()
    assert hashlib.sha256(content).hexdigest() == DATA_SHA256
    parsed = {
        int(row["run"]): {key: float(value) for key, value in row.items()}
        for row in csv.DictReader(content.decode("utf-8").splitlines())
    }
    assert set(parsed) == {*TRAIN_RUNS, *HOLDOUT_RUNS}
    assert all(row["samples"] == 9000 for row in parsed.values())
    return parsed


def _state(row: dict[str, float]) -> tuple[float, float, float, float]:
    temperature_k = row["t2_at_x46_4_c"] + 273.15
    density = CP.PropsSI("D", "T", temperature_k, "P", 101325.0, "Water")
    viscosity = CP.PropsSI("V", "T", temperature_k, "P", 101325.0, "Water")
    flow_m3_s = (row["q_in_l_s"] + row["q_out_l_s"]) / 2000.0
    return density, viscosity / density, flow_m3_s, temperature_k


def _colebrook(reynolds: float, relative_roughness: float) -> float:
    def residual(factor: float) -> float:
        return 1.0 / math.sqrt(factor) + 2.0 * math.log10(
            relative_roughness / 3.7 + 2.51 / (reynolds * math.sqrt(factor))
        )

    low, high = 0.005, 0.15
    assert residual(low) > 0.0 > residual(high)
    for _ in range(80):
        mid = (low + high) / 2.0
        if residual(mid) > 0.0:
            low = mid
        else:
            high = mid
    return (low + high) / 2.0


def _fit_relative_roughness(rows: dict[int, dict[str, float]]) -> float:
    observations: list[tuple[float, float, float, float]] = []
    for run in TRAIN_RUNS:
        row = rows[run]
        density, nu, flow, _ = _state(row)
        pressures = {name: row[f"{name}_bar_g"] * 100000.0 for name in ("p5", "p7", "p8", "p9")}
        for upstream, downstream, length in SEGMENTS:
            observed = (pressures[upstream] - pressures[downstream]) / (density * GRAVITY_M_S2)
            assert observed > 0.0
            observations.append((flow, nu, length, observed))

    def objective(log10_rr: float) -> float:
        rr = 10.0**log10_rr
        errors = []
        for flow, nu, length, observed in observations:
            area = math.pi * DIAMETER_M**2 / 4.0
            velocity = flow / area
            re = velocity * DIAMETER_M / nu
            factor = _colebrook(re, rr)
            predicted = factor * length / DIAMETER_M * velocity**2 / (2.0 * GRAVITY_M_S2)
            errors.append((predicted / observed - 1.0) ** 2)
        return sum(errors) / len(errors)

    low, high = -6.0, -2.0
    ratio = (math.sqrt(5.0) - 1.0) / 2.0
    x1, x2 = high - ratio * (high - low), low + ratio * (high - low)
    y1, y2 = objective(x1), objective(x2)
    for _ in range(100):
        if y1 <= y2:
            high, x2, y2 = x2, x1, y1
            x1 = high - ratio * (high - low)
            y1 = objective(x1)
        else:
            low, x1, y1 = x1, x2, y2
            x2 = low + ratio * (high - low)
            y2 = objective(x2)
    return 10.0 ** ((low + high) / 2.0)


@pytest.mark.parametrize("run", HOLDOUT_RUNS, ids=("run-4031", "run-4036"))
def test_held_out_run_head_loss_prediction_within_published_repeatability(run: int) -> None:
    rows = _rows()
    relative_roughness = _fit_relative_roughness(rows)
    row = rows[run]
    density, nu, flow, _ = _state(row)
    pressures = {name: row[f"{name}_bar_g"] * 100000.0 for name in ("p5", "p7", "p8", "p9")}

    for upstream, downstream, length in SEGMENTS:
        measured_head = (pressures[upstream] - pressures[downstream]) / (density * GRAVITY_M_S2)
        result = liquid_head_loss(
            LiquidHeadLoss(
                diameter_m=DIAMETER_M,
                length_m=length,
                roughness_m=relative_roughness * DIAMETER_M,
                volume_flow_m3_s=flow,
                kinematic_viscosity_m2_s=nu,
                gravity_m_s2=GRAVITY_M_S2,
                friction_method="Colebrook",
            )
        )
        assert result.status == "computed"
        predicted_head = result.values["required_inlet_minus_outlet_head_m"]
        assert abs(predicted_head / measured_head - 1.0) <= MAX_RELATIVE_HEAD_ERROR
