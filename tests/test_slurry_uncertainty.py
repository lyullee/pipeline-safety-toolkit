"""Synthetic integration cases; numerical execution is deferred to Nurion."""

import importlib.util
from pathlib import Path

import pytest

from pipeline_integrity.slurry_uncertainty import slurry_transport_node
from pipeline_integrity.uncertainty import EnsembleMetric, UncertainVariable

_spec = importlib.util.spec_from_file_location(
    "slurry_uncertainty_example", Path(__file__).parents[1] / "examples" / "slurry_transport.py"
)
_example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_example)


def _definitions(evidence_id):
    variable = UncertainVariable(id="flow", unit="m^3/s", layer="input", evidence_id=evidence_id)
    metric = EnsembleMetric(
        id="outlet_pressure_pa_abs", unit="Pa", meaning="quantity", evidence_id=evidence_id,
        support_lower=0.0,
    )
    return variable, metric


def test_slurry_node_calls_existing_evaluator_for_a_declared_draw():
    request, base_context = _example.make_case(reviewed=True)
    variable, metric = _definitions(base_context.evidence[0].id)
    node = slurry_transport_node(
        request, id="slurry", variables=(variable,), bindings={"flow": "volume_flow_m3_s"},
        metrics=(metric,),
    )
    context = base_context.model_copy(
        deep=True, update={"metadata": base_context.metadata | {"ensemble": {"draw_id": "case_1"}}}
    )
    outcome = node.evaluate({"flow": request.volume_flow_m3_s}, context)
    assert outcome.context == context
    assert outcome.statuses[metric.id] == "computed"
    assert outcome.native_payload["slurry_record"]["physical_validation_claim"] is False


def test_slurry_node_rejects_implicit_units_and_dimensional_rheology_coefficient():
    request, context = _example.make_case()
    variable, metric = _definitions(context.evidence[0].id)
    with pytest.raises(ValueError, match="unit mismatch"):
        slurry_transport_node(
            request, id="slurry", variables=(variable,), bindings={"flow": "length_m"},
            metrics=(metric,),
        )
    consistency = UncertainVariable(
        id="consistency", unit="Pa*s", layer="model", evidence_id=context.evidence[0].id
    )
    with pytest.raises(ValueError, match="allowlisted"):
        slurry_transport_node(
            request, id="slurry", variables=(consistency,),
            bindings={"consistency": "properties.consistency_Pa_s_n"}, metrics=(metric,),
        )


def test_slurry_node_retains_unresolved_draw_as_needs_data():
    request, context = _example.make_case()
    variable, metric = _definitions(context.evidence[0].id)
    node = slurry_transport_node(
        request, id="slurry", variables=(variable,), bindings={"flow": "volume_flow_m3_s"},
        metrics=(metric,),
    )
    draw_context = context.model_copy(
        deep=True, update={"metadata": context.metadata | {"ensemble": {"draw_id": "case_missing"}}}
    )
    outcome = node.evaluate({"flow": None}, draw_context)
    assert outcome.statuses[metric.id] == "needs_data"
    assert outcome.values[metric.id] is None
    assert outcome.native_payload["unresolved_variable_ids"] == ["flow"]
