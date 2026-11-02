"""Analytical joint moments, retained missing weight, dependence and boundary integrity."""

import copy
from dataclasses import replace

import numpy as np
import pytest
from pydantic import ValidationError

from pipeline_integrity import create_registry
from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.uncertainty import (
    EnsembleMetric,
    EnsembleNode,
    EnsembleRequest,
    EnsembleValues,
    JointCase,
    Marginal,
    Threshold,
    UncertainVariable,
    evaluate_ensemble,
    generate_ensemble,
    registry_node,
    verify_ensemble_result,
)


def context():
    return RunContext(
        asset_id="pipe",
        scenario_id="declared_cases",
        clock_id="operation_clock",
        origin="synthetic",
        evidence=(EvidenceRef(id="reference", citation="Original test assumptions", origin="synthetic"),),
    )


def variable(id="x", unit="1", family=None, **parameters):
    return UncertainVariable(
        id=id,
        unit=unit,
        layer="input",
        evidence_id="reference",
        marginal=None if family is None else Marginal(family=family, parameters=parameters),
    )


def metric(id="y", **updates):
    return EnsembleMetric(id=id, unit="1", meaning="quantity", evidence_id="reference", **updates)


def paired(values=(1.0, 3.0, None), weights=(0.2, 0.3, 0.5)):
    return EnsembleRequest(
        id="joint",
        variables=(variable(),),
        method="joint_cases",
        dependence="paired_rows",
        interpretation="scenario",
        evidence_id="reference",
        definition_reviewed=True,
        cases=tuple(
            JointCase(id=f"case_{i}", weight=w, values={"x": x}, evidence_id="reference")
            for i, (x, w) in enumerate(zip(values, weights, strict=True))
        ),
    )


def node(*, name="identity", metrics=None, callback=None):
    metrics = metrics or (metric(),)

    def evaluate(values, ctx):
        x = values["x"]
        return EnsembleValues(
            context=ctx,
            values={m.id: x for m in metrics},
            units={m.id: m.unit for m in metrics},
            statuses={m.id: "computed" if x is not None else "needs_data" for m in metrics},
            native_payload={"x": x},
        )

    return EnsembleNode(
        id=name,
        version="1",
        source_digest="a" * 64,
        configuration={"definition": "original identity"},
        metrics=metrics,
        evaluate=callback or evaluate,
    )


def test_missing_mass_keeps_conditional_statistics_and_conservative_bounds():
    m = metric(
        support_lower=0.0,
        support_upper=10.0,
        threshold=Threshold(value=2.0, comparison="gt", evidence_id="reference"),
    )
    result = evaluate_ensemble(generate_ensemble(paired(), context()), (node(metrics=(m,)),))
    s = result["summaries"]["identity"]["y"]
    assert result["status"] == "needs_data"
    assert s["known_weight"] == s["unresolved_weight"] == 0.5
    assert s["known_weighted_sum"] == pytest.approx(1.1)
    assert s["conditional_on_valid_mean"] == pytest.approx(2.2)
    assert s["full_design_mean"] is None
    assert s["full_design_mean_bounds"] == pytest.approx([1.1, 6.1])
    assert s["conditional_on_valid_quantiles"] == [1.0, 3.0, 3.0]
    assert s["threshold_weight_bounds"]["lower"] == 0.3
    assert s["threshold_weight_bounds"]["upper"] == 0.8
    assert not result["observed_failure_rate_inferred"]
    assert result["record_digest"] == digest({k: v for k, v in result.items() if k != "record_digest"})


def test_all_missing_is_not_zero_and_has_full_threshold_ambiguity():
    result = evaluate_ensemble(
        generate_ensemble(paired((None,), (1.0,)), context()),
        (node(metrics=(metric(threshold=Threshold(value=0.0, comparison="ge", evidence_id="reference")),)),),
    )
    s = result["summaries"]["identity"]["y"]
    assert s["conditional_on_valid_mean"] is None and s["full_design_mean"] is None
    assert s["threshold_weight_bounds"]["upper"] == 1.0
    assert s["threshold_weight_bounds"]["lower"] == 0.0


def test_zero_weight_is_explicitly_skipped_and_does_not_create_missing_mass():
    result = evaluate_ensemble(generate_ensemble(paired((2.0, None), (1.0, 0.0)), context()), (node(),))
    assert result["status"] == "computed" and result["zero_weight_draws_skipped"] == 1
    assert result["summaries"]["identity"]["y"]["full_design_mean"] == 2.0


