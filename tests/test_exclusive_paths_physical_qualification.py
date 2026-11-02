"""Independent conservation checks for complete mutually exclusive event paths."""

import pytest
from pydantic import ValidationError

from pipeline_integrity.adapters import MaterialClient
from pipeline_integrity.contracts import EvidenceRef, RunContext


CONTEXT = RunContext(
    asset_id="synthetic_initiator",
    scenario_id="mutually_exclusive_path_partition",
    clock_id="same_exposure_basis",
    origin="synthetic",
    evidence=(EvidenceRef(id="authored", citation="Independently authored partition cases", origin="synthetic", license_id="MIT"),),
)
CLIENT = MaterialClient()


def evaluate(rate, probabilities):
    result = CLIENT.evaluate(
        "reliability.exclusive_paths",
        {"initiating_frequency_per_s": rate, "conditional_probabilities": list(probabilities)},
        CONTEXT,
        location_id="common_event_horizon",
    )
    assert result.status == "computed"
    return result.payload["values"]["path_frequencies_per_s"]


def test_path_frequencies_match_partition_products_and_conserve_initiating_frequency():
    rate, probabilities = 2.4e-5, [0.05, 0.15, 0.3, 0.5]
    frequencies = evaluate(rate, probabilities)
    assert frequencies == pytest.approx([rate * p for p in probabilities], rel=0, abs=0)
    assert sum(frequencies) == pytest.approx(rate, rel=1e-15, abs=0)


def test_common_frequency_scaling_preserves_conditional_path_shares():
    probabilities = [0.1, 0.2, 0.7]
    base = evaluate(3.0e-7, probabilities)
    scaled = evaluate(40.0 * 3.0e-7, probabilities)
    assert scaled == pytest.approx([40.0 * value for value in base], rel=2e-15)
    assert [value / sum(scaled) for value in scaled] == pytest.approx(probabilities)


def test_zero_probability_paths_are_retained_as_zero_frequency_rows():
    frequencies = evaluate(8.0e-6, [0.0, 0.25, 0.75, 0.0])
    assert frequencies == [0.0, 2.0e-6, 6.0e-6, 0.0]
    assert sum(frequencies) == pytest.approx(8.0e-6)


@pytest.mark.parametrize("probabilities", [[0.2, 0.3], [0.2, 0.3, 0.500000001]])
def test_incomplete_or_overfull_path_probability_partition_is_rejected(probabilities):
    with pytest.raises(ValidationError):
        CLIENT.evaluate(
            "reliability.exclusive_paths",
            {"initiating_frequency_per_s": 1.0e-5, "conditional_probabilities": probabilities},
            CONTEXT,
            location_id="common_event_horizon",
        )


@pytest.mark.parametrize("probability", [-0.1, 1.1])
def test_conditional_probability_range_is_enforced(probability):
    with pytest.raises(ValidationError):
        CLIENT.evaluate(
            "reliability.exclusive_paths",
            {"initiating_frequency_per_s": 1.0e-5, "conditional_probabilities": [probability, 1.0 - probability]},
            CONTEXT,
            location_id="common_event_horizon",
        )
