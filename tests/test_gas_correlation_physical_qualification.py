"""Catalog-path similarity identities for the declared SI Weymouth gas correlation."""

import pytest

from pipeline_integrity import evaluate
from pipeline_integrity.contracts import RunContext

CTX = RunContext(asset_id="weymouth-reference", scenario_id="synthetic-single-pipe", clock_id="steady", origin="synthetic")
BASE = {
    "method": "Weymouth",
    "relative_density_to_air": 0.693,
    "mean_temperature_k": 277.15,
    "mean_compressibility": 0.92,
    "length_m": 160000.0,
    "diameter_m": 0.34,
    "inlet_pressure_pa_abs": 9.0e6,
    "outlet_pressure_pa_abs": 2.0e6,
    "standard_temperature_k": 288.7,
    "standard_pressure_pa_abs": 101325.0,
    "empirical_efficiency": 0.92,
}


@pytest.mark.parametrize(
    "updates,expected_ratio",
    [
        ({"length_m": 640000.0}, 0.5),
        ({"diameter_m": 0.51}, 1.5**2.667),
        ({"inlet_pressure_pa_abs": 11.25e6, "outlet_pressure_pa_abs": 2.5e6}, 1.25),
        ({"empirical_efficiency": 0.69}, 0.75),
        ({"standard_temperature_k": 317.57}, 1.1),
        ({"standard_pressure_pa_abs": 111457.5}, 1.0 / 1.1),
    ],
)
def test_catalog_weymouth_flow_obeys_independent_similarity_scalings(updates, expected_ratio):
    base = evaluate("hydraulics.gas_correlation", BASE, CTX)
    scaled = evaluate("hydraulics.gas_correlation", BASE | updates, CTX)
    q0 = base.values["standard_volume_flow_m3_s"]
    q1 = scaled.values["standard_volume_flow_m3_s"]
    assert base.status == scaled.status == "computed"
    assert q0 > 0 and q1 > 0
    assert q1 / q0 == pytest.approx(expected_ratio, rel=2e-12)


def test_catalog_weymouth_pressure_squared_integral_invariant():
    altered = BASE | {"length_m": 320000.0, "standard_pressure_pa_abs": 202650.0}
    q0 = evaluate("hydraulics.gas_correlation", BASE, CTX).values["standard_volume_flow_m3_s"]
    q1 = evaluate("hydraulics.gas_correlation", altered, CTX).values["standard_volume_flow_m3_s"]
    def invariant(q, length, ps):
        return q**2 * length / (
            (BASE["inlet_pressure_pa_abs"]**2 - BASE["outlet_pressure_pa_abs"]**2)
            * (BASE["standard_temperature_k"] / ps) ** 2
        )
    assert invariant(q1, altered["length_m"], altered["standard_pressure_pa_abs"]) == pytest.approx(
        invariant(q0, BASE["length_m"], BASE["standard_pressure_pa_abs"]), rel=3e-12
    )
