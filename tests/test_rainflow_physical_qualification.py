"""Bounded scalar rainflow decomposition invariants through the native material API."""

import pytest
from pydantic import ValidationError

from pipeline_integrity.adapters import MaterialClient
from pipeline_integrity.contracts import EvidenceRef, RunContext


CONTEXT = RunContext(
    asset_id="rainflow_reference_specimen",
    scenario_id="scalar_cycle_count_reference",
    clock_id="ordered_sample_index",
    origin="synthetic",
    evidence=(
        EvidenceRef(
            id="authored",
            citation="Independently authored turning-point reference history",
            origin="synthetic",
            license_id="MIT",
        ),
    ),
)
CLIENT = MaterialClient()


def evaluate(values, unit="MPa"):
    result = CLIENT.evaluate(
        "fatigue.rainflow",
        {"values": list(values), "quantity_unit": unit},
        CONTEXT,
        location_id="uniaxial_channel",
    )
    assert result.status == "computed"
    return result.payload["values"]


def signature(values):
    result = evaluate(values)
    return sorted((round(row["range"], 10), round(row["mean"], 10), row["count"]) for row in result["cycles"])


def test_nested_reference_history_closes_inner_cycle_and_residual_halves():
    result = evaluate([0.0, 3.0, 1.0, 2.0, 0.0])
    assert sorted((row["range"], row["count"]) for row in result["cycles"]) == [(1.0, 1.0), (3.0, 0.5), (3.0, 0.5)]
    assert result["cycle_count"] == pytest.approx(sum(row["count"] for row in result["cycles"]))
    assert result["cycle_count"] == pytest.approx(2.0)


def test_positive_affine_stress_transform_preserves_count_and_indices():
    source = [0.0, 3.0, 1.0, 2.0, 0.0]
    base = evaluate(source)
    transformed = evaluate([2.5 * value + 17.0 for value in source])
    assert len(transformed["cycles"]) == len(base["cycles"])
    assert transformed["cycle_count"] == pytest.approx(base["cycle_count"])
    for left, right in zip(base["cycles"], transformed["cycles"], strict=True):
        assert right["range"] == pytest.approx(2.5 * left["range"])
        assert right["mean"] == pytest.approx(2.5 * left["mean"] + 17.0)
        assert right["count"] == left["count"]
        assert (right["start_index"], right["end_index"]) == (left["start_index"], left["end_index"])


def test_reversing_sample_order_preserves_range_count_multiset():
    values = [0.0, 3.0, 1.0, 2.0, 0.0]
    forward = evaluate(values)
    reverse = evaluate(list(reversed(values)))
    assert sorted((row["range"], row["count"]) for row in reverse["cycles"]) == sorted(
        (row["range"], row["count"]) for row in forward["cycles"]
    )
    assert reverse["cycle_count"] == pytest.approx(forward["cycle_count"])


def test_plateaus_are_zero_range_free_and_residual_excursions_are_half_cycles():
    result = evaluate([0.0, 0.0, 2.0, 2.0, 0.0, 0.0])
    assert all(row["range"] > 0 for row in result["cycles"])
    assert result["cycle_count"] == pytest.approx(1.0)
    assert sorted(row["count"] for row in result["cycles"]) == [0.5, 0.5]


def test_unit_is_preserved_and_every_cycle_reconstructs_its_sample_range_and_mean():
    result = evaluate([2.0, -1.0, 4.0, 0.0], unit="MPa")
    assert result["quantity_unit"] == "MPa"
    for row in result["cycles"]:
        # Cycle endpoints are source sample indices and its range/mean must be
        # computed from those original values, independent of downstream units.
        i, j = row["start_index"], row["end_index"]
        samples = [2.0, -1.0, 4.0, 0.0]
        expected_range = abs(samples[j] - samples[i])
        expected_mean = 0.5 * (samples[j] + samples[i])
        assert row["range"] == pytest.approx(expected_range)
        assert row["mean"] == pytest.approx(expected_mean)


def test_history_requires_at_least_three_samples():
    with pytest.raises(ValidationError):
        CLIENT.evaluate(
            "fatigue.rainflow",
            {"values": [0.0, 1.0], "quantity_unit": "MPa"},
            CONTEXT,
            location_id="uniaxial_channel",
        )
