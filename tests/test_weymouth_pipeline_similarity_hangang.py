"""Scale-invariant checks for the SI Weymouth gas-pipe correlation wrapper."""

import pytest

from pipeline_integrity.models.hydraulics import GasCorrelation, gas_correlation


BASE = GasCorrelation(
    method="Weymouth",
    relative_density_to_air=0.693,
    mean_temperature_k=277.15,
    mean_compressibility=0.92,
    length_m=160_000.0,
    diameter_m=0.34,
    inlet_pressure_pa_abs=9.0e6,
    outlet_pressure_pa_abs=2.0e6,
    standard_temperature_k=288.7,
    standard_pressure_pa_abs=101_325.0,
    empirical_efficiency=0.92,
)


@pytest.mark.parametrize(
    "update,expected_ratio",
    [
        ({"length_m": BASE.length_m * 4.0}, 0.5),
        ({"diameter_m": BASE.diameter_m * 1.5}, 1.5**2.667),
        (
            {
                "inlet_pressure_pa_abs": BASE.inlet_pressure_pa_abs * 1.25,
                "outlet_pressure_pa_abs": BASE.outlet_pressure_pa_abs * 1.25,
            },
            1.25,
        ),
        ({"empirical_efficiency": BASE.empirical_efficiency * 0.75}, 0.75),
        ({"standard_temperature_k": BASE.standard_temperature_k * 1.1}, 1.1),
        ({"standard_pressure_pa_abs": BASE.standard_pressure_pa_abs * 1.1}, 1.0 / 1.1),
    ],
)
def test_weymouth_flow_obeys_independent_similarity_scalings(update, expected_ratio):
    baseline = gas_correlation(BASE)
    scaled = gas_correlation(BASE.model_copy(update=update))
    q_base = baseline.values["standard_volume_flow_m3_s"]
    q_scaled = scaled.values["standard_volume_flow_m3_s"]
    assert baseline.status == scaled.status == "computed"
    assert q_base > 0 and q_scaled > 0
    assert q_scaled / q_base == pytest.approx(expected_ratio, rel=2e-12)


def test_weymouth_pressure_squared_integral_scales_with_standard_basis_and_length():
    base = gas_correlation(BASE).values["standard_volume_flow_m3_s"]
    altered = gas_correlation(
        BASE.model_copy(
            update={
                "length_m": BASE.length_m * 2.0,
                "standard_pressure_pa_abs": BASE.standard_pressure_pa_abs * 2.0,
            }
        )
    ).values["standard_volume_flow_m3_s"]
    # The source equation gives Q² L / ((P1²-P2²) (Ts/Ps)²) as invariant.
    left = base**2 * BASE.length_m / (
        (BASE.inlet_pressure_pa_abs**2 - BASE.outlet_pressure_pa_abs**2)
        * (BASE.standard_temperature_k / BASE.standard_pressure_pa_abs) ** 2
    )
    right = altered**2 * (2.0 * BASE.length_m) / (
        (BASE.inlet_pressure_pa_abs**2 - BASE.outlet_pressure_pa_abs**2)
        * (BASE.standard_temperature_k / (2.0 * BASE.standard_pressure_pa_abs)) ** 2
    )
    assert right == pytest.approx(left, rel=3e-12)