def test_support_contradiction_retains_value_and_disables_unknown_mean_bounds():
    result = evaluate_ensemble(
        generate_ensemble(paired(), context()),
        (node(metrics=(metric(support_lower=0.0, support_upper=2.0),)),),
    )
    s = result["summaries"]["identity"]["y"]
    assert result["status"] == "out_of_scope" and not s["declared_support_trusted"]
    assert result["rows"][1]["nodes"]["identity"]["values"]["y"] == 3.0
    assert s["full_design_mean_bounds"] is None and s["unresolved_weight"] == 0.8


def test_shared_context_and_cross_node_covariance_preserve_row_pairing():
    seen = []

    def twice(values, ctx):
        seen.append(ctx)
        values["x"] *= 2
        return EnsembleValues(
            context=ctx,
            values={"z": values["x"]},
            units={"z": "1"},
            statuses={"z": "computed"},
            native_payload={},
        )

    result = evaluate_ensemble(
        generate_ensemble(paired((1.0, 3.0), (0.5, 0.5)), context()),
        (node(), node(name="double", metrics=(metric("z"),), callback=twice)),
    )
    assert result["summaries"]["identity"]["y"]["full_design_mean"] == 2.0
    assert result["summaries"]["double"]["z"]["full_design_mean"] == 4.0
    assert result["paired_covariances"][0]["conditional_on_paired_valid_covariance"] == 2.0
    assert all(c.clock_id == "operation_clock" for c in seen)
    assert [c.metadata["ensemble"]["draw_id"] for c in seen] == ["case_0", "case_1"]
    assert all(not c.metadata["ensemble"]["draw_index_is_time"] for c in seen)


def test_foreign_context_and_wrong_units_are_setup_errors():
    def wrong(values, ctx):
        return EnsembleValues(
            context=ctx.model_copy(update={"asset_id": "foreign"}),
            values={"y": 1.0},
            units={"y": "1"},
            statuses={"y": "computed"},
            native_payload={},
        )

    design = generate_ensemble(paired(), context())
    with pytest.raises(ValueError, match="different asset"):
        evaluate_ensemble(design, (node(callback=wrong),))

    def wrong_unit(values, ctx):
        return EnsembleValues(
            context=ctx, values={"y": 1.0}, units={"y": "W"}, statuses={"y": "computed"}, native_payload={}
        )

    with pytest.raises(ValueError, match="units"):
        evaluate_ensemble(design, (node(callback=wrong_unit),))


def test_programming_failures_are_not_silently_removed_or_resampled():
    def broken(values, ctx):
        raise RuntimeError("callback defect")

    with pytest.raises(RuntimeError, match="callback defect"):
        evaluate_ensemble(generate_ensemble(paired(), context()), (node(callback=broken),))


@pytest.mark.parametrize("weights", [(0.2, 0.3), (1.1, -0.1)])
def test_weights_are_not_repaired(weights):
    with pytest.raises(ValidationError):
        paired((1.0, 3.0), weights)


def test_modified_seal_and_resealed_changed_joint_rows_are_rejected():
    design = generate_ensemble(paired(), context())
    design["draws"][0]["values"]["x"] = 99.0
    with pytest.raises(ValueError, match="modified"):
        evaluate_ensemble(design, (node(),))
    design["record_digest"] = digest({k: v for k, v in design.items() if k != "record_digest"})
    with pytest.raises(ValueError, match="differ"):
        evaluate_ensemble(design, (node(),))


def test_ensemble_result_replay_rejects_resealed_output_tampering():
    design = generate_ensemble(paired((1.0, 3.0), (0.5, 0.5)), context())
    nodes = (node(),)
    result = evaluate_ensemble(design, nodes)
    assert verify_ensemble_result(result, design, nodes)["valid"]

    changed = copy.deepcopy(result)
    changed["rows"][0]["nodes"]["identity"]["values"]["y"] = 999.0
    changed["record_digest"] = digest({key: value for key, value in changed.items() if key != "record_digest"})
    verification = verify_ensemble_result(changed, design, nodes)
    assert not verification["valid"]
    assert "replay" in verification["reason"]


def test_unreviewed_and_unresolved_distribution_do_not_call_optional_sampler():
    request = paired().model_copy(update={"definition_reviewed": False})
    design = generate_ensemble(request, context())
    assert design["status"] == "needs_data" and not design["draws"]
    assert not evaluate_ensemble(design, (node(),))["rows"]
    request = EnsembleRequest(
        id="missing",
        variables=(variable(),),
        method="sobol",
        samples=8,
        seed=1,
        dependence="independent",
        interpretation="epistemic",
        evidence_id="reference",
        definition_reviewed=True,
    )
    design = generate_ensemble(request, context())
    assert design["status"] == "needs_data" and "scipy" not in design["runtime"]


