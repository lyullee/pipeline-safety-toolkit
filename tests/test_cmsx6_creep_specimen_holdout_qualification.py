"""Specimen-level held-out life prediction for one CMSX-6 laboratory condition.

This test uses a public CC BY 4.0 workbook and keeps three physical specimens
out of the fit. It checks the native censored-life fit and conditional-life
interface at one temperature and three nominal stresses. It does not support
other heats, orientations, temperatures, loading bases, or pipeline service.
"""

from __future__ import annotations

import hashlib
import math
import os
import runpy
from pathlib import Path

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.creep_exchange import (
    REVIEWS,
    CreepRequest,
    evaluate_creep,
    load_cmsx6_specimens,
)


_DATA_ROOT = Path(os.environ.get("PIPELINE_PHYSICS_DATA_ROOT", Path(__file__).resolve().parents[1] / "data"))
DATA_PATH = _DATA_ROOT / "raw/cmsx6_creep/2024-10_Creep-reference-data-CMSX-6_v1.0.xlsx"
DATA_SHA256 = "ba3b7ba142e9578096502ca083704043bfa28eb2e3f307a6892a390241a88495"
MATERIAL_CONDITION = "BAM-CMSX6-reference-v1.0-single-crystal-heat-treated"
HOLDOUT_IDS = {"Vh5205_C-98", "Vh5205_C-97", "Vh5205_C-80"}
Z_975 = 1.959963984540054


def test_censored_creep_fit_predicts_three_withheld_specimen_lives() -> None:
    raw = DATA_PATH.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == DATA_SHA256
    source_record = load_cmsx6_specimens(DATA_PATH)
    rows = source_record["records"]
    assert len(rows) == 12
    assert sum(bool(row["right_censored"]) for row in rows) == 3
    assert {row["material_condition_id"] for row in rows} == {MATERIAL_CONDITION}
    assert {row["temperature_K"] for row in rows} == {1253.15}

    held_out = [row for row in rows if row["specimen_id"] in HOLDOUT_IDS]
    training = [row for row in rows if row["specimen_id"] not in HOLDOUT_IDS]
    assert len(held_out) == 3 and len(training) == 9
    assert all(not row["right_censored"] for row in held_out)
    assert {row["initial_stress_Pa"] for row in held_out} == {
        170_000_000.0,
        200_000_000.0,
        230_000_000.0,
    }

    context = RunContext(
        asset_id="cmsx6_specimen_holdout",
        scenario_id="single_heat_single_temperature_constant_force",
        clock_id="elapsed_s",
        origin="laboratory",
        evidence=(
            EvidenceRef(
                id="source",
                citation=source_record["citation"],
                origin="laboratory",
                sha256=source_record["source_sha256"],
                license_id="CC-BY-4.0",
            ),
            EvidenceRef(
                id="review",
                citation="Specimen independence and common nominal material condition are caller-reviewed assumptions.",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    parameters = {
        "material_condition_id": MATERIAL_CONDITION,
        "stress_basis": training[0]["stress_basis"],
        "temperature_basis": training[0]["temperature_basis"],
        "family": "stress_only",
        "specimen_id": [row["specimen_id"] for row in training],
        "initial_stress_Pa": [row["initial_stress_Pa"] for row in training],
        "temperature_K": [row["temperature_K"] for row in training],
        "endpoint_time_s": [row["endpoint_time_s"] for row in training],
        "right_censored": [row["right_censored"] for row in training],
        "temperature_tolerance_K": 0.0,
        "independent_specimens_confirmed": True,
        "common_mechanism_and_condition_confirmed": True,
        "evidence": [
            {
                "source_id": "source",
                "origin": "open_dataset",
                "citation": source_record["citation"],
                "license_id": "CC-BY-4.0",
                "sha256": source_record["source_sha256"],
            }
        ],
    }
    fit_request = CreepRequest(
        method="creep.lognormal_life_fit",
        asset_id=context.asset_id,
        location_id="cmsx6_reference_cohort",
        clock_id=context.clock_id,
        material_condition_id=MATERIAL_CONDITION,
        parameters=parameters,
        applicability_reviews=dict.fromkeys(REVIEWS["creep.lognormal_life_fit"], True),
        evidence_ids=("source", "review"),
    )
    fit = evaluate_creep(fit_request, context)
    assert fit["status"] == "computed", fit["issues"]
    assert fit["values"]["model"]["observations"] == 9
    assert fit["values"]["model"]["failures"] == 6
    assert fit["values"]["model"]["activation_energy_J_mol"] is None

    prediction_request = runpy.run_path("examples/creep_methods.py")["prediction_request"]
    for row in held_out:
        request = prediction_request(
            fit,
            context,
            current_age_s=0.0,
            horizon_s=row["endpoint_time_s"],
            stress_pa=row["initial_stress_Pa"],
            temperature_k=row["temperature_K"],
        )
        prediction = evaluate_creep(request, context, fit_record=fit)
        assert prediction["status"] == "computed", prediction["issues"]
        values = prediction["values"]
        assert values["parameter_uncertainty_included"] is False
        standardized_log_error = abs(
            math.log(row["endpoint_time_s"] / values["median_life_s"])
        ) / values["log_life_sigma"]
        # Fixed central 95% interval of the fitted lognormal specimen scatter.
        assert standardized_log_error <= Z_975, (
            row["specimen_id"], standardized_log_error, values
        )
