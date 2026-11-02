"""Synthetic pressure-drop scaling check for the fixed-coefficient gas-meter closure.

This exercises an authored numerical case, not a calibrated meter or facility measurement.
"""
import math
import runpy
from pathlib import Path

import pytest

from pipeline_integrity.gas_meter import boundary_metadata, evaluate_gas_meter


MAKE_CASE = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/gas_meter.py"))["make_case"]


def _rebind(request, context):
    context = context.model_copy(deep=True)
    context.metadata["gas_meter_boundary"] = boundary_metadata(request)
    return context


def test_fixed_coefficient_mass_flow_obeys_sqrt_pressure_drop_at_fixed_upstream_state():
    base, context = MAKE_CASE(reviewed=True, mode="specified_pair")
    requests = [base.model_copy(update={"downstream_pressure_pa_abs": p}) for p in (495000.0, 480000.0)]
    records = [evaluate_gas_meter(request, _rebind(request, context)) for request in requests]
    assert all(record["status"] == "computed" for record in records)

    density = records[0]["values"]["upstream_density_kg_m3"]
    assert records[1]["values"]["upstream_density_kg_m3"] == pytest.approx(density, rel=1e-13)
    geometry = base.geometry
    area = math.pi * geometry.secondary_dimension_m**2 / 4.0
    beta = geometry.secondary_dimension_m / geometry.inner_pipe_diameter_m
    coefficient = base.specified_discharge_coefficient * base.specified_expansibility_factor
    expected = [coefficient * area * math.sqrt(2.0 * density * (base.upstream_pressure_pa_abs - p)
                / (1.0 - beta**4)) for p in (495000.0, 480000.0)]
    actual = [record["values"]["signed_mass_flow_kg_s"] for record in records]
    assert actual == pytest.approx(expected, rel=2e-12)
    assert actual[1] / actual[0] == pytest.approx(math.sqrt(4.0), rel=2e-12)
    for record in records:
        values = record["values"]
        assert values["signed_actual_volume_flow_m3_s"] * values["upstream_density_kg_m3"] == pytest.approx(
            values["signed_mass_flow_kg_s"], rel=2e-13)
        assert values["signed_reference_volume_flow_m3_s"] * values["reference_density_kg_m3"] == pytest.approx(
            values["signed_mass_flow_kg_s"], rel=2e-13)
        assert record["qualification"].startswith("research_screening;")

