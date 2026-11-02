"""Finite design study that samples declared flow uncertainty through the slurry owner."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from pipeline_integrity.slurry_uncertainty import slurry_transport_node
from pipeline_integrity.uncertainty import (
    EnsembleMetric,
    EnsembleRequest,
    UncertainVariable,
    Marginal,
    evaluate_ensemble,
    generate_ensemble,
)


def _base_case():
    path = Path(__file__).with_name("slurry_transport.py")
    spec = importlib.util.spec_from_file_location("slurry_example_for_ensemble", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load the adjacent synthetic slurry example")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.make_case(reviewed=True)


def make_study():
    request, context = _base_case()
    evidence_id = context.evidence[0].id
    variable = UncertainVariable(
        id="volume_flow",
        unit="m^3/s",
        layer="input",
        evidence_id=evidence_id,
        marginal=Marginal(family="uniform", parameters={"lower": 0.00008, "upper": 0.00012}),
    )
    design_request = EnsembleRequest(
        id="slurry_flow_sensitivity",
        variables=(variable,),
        method="latin_hypercube",
        interpretation="epistemic",
        dependence="independent",
        evidence_id=evidence_id,
        definition_reviewed=True,
        samples=8,
        seed=20261029,
    )
    design = generate_ensemble(design_request, context)
    metric = EnsembleMetric(
        id="outlet_pressure_pa_abs",
        unit="Pa",
        meaning="quantity",
        evidence_id=evidence_id,
        support_lower=0.0,
    )
    node = slurry_transport_node(
        request,
        id="homogeneous_bingham_pipe",
        variables=(variable,),
        bindings={"volume_flow": "volume_flow_m3_s"},
        metrics=(metric,),
    )
    return design, node


def main():
    design, node = make_study()
    result = evaluate_ensemble(design, (node,))
    print(
        json.dumps(
            {
                "status": result["status"],
                "interpretation": result["interpretation"],
                "summary": result["summaries"][node.id]["outlet_pressure_pa_abs"],
                "operational_failure_probability_inferred": result["observed_failure_rate_inferred"],
                "field_qualified": result["field_qualified"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
