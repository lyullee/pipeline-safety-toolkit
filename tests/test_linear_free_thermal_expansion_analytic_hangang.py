"""Independent length and engineering-strain checks for free linear expansion."""

import pytest

from pipeline_integrity.models.thermal import Expansion, expansion


CASES = (
    (8.0, 12e-6, 300.0, 800.0),
    (1_000.0, 17.3e-6, 500.0, 290.0),
    (25.0, 0.0, 273.15, 373.15),
    (2.5, 8e-6, 400.0, 400.0),
    (100.0, 0.02, 300.0, 300.5),
)


@pytest.mark.parametrize("length,alpha,t_ref,t_op", CASES)
def test_free_linear_expansion_matches_signed_strain_and_length_oracle(length, alpha, t_ref, t_op):
    result = expansion(
        Expansion(
            reference_length_m=length,
            linear_expansion_per_k=alpha,
            reference_temperature_k=t_ref,
            operating_temperature_k=t_op,
        )
    )
    strain = alpha * (t_op - t_ref)
    expected_change = length * strain
    assert result.status == "computed"
    assert result.values["free_length_change_m"] == pytest.approx(expected_change, abs=1e-12)
    assert result.values["operating_length_m"] == pytest.approx(length * (1.0 + strain), rel=2e-15)
    assert result.values["operating_length_m"] - length == pytest.approx(
        result.values["free_length_change_m"], abs=1e-12
    )


def test_contraction_beyond_positive_length_domain_is_withheld():
    result = expansion(
        Expansion(
            reference_length_m=1.0,
            linear_expansion_per_k=0.1,
            reference_temperature_k=300.0,
            operating_temperature_k=280.0,
        )
    )
    assert result.status == "out_of_scope"
    assert result.values == {}