@pytest.mark.parametrize(
    "family,parameters",
    [
        ("normal", {"mean": 0.0, "standard_deviation": -1.0}),
        ("normal", {"mean": 0.0, "std": 1.0}),
        ("uniform", {"lower": 2.0, "upper": 1.0}),
        ("triangular", {"lower": 0.0, "mode": 3.0, "upper": 1.0}),
        ("beta", {"alpha": 0.0, "beta": 2.0, "lower": 0.0, "upper": 1.0}),
    ],
)
def test_bad_distribution_parameters_are_not_guessed(family, parameters):
    with pytest.raises(ValidationError):
        Marginal(family=family, parameters=parameters)


@pytest.mark.parametrize("method", ["monte_carlo", "latin_hypercube", "sobol"])
def test_sampler_reproducible_and_lhs_retains_original_strata(method):
    v = variable(family="uniform", lower=0.0, upper=1.0)
    request = EnsembleRequest(
        id="sample",
        variables=(v,),
        method=method,
        samples=256,
        seed=42,
        dependence="independent",
        interpretation="aleatory",
        evidence_id="reference",
        definition_reviewed=True,
    )
    a, b = generate_ensemble(request, context()), generate_ensemble(request, context())
    assert a == b
    x = np.array([r["values"]["x"] for r in a["draws"]])
    assert 0 < x.min() < x.max() < 1
    if method in ("latin_hypercube", "sobol"):
        assert sorted((x * 256).astype(int)) == list(range(256))


@pytest.mark.parametrize(
    "family,parameters,expected",
    [
        ("constant", {"value": 7.0}, 7.0),
        ("normal", {"mean": 2.0, "standard_deviation": 0.2}, 2.0),
        ("lognormal", {"log_mean": 0.0, "log_standard_deviation": 0.2}, np.exp(0.02)),
        ("triangular", {"lower": 1.0, "mode": 2.0, "upper": 6.0}, 3.0),
        ("beta", {"alpha": 2.0, "beta": 3.0, "lower": 1.0, "upper": 6.0}, 3.0),
    ],
)
def test_marginal_sobol_moments_against_analytical_values(family, parameters, expected):
    request = EnsembleRequest(
        id="law",
        variables=(variable(family=family, **parameters),),
        method="sobol",
        samples=4096,
        seed=17,
        dependence="independent",
        interpretation="epistemic",
        evidence_id="reference",
        definition_reviewed=True,
    )
    result = evaluate_ensemble(generate_ensemble(request, context()), (node(),))
    assert result["summaries"]["identity"]["y"]["full_design_mean"] == pytest.approx(expected, abs=0.001)


@pytest.mark.parametrize(
    "matrix",
    [
        ((1.0, 0.4), (0.3, 1.0)),
        ((1.0, 1.1), (1.1, 1.0)),
        ((0.9, 0.0), (0.0, 1.0)),
        ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0)),
        ((1.0, 0.9, 0.9), (0.9, 1.0, -0.9), (0.9, -0.9, 1.0)),
    ],
)
def test_invalid_correlation_is_rejected_instead_of_repaired(matrix):
    variables = tuple(
        variable(id=f"x{i}", family="normal", mean=0.0, standard_deviation=1.0) for i in range(len(matrix))
    )
    with pytest.raises(ValidationError):
        EnsembleRequest(
            id="bad_copula",
            variables=variables,
            method="sobol",
            samples=8,
            seed=1,
            dependence="gaussian_copula",
            latent_correlation=matrix,
            interpretation="epistemic",
            evidence_id="reference",
            definition_reviewed=True,
        )


@pytest.mark.parametrize("rho", [-1.0, -0.7, 0.0, 0.8, 1.0])
def test_joint_normal_product_analytical_moment_including_singular_dependence(rho):
    request = EnsembleRequest(
        id="product",
        variables=(
            variable("x", family="normal", mean=2.0, standard_deviation=0.2),
            variable("h", family="normal", mean=4.0, standard_deviation=0.4),
        ),
        method="sobol",
        samples=4096,
        seed=16,
        dependence="gaussian_copula",
        latent_correlation=((1.0, rho), (rho, 1.0)),
        interpretation="epistemic",
        evidence_id="reference",
        definition_reviewed=True,
    )
    design = generate_ensemble(request, context())
    actual = sum(r["weight"] * r["values"]["x"] * r["values"]["h"] for r in design["draws"])
    assert actual == pytest.approx(8 + rho * 0.08, abs=0.001)
    assert design["copula"]["root_reconstruction_max_absolute_error"] < 1e-12
    assert not design["copula"]["physical_Pearson_correlation_implied"]


