"""Exact Newton-cooling and Biot-domain checks for the owner lumped model."""

import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.material_exchange import evaluate_material_job, prepare_material_job

CTX = RunContext(
    asset_id="lumped-thermal-reference",
    scenario_id="single-node-cooling",
    clock_id="elapsed-s",
    origin="synthetic",
    evidence=(EvidenceRef(id="thermal", citation="Authored lumped-capacitance inputs", origin="synthetic", license_id="MIT"),),
)
CASES = (
    (500.0, 300.0, 10000.0, 2.0, 10.0, 0.02, 10.0, 0.1, 100.0),
    (280.0, 420.0, 8000.0, 1.2, 20.0, 0.015, 5.0, 0.2, 60.0),
    (350.0, 350.0, 5000.0, 0.5, 8.0, 0.04, 4.0, 0.1, 0.0),
)


def test_lumped_temperature_matches_exact_newton_cooling_and_biot_gate():
    for initial, ambient, capacity, area, h, length, conductivity, max_biot, duration in CASES:
        job = prepare_material_job(
            "thermal.lumped_temperature",
            {
                "initial_temperature_K": initial,
                "ambient_temperature_K": ambient,
                "heat_capacity_J_K": capacity,
                "surface_area_m2": area,
                "convection_W_m2_K": h,
                "solid_conductivity_W_m_K": conductivity,
                "characteristic_length_m": length,
                "maximum_declared_biot": max_biot,
                "exposure_s": duration,
            },
            CTX,
            location_id="uniform-solid-node",
            evidence_ids=("thermal",),
        )
        receipt = evaluate_material_job(job, CTX)
        values = receipt.native.payload["values"]
        biot = h * length / conductivity
        tau = capacity / (h * area)
        expected = ambient + (initial - ambient) * math.exp(-duration / tau)
        assert receipt.native.status == "computed"
        assert values["biot_number"] == pytest.approx(biot, rel=1e-14)
        assert values["time_constant_s"] == pytest.approx(tau, rel=1e-14)
        assert values["temperature_K"] == pytest.approx(expected, rel=2e-14, abs=1e-13)


def test_lumped_temperature_withholds_state_when_declared_biot_limit_is_exceeded():
    parameters = {
        "initial_temperature_K": 500.0,
        "ambient_temperature_K": 300.0,
        "heat_capacity_J_K": 10000.0,
        "surface_area_m2": 2.0,
        "convection_W_m2_K": 100.0,
        "solid_conductivity_W_m_K": 0.1,
        "characteristic_length_m": 0.01,
        "maximum_declared_biot": 0.1,
        "exposure_s": 50.0,
    }
    job = prepare_material_job(
        "thermal.lumped_temperature", parameters, CTX,
        location_id="uniform-solid-node", evidence_ids=("thermal",),
    )
    receipt = evaluate_material_job(job, CTX)
    assert receipt.native.status == "out_of_scope"

