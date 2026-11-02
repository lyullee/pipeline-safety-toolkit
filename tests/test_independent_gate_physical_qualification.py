"""Bounded probability identities for explicitly independent Boolean gates."""

import math

import pytest
from pydantic import ValidationError

from pipeline_integrity.adapters import MaterialClient
from pipeline_integrity.contracts import EvidenceRef, RunContext


CONTEXT = RunContext(
    asset_id="synthetic_gate_inputs",
    scenario_id="independent_boolean_gate_oracle",
    clock_id="shared_declared_horizon",
    origin="synthetic",
    evidence=(EvidenceRef(id="authored", citation="Independently authored probability identities", origin="synthetic", license_id="MIT"),),
)
CLIENT = MaterialClient()


def evaluate(probabilities, gate, independence=True):
    return CLIENT.evaluate(
        "reliability.independent_gate",
        {"probabilities": list(probabilities), "gate": gate, "independence_declared": independence},
        CONTEXT,
        location_id="same_event_horizon",
    )


def test_and_gate_matches_independent_intersection_product():
    result = evaluate([0.2, 0.5, 0.9], "AND")
    assert result.status == "computed"
    assert result.payload["values"]["probability"] == pytest.approx(0.09, rel=2e-16, abs=0)


def test_or_gate_matches_complement_of_joint_nonoccurrence():
    result = evaluate([0.2, 0.5, 0.9], "OR")
    assert result.status == "computed"
    assert result.payload["values"]["probability"] == pytest.approx(0.96, abs=2e-16)


@pytest.mark.parametrize("gate", ["AND", "OR"])
def test_single_input_gate_is_identity(gate):
    result = evaluate([0.37], gate)
    assert result.status == "computed"
    assert result.payload["values"]["probability"] == pytest.approx(0.37, abs=0)


def test_zero_and_certain_event_boundaries_are_exact():
    assert evaluate([0.0, 0.4], "AND").payload["values"]["probability"] == 0.0
    assert evaluate([0.0, 0.4], "OR").payload["values"]["probability"] == pytest.approx(0.4, abs=0)
    assert evaluate([1.0, 0.4], "OR").payload["values"]["probability"] == 1.0
    assert evaluate([1.0, 0.4], "AND").payload["values"]["probability"] == pytest.approx(0.4, abs=0)


def test_or_uses_stable_complement_for_tiny_event_probabilities():
    probabilities = [1e-14, 2e-14, 3e-14]
    expected = -math.expm1(sum(math.log1p(-p) for p in probabilities))
    result = evaluate(probabilities, "OR")
    assert result.payload["values"]["probability"] == pytest.approx(expected, rel=2e-15, abs=0)


def test_undeclared_independence_withholds_joint_probability():
    result = evaluate([0.2, 0.7], "AND", independence=False)
    assert result.status == "needs_data"
    assert result.payload["values"] == {}


@pytest.mark.parametrize("probability", [-0.01, 1.01])
def test_probability_outside_unit_interval_is_rejected(probability):
    with pytest.raises(ValidationError):
        evaluate([probability, 1.0 - probability], "OR")


def test_empty_gate_is_rejected():
    with pytest.raises(ValidationError):
        evaluate([], "AND")