def test_sobol_count_no_seed_and_unknown_evidence_fail_explicitly():
    request = EnsembleRequest(
        id="sample",
        variables=(variable(family="uniform", lower=0.0, upper=1.0),),
        method="sobol",
        samples=8,
        seed=1,
        dependence="independent",
        interpretation="scenario",
        evidence_id="reference",
    )
    for update in ({"samples": 7}, {"seed": None}):
        with pytest.raises(ValidationError):
            EnsembleRequest.model_validate(request.model_copy(update=update).model_dump())
    with pytest.raises(ValueError, match="evidence"):
        generate_ensemble(request.model_copy(update={"evidence_id": "missing"}), context())


def test_nonrepresentable_lognormal_is_not_redrawn():
    request = EnsembleRequest(
        id="overflow",
        variables=(variable(family="lognormal", log_mean=1000.0, log_standard_deviation=1.0),),
        method="sobol",
        samples=8,
        seed=1,
        dependence="independent",
        interpretation="epistemic",
        evidence_id="reference",
        definition_reviewed=True,
    )
    design = generate_ensemble(request, context())
    assert design["status"] == "out_of_scope" and design["draws"] == []
    assert "unrepresentable" in design["issues"][0]


def test_conditional_probability_support_is_required_and_violation_held():
    with pytest.raises(ValidationError):
        EnsembleMetric(id="p", unit="1", meaning="conditional_probability", evidence_id="reference")
    m = EnsembleMetric(
        id="p",
        unit="1",
        meaning="conditional_probability",
        support_lower=0.0,
        support_upper=1.0,
        evidence_id="reference",
    )
    result = evaluate_ensemble(
        generate_ensemble(paired((0.2, 1.5), (0.5, 0.5)), context()), (node(metrics=(m,)),)
    )
    assert result["status"] == "out_of_scope"
    assert result["summaries"]["identity"]["p"]["unresolved_weight"] == 0.5


def test_node_configuration_mutation_and_nested_draw_coordinates_fail():
    n = node()

    def mutate(values, ctx):
        n.configuration["definition"] = "changed"
        return EnsembleValues(
            context=ctx, values={"y": 1.0}, units={"y": "1"}, statuses={"y": "computed"}, native_payload={}
        )

    with pytest.raises(ValueError, match="configuration changed"):
        evaluate_ensemble(generate_ensemble(paired(), context()), (replace(n, evaluate=mutate),))
    with pytest.raises(ValueError, match="nested ensemble"):
        evaluate_ensemble(
            generate_ensemble(paired(), context().model_copy(update={"metadata": {"ensemble": {}}})),
            (node(),),
        )


def test_registry_adapter_scalar_units_unknown_and_invalid_physical_inputs():
    v = variable("x", "m3/s")
    request = paired((0.01, None, -0.01), (0.5, 0.2, 0.3)).model_copy(update={"variables": (v,)})
    m = EnsembleMetric(
        id="electrical_power_w",
        unit="W",
        meaning="quantity",
        evidence_id="reference",
        support_lower=0.0,
        support_upper=1e6,
    )
    engine = create_registry()
    base = dict(
        volume_flow_m3_s=0.01, density_kg_m3=1000.0, head_m=40.0, pump_efficiency=0.8, motor_efficiency=0.9
    )
    kwargs = dict(
        id="pump",
        model_id="equipment.pump_power",
        base_inputs=base,
        bindings={"x": "volume_flow_m3_s"},
        variables=(v,),
        metrics=(m,),
    )
    n = registry_node(engine, **kwargs)
    base["head_m"] = 4000.0  # external template edits cannot change a prepared node
    result = evaluate_ensemble(generate_ensemble(request, context()), (n,))
    s = result["summaries"]["pump"][m.id]
    assert result["status"] == "out_of_scope" and s["unresolved_weight"] == 0.5
    assert s["conditional_on_valid_mean"] == pytest.approx(0.01 * 1000 * 9.80665 * 40 / (0.8 * 0.9))
    with pytest.raises(ValueError, match="units"):
        registry_node(engine, **(kwargs | {"variables": (variable("x", "L/min"),)}))
    modified = copy.deepcopy(n.configuration)
    assert modified["base_inputs"]["head_m"] == 40.0
    n.configuration["base_inputs"]["head_m"] = 50.0
    with pytest.raises(ValueError, match="configuration was modified"):
        evaluate_ensemble(generate_ensemble(request, context()), (n,))


@pytest.mark.parametrize("dependence", ["independent", "gaussian_copula"])
def test_transform_endpoint_is_not_clipped_or_resampled(monkeypatch, dependence):
    class EndpointGenerator:
        bit_generator = object()

        def random(self, shape):
            return np.zeros(shape)

    monkeypatch.setattr(np.random, "default_rng", lambda seed: EndpointGenerator())
    request = EnsembleRequest(
        id="endpoint",
        variables=(variable(family="normal", mean=0.0, standard_deviation=1.0),),
        method="monte_carlo",
        samples=8,
        seed=1,
        dependence=dependence,
        latent_correlation=((1.0,),) if dependence == "gaussian_copula" else None,
        interpretation="epistemic",
        evidence_id="reference",
        definition_reviewed=True,
    )
    design = generate_ensemble(request, context())
    assert design["status"] == "out_of_scope" and not design["draws"]


def test_model_output_contract_bug_is_not_relabelled_as_bad_input():
    from pipeline_integrity.contracts import Calculation, Contract, ModelInfo
    from pipeline_integrity.registry import ModelSpec, Registry

    class Input(Contract):
        x: float

    def calculate(p):
        if p.x < 0:
            return Calculation(values={}, status="invalid_programming_status")
        return Calculation(values={"y": p.x})

    engine = Registry()
    engine.register(
        ModelSpec(
            ModelInfo(
                id="example",
                version="1",
                title="Original contract test",
                category="test",
                input_units={"x": "1"},
                output_units={"y": "1"},
                sources=(),
                assumptions=(),
                implementation="user_model",
            ),
            Input,
            calculate,
        )
    )
    n = registry_node(
        engine,
        id="test",
        model_id="example",
        base_inputs={"x": 1.0},
        bindings={"x": "x"},
        variables=(variable(),),
        metrics=(metric(),),
    )
    with pytest.raises(ValidationError, match="invalid_programming_status"):
        evaluate_ensemble(generate_ensemble(paired((-1.0,), (1.0,)), context()), (n,))


def test_unknown_variable_does_not_withhold_an_unrelated_node_output():
    variables = (variable(), variable("unrelated"))
    request = EnsembleRequest(
        id="unrelated",
        variables=variables,
        method="joint_cases",
        dependence="paired_rows",
        interpretation="scenario",
        evidence_id="reference",
        definition_reviewed=True,
        cases=(
            JointCase(id="one", weight=1.0, values={"x": 2.0, "unrelated": None}, evidence_id="reference"),
        ),
    )
    result = evaluate_ensemble(generate_ensemble(request, context()), (node(),))
    assert result["status"] == "computed"
    assert result["summaries"]["identity"]["y"]["full_design_mean"] == 2.0


def test_partial_output_covariance_conditions_on_the_same_valid_pairs():
    def evaluate(values, ctx):
        x = values["x"]
        return EnsembleValues(
            context=ctx,
            values={"a": x, "b": None if x == 3.0 else 2 * x},
            units={"a": "1", "b": "1"},
            statuses={"a": "computed", "b": "needs_data" if x == 3.0 else "computed"},
            native_payload={},
        )

    result = evaluate_ensemble(
        generate_ensemble(paired((1.0, 2.0, 3.0), (0.2, 0.3, 0.5)), context()),
        (node(metrics=(metric("a"), metric("b")), callback=evaluate),),
    )
    assert result["summaries"]["identity"]["a"]["unresolved_weight"] == 0.0
    pair = result["paired_covariances"][0]
    assert pair["paired_known_weight"] == 0.5
    assert pair["conditional_on_paired_valid_covariance"] == pytest.approx(0.48)


def test_example_physics_costs_share_draw_and_retain_alternative_difference():
    import runpy

    example = runpy.run_path("examples/coupled_uncertainty.py")
    n, ctx = example["make_node"]()
    result = evaluate_ensemble(generate_ensemble(example["make_request"](samples=4), ctx), (n,))
    assert result["status"] == "computed"
    for row in result["rows"]:
        output = row["nodes"][n.id]
        assert output["values"]["replace_minus_keep"] == pytest.approx(-99.71854270988888, abs=1e-8)
        native = output["native_payload"]
        assert native["pump"]["context"]["metadata"]["ensemble"]["draw_id"] == row["id"]
        assert native["lifecycle"]["context"] == native["pump"]["context"]
