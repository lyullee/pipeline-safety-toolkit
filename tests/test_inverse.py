import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame, digest
from pipeline_integrity.inverse import (
    FitParameter,
    FitRequest,
    Observations,
    ObservedChannel,
    OperatingPoint,
    OperatingPointFitRequest,
    OperatingPointObservations,
    OperatingPointPrediction,
    Prediction,
    ResidualScale,
    fit_operating_points,
    fit_parameters,
    observations_from_frames,
)


def point_case(*, heldout_offset=0.0, missing=False):
    ids = ("batch_B", "batch_A", "held_1", "batch_C", "held_2", "unused")
    values = tuple(
        2.0 * (i + 1) + 3.0 + (heldout_offset if key.startswith("held") else 0) for i, key in enumerate(ids)
    )
    source = digest({"point_ids": ids, "values": values, "kind": "authored spatial-state fixture"})
    points = tuple(
        OperatingPoint(
            id=key,
            state={"position": float(i + 1)},
            state_units={"position": "m"},
            group_id="one_apparatus",
            evidence_ids=("point_source",),
            source_digests={"authored": source},
        )
        for i, key in enumerate(ids)
    )
    observations = OperatingPointObservations(
        id="steady_fixture",
        asset_id="reference",
        origin="synthetic",
        source_digests={"authored": source},
        points=points,
        channels=(
            ObservedChannel(
                id="y",
                scale=ResidualScale(value=0.1, unit="m", source_id="point_scale"),
                values=tuple(
                    None if missing and key == "batch_A" else value
                    for key, value in zip(ids, values, strict=True)
                ),
                valid=tuple(not (missing and key == "batch_A") for key in ids),
            ),
        ),
    )
    request = OperatingPointFitRequest(
        parameters=(
            FitParameter(id="a", unit="1", lower=0.0, upper=5.0, initial=1.0),
            FitParameter(id="b", unit="m", lower=0.0, upper=10.0, initial=1.0),
        ),
        provider_id="authored_steady_linear",
        provider_version="1",
        training_point_ids=("batch_C", "batch_B", "batch_A"),
        heldout_point_ids=("held_2", "held_1"),
    )
    c = RunContext(
        asset_id="reference",
        scenario_id="steady_fit",
        clock_id="execution_log_only",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="point_source",
                citation="Original steady spatial-state and response fixture",
                origin="synthetic",
                sha256=source,
            ),
            EvidenceRef(
                id="point_scale",
                citation="Declared deterministic scale; no covariance assumption",
                origin="synthetic",
            ),
        ),
    )
    return observations, request, c


def steady_linear(parameters, points, ctx):
    output = tuple(parameters["a"] * p.state["position"] + parameters["b"] for p in points)
    return OperatingPointPrediction(
        status="computed",
        asset_id=ctx.asset_id,
        point_ids=tuple(p.id for p in points),
        point_digests={p.id: digest(p) for p in points},
        values={"y": output},
        units={"y": "m"},
        provider_source_digest=digest("authored steady spatial linear provider v1"),
        evaluation_digest=digest((parameters, tuple(digest(p) for p in points), output)),
    )


def test_operating_point_fit_has_no_fake_time_axis_and_exact_source_id_holdout():
    obs, req, ctx = point_case()
    result = fit_operating_points(obs, req, ctx, steady_linear)
    changed, changed_req, changed_ctx = point_case(heldout_offset=1000.0)
    other = fit_operating_points(changed, changed_req, changed_ctx, steady_linear)
    assert result["status"] == other["status"] == "fitted"
    assert result["fitted_parameters"] == pytest.approx({"a": 2.0, "b": 3.0}, abs=1e-5)
    assert result["fitted_parameters"] == other["fitted_parameters"]
    assert result["training_point_ids"] == ["batch_C", "batch_B", "batch_A"]
    assert result["heldout_point_ids"] == ["held_2", "held_1"]
    assert result["unused_rows"] == 1 and result["same_group_holdout"]
    assert not result["field_validation_claim"] and not result["independent_experiment_validation"]
    assert not result["covariance_or_error_independence_inferred"]
    assert all("point_ids" in e and "time_range_s" not in e for e in result["evaluations"])
    assert not result["validation"]["used_for_parameter_selection"]
    assert other["validation"]["fitted"]["normalized_rmse"] > 9999


def test_point_invalid_sample_does_not_compress_or_rename_source_axis():
    obs, req, ctx = point_case(missing=True)
    result = fit_operating_points(obs, req, ctx, steady_linear)
    assert result["status"] == "fitted"
    assert result["training"]["fitted"]["channels"]["y"]["valid_samples"] == 2
    assert result["training_point_ids"] == list(req.training_point_ids)


def test_point_split_source_and_units_are_explicit_contracts():
    obs, req, ctx = point_case()
    with pytest.raises(ValueError):
        OperatingPointFitRequest.model_validate(
            req.model_copy(update={"heldout_point_ids": ("batch_A",)}).model_dump(mode="python")
        )
    with pytest.raises(ValueError):
        fit_operating_points(
            obs, req.model_copy(update={"heldout_point_ids": ("not_observed",)}), ctx, steady_linear
        )
    with pytest.raises(ValueError):
        fit_operating_points(obs, req, ctx.model_copy(update={"evidence": ()}), steady_linear)
    point = obs.points[0].model_copy(update={"source_digests": {"authored": "0" * 64}})
    with pytest.raises(ValueError):
        fit_operating_points(
            obs.model_copy(update={"points": (point,) + obs.points[1:]}), req, ctx, steady_linear
        )
    point = obs.points[0].model_copy(update={"state_units": {"unrelated": "m"}})
    with pytest.raises(ValueError):
        fit_operating_points(
            obs.model_copy(update={"points": (point,) + obs.points[1:]}), req, ctx, steady_linear
        )


@pytest.mark.parametrize("mutation", ["order", "digest", "unit", "state_mutation", "context_mutation"])
def test_actual_point_provider_cannot_change_order_state_source_units_or_context(mutation):
    obs, req, ctx = point_case()

    def altered(parameters, points, context):
        result = steady_linear(parameters, points, context)
        if mutation == "order":
            return result.model_copy(update={"point_ids": tuple(reversed(result.point_ids))})
        if mutation == "digest":
            return result.model_copy(update={"point_digests": {key: "0" * 64 for key in result.point_ids}})
        if mutation == "unit":
            return result.model_copy(update={"units": {"y": "Pa"}})
        if mutation == "state_mutation":
            points[0].state["position"] += 1
        else:
            context.metadata["changed"] = True
        return result

    with pytest.raises(ValueError):
        fit_operating_points(obs, req, ctx, altered)


def test_point_budget_withheld_and_rank_results_reuse_shared_kernel():
    obs, req, ctx = point_case()
    result = fit_operating_points(
        obs, req.model_copy(update={"maximum_model_evaluations": 1}), ctx, steady_linear
    )
    assert result["status"] == "budget_exhausted" and result["fitted_parameters"] is None

    def withheld(parameters, points, context):
        return steady_linear(parameters, points, context).model_copy(update={"status": "withheld"})

    result = fit_operating_points(obs, req, ctx, withheld)
    assert result["status"] == "withheld" and result["fitted_parameters"] is None

    def summed(parameters, points, context):
        return steady_linear({"a": parameters["a"] + parameters["b"], "b": 3.0}, points, context)

    result = fit_operating_points(obs, req, ctx, summed)
    assert result["status"] == "unidentifiable" and result["local_identifiability"]["rank"] == 1


def context():
    return RunContext(asset_id="reference", scenario_id="fit", clock_id="elapsed", origin="synthetic")


def observations(validation_offset=0.0, missing=False, outlier=False):
    frames = []
    for t in range(10):
        value = 2.0 * t + 3.0 + (validation_offset if t >= 6 else 0.0) + (20.0 if outlier and t == 4 else 0.0)
        frames.append(
            SignalFrame(
                stream_id="reference",
                clock_id="elapsed",
                time_s=float(t),
                values={"y": None if missing and t == 2 else value},
                units={"y": "m"},
                valid={"y": not (missing and t == 2)},
                origin="synthetic",
            )
        )
    return observations_from_frames(
        frames,
        observation_id="linear",
        asset_id="reference",
        source_digests={"authored": digest("authored linear observations")},
        scales={"y": ResidualScale(value=0.1, unit="m", source_id="declared_weight_not_standard_deviation")},
    )


def request(**changes):
    return FitRequest(
        **{
            "parameters": (
                FitParameter(id="a", unit="m/s", lower=0.0, upper=5.0, initial=1.0),
                FitParameter(id="b", unit="m", lower=0.0, upper=10.0, initial=1.0),
            ),
            "provider_id": "authored_linear",
            "provider_version": "1",
            "fit_end_s": 4.0,
            "validation_start_s": 6.0,
            "validation_end_s": 9.0,
            **changes,
        }
    )


def linear(values, times, ctx):
    output = tuple(values["a"] * t + values["b"] for t in times)
    return Prediction(
        status="computed",
        asset_id=ctx.asset_id,
        clock_id=ctx.clock_id,
        time_s=times,
        values={"y": output},
        units={"y": "m"},
        provider_source_digest=digest("authored linear v1"),
        evaluation_digest=digest((values, times, output)),
    )


def test_exact_recovery_and_held_out_values_do_not_change_selected_parameters():
    first = fit_parameters(observations(), request(), context(), linear)
    changed = fit_parameters(observations(validation_offset=1000.0), request(), context(), linear)
    assert first["status"] == changed["status"] == "fitted"
    assert first["fitted_parameters"] == pytest.approx({"a": 2.0, "b": 3.0}, abs=1e-5)
    assert first["fitted_parameters"] == changed["fitted_parameters"]
    assert first["local_identifiability"]["rank"] == 2
    assert first["validation"]["fitted"]["normalized_rmse"] < 1e-4
    assert changed["validation"]["fitted"]["normalized_rmse"] > 9999
    assert first["unused_rows"] == 1 and not first["validation"]["used_for_parameter_selection"]


def test_invalid_samples_are_excluded_without_time_compression():
    result = fit_parameters(observations(missing=True), request(), context(), linear)
    assert result["fitted_parameters"] == pytest.approx({"a": 2.0, "b": 3.0}, abs=1e-5)
    assert result["training"]["fitted"]["channels"]["y"]["valid_samples"] == 4


def test_rank_deficiency_is_not_reported_as_identified_parameters():
    def summed(values, times, ctx):
        return linear({"a": values["a"] + values["b"], "b": 3.0}, times, ctx)

    result = fit_parameters(observations(), request(), context(), summed)
    assert result["status"] == "unidentifiable"
    assert result["local_identifiability"]["rank"] == 1
    assert result["local_identifiability"]["condition_number"] is None


def test_budget_and_withheld_forward_runs_never_return_a_fitted_parameter_set():
    result = fit_parameters(observations(), request(maximum_model_evaluations=1), context(), linear)
    assert result["status"] == "budget_exhausted" and result["model_evaluations"] == 1
    assert result["fitted_parameters"] is None

    def withheld(values, times, ctx):
        return linear(values, times, ctx).model_copy(update={"status": "withheld"})

    result = fit_parameters(observations(), request(), context(), withheld)
    assert result["status"] == "withheld" and result["fitted_parameters"] is None


@pytest.mark.parametrize(
    "mutation", ["duplicate_times", "nan_value", "bad_scale", "bad_budget", "invalid_context"]
)
def test_time_model_copy_is_revalidated_before_optional_solver_import(mutation, monkeypatch):
    obs, req, ctx = observations(), request(), context()
    if mutation == "duplicate_times":
        obs = obs.model_copy(update={"time_s": (0.0,) * len(obs.time_s)})
    elif mutation == "nan_value":
        channel = obs.channels[0].model_copy(update={"values": (float("nan"), *obs.channels[0].values[1:])})
        obs = obs.model_copy(update={"channels": (channel,)})
    elif mutation == "bad_scale":
        channel = obs.channels[0].model_copy(
            update={"scale": obs.channels[0].scale.model_copy(update={"value": 0.0})}
        )
        obs = obs.model_copy(update={"channels": (channel,)})
    elif mutation == "bad_budget":
        req = req.model_copy(update={"maximum_model_evaluations": True})
    else:
        ctx = ctx.model_copy(update={"metadata": {"invalid": float("nan")}})
    monkeypatch.setattr(
        "pipeline_integrity.inverse._scipy_least_squares",
        lambda: pytest.fail("invalid cloned input imported solver"),
    )
    with pytest.raises(ValueError):
        fit_parameters(obs, req, ctx, linear)


@pytest.mark.parametrize("kind", ["withheld_time", "withheld_asset", "nan", "wrong_dtype", "bad_diagnostics"])
def test_time_predictions_are_revalidated_and_withheld_axis_is_bound(kind):
    def bad(values, times, ctx):
        result = linear(values, times, ctx)
        if kind == "withheld_time":
            return result.model_copy(update={"status": "withheld", "time_s": tuple(reversed(times))})
        if kind == "withheld_asset":
            return result.model_copy(update={"status": "withheld", "asset_id": "another_asset"})
        if kind == "nan":
            return result.model_copy(update={"values": {"y": (float("nan"),) * len(times)}})
        if kind == "bad_diagnostics":
            return result.model_copy(update={"diagnostics": {"hidden": float("nan")}})
        return result.model_copy(update={"values": {"y": tuple("1" for _ in times)}})

    with pytest.raises(ValueError):
        fit_parameters(observations(), request(), context(), bad)


@pytest.mark.parametrize("axis", ["time", "point"])
def test_provider_cannot_change_requested_parameter_dictionary(axis):
    def time_provider(values, times, ctx):
        values["a"] = 500.0
        return linear(values, times, ctx)

    def point_provider(values, points, ctx):
        values["a"] = 500.0
        return steady_linear(values, points, ctx)

    with pytest.raises(ValueError, match="parameter values"):
        if axis == "time":
            fit_parameters(observations(), request(), context(), time_provider)
        else:
            obs, req, ctx = point_case()
            fit_operating_points(obs, req, ctx, point_provider)


def test_time_context_mutation_is_rejected_without_changing_callers_context():
    ctx = context()

    def mutated(values, times, actual):
        result = linear(values, times, actual)
        actual.metadata["changed"] = True
        return result

    with pytest.raises(ValueError, match="mutated"):
        fit_parameters(observations(), request(), ctx, mutated)
    assert ctx.metadata == {}


def test_point_identity_mutation_is_a_contract_error_not_a_dictionary_key_error():
    obs, req, ctx = point_case()

    def changed(values, points, actual):
        result = steady_linear(values, points, actual)
        object.__setattr__(points[0], "id", "not_a_requested_id")
        return result

    with pytest.raises(ValueError, match="mutated"):
        fit_operating_points(obs, req, ctx, changed)
    assert obs.points[0].id == "batch_B"


@pytest.mark.parametrize("axis", ["time", "point"])
def test_withheld_prediction_cannot_hide_a_changed_provider_source(axis):
    calls = []

    def changed(values, coordinates, ctx):
        result = (
            linear(values, coordinates, ctx) if axis == "time" else steady_linear(values, coordinates, ctx)
        )
        calls.append(1)
        if len(calls) > 1:
            return result.model_copy(
                update={
                    "status": "withheld",
                    "values": {},
                    "provider_source_digest": digest("different provider source"),
                }
            )
        return result

    with pytest.raises(ValueError, match="source changed"):
        if axis == "time":
            fit_parameters(observations(), request(), context(), changed)
        else:
            obs, req, ctx = point_case()
            fit_operating_points(obs, req, ctx, changed)


def test_shared_mutable_prediction_does_not_change_earlier_cached_evaluations():
    shared = []

    def reused(values, times, ctx):
        fresh = linear(values, times, ctx)
        if not shared:
            shared.append(fresh)
        else:
            shared[0].values.clear()
            shared[0].values.update(fresh.values)
            object.__setattr__(shared[0], "time_s", times)
            object.__setattr__(shared[0], "evaluation_digest", fresh.evaluation_digest)
        return shared[0]

    result = fit_parameters(observations(), request(), context(), reused)
    assert result["status"] == "fitted"
    assert result["fitted_parameters"] == pytest.approx({"a": 2.0, "b": 3.0}, abs=1e-5)


def test_previous_parameter_alias_does_not_rewrite_evaluation_trace():
    previous = []

    def retained(values, times, ctx):
        if previous:
            previous[-1]["a"] = -999.0
        previous.append(values)
        return linear(values, times, ctx)

    result = fit_parameters(observations(), request(), context(), retained)
    assert result["status"] == "fitted"
    assert all(0 <= row["parameters"]["a"] <= 5 for row in result["evaluations"])


def test_large_finite_physical_rmse_does_not_overflow_intermediate_square():
    obs = Observations(
        id="authored_large_math",
        asset_id="reference",
        clock_id="elapsed",
        origin="synthetic",
        source_digests={"authored": digest("large finite arithmetic fixture; no physical qualification")},
        time_s=tuple(float(i) for i in range(10)),
        channels=(
            ObservedChannel(
                id="y",
                scale=ResidualScale(value=1e200, unit="m", source_id="authored_normalization"),
                values=(3e200,) * 10,
                valid=(True,) * 10,
            ),
        ),
    )
    req = request(parameters=(FitParameter(id="offset", unit="m", lower=0.0, upper=1e201, initial=1e200),))

    def constant(values, times, ctx):
        return Prediction(
            status="computed",
            asset_id=ctx.asset_id,
            clock_id=ctx.clock_id,
            time_s=times,
            values={"y": (values["offset"],) * len(times)},
            units={"y": "m"},
            provider_source_digest=digest("authored constant mathematical function"),
            evaluation_digest=digest((values, times)),
        )

    result = fit_parameters(obs, req, context(), constant)
    assert result["status"] == "fitted"
    assert result["training"]["baseline"]["channels"]["y"]["rmse"] == pytest.approx(2e200)


def test_robust_loss_reduces_the_influence_of_a_training_outlier():
    plain = fit_parameters(observations(outlier=True), request(), context(), linear)
    robust = fit_parameters(observations(outlier=True), request(loss="soft_l1"), context(), linear)
    assert (
        robust["validation"]["fitted"]["normalized_rmse"]
        < plain["validation"]["fitted"]["normalized_rmse"] / 10
    )


def test_context_unit_and_source_changes_are_rejected():
    with pytest.raises(ValueError, match="asset and clock"):
        fit_parameters(
            observations(), request(), context().model_copy(update={"clock_id": "different"}), linear
        )

    def wrong_unit(values, times, ctx):
        return linear(values, times, ctx).model_copy(update={"units": {"y": "Pa"}})

    with pytest.raises(ValueError, match="units"):
        fit_parameters(observations(), request(), context(), wrong_unit)
    calls = []

    def changing(values, times, ctx):
        calls.append(1)
        return linear(values, times, ctx).model_copy(update={"provider_source_digest": digest(len(calls))})

    with pytest.raises(ValueError, match="source changed"):
        fit_parameters(observations(), request(), context(), changing)
    with pytest.raises(ValueError, match="strictly after"):
        request(validation_start_s=4.0)


def test_water_provider_recovers_known_demand_and_roughness_without_changing_source():
    pytest.importorskip("wntr")
    from pathlib import Path

    from pipeline_integrity.datasets import file_digest
    from pipeline_integrity.water import WaterRun
    from pipeline_integrity.water_inverse import WaterForward, WaterParameter, WaterSensor

    path = Path(__file__).resolve().parents[1] / "examples/water_transport.inp"
    before = file_digest(path)
    forward = WaterForward(
        path,
        specification=WaterRun(duration_s=600, hydraulic_timestep_s=100, report_timestep_s=100),
        sensors=(
            WaterSensor(channel_id="pressure:J", element_id="J", quantity="pressure_head"),
            WaterSensor(channel_id="flow:P", element_id="P", quantity="volume_flow"),
        ),
        parameters=(
            WaterParameter(parameter_id="demand", kind="demand_multiplier"),
            WaterParameter(parameter_id="roughness", kind="pipe_roughness_multiplier", element_ids=("P",)),
        ),
    )
    truth = forward(
        {"demand": 1.12, "roughness": 0.93}, tuple(float(t) for t in range(0, 601, 100)), context()
    )
    frames = [
        SignalFrame(
            stream_id="reference",
            clock_id="elapsed",
            time_s=t,
            values={k: v[i] for k, v in truth.values.items()},
            units=truth.units,
            valid={k: True for k in truth.units},
            origin="synthetic",
        )
        for i, t in enumerate(truth.time_s)
    ]
    observed = observations_from_frames(
        frames,
        observation_id="water",
        asset_id="reference",
        source_digests={"network": before},
        scales={
            "pressure:J": ResidualScale(value=0.01, unit="m", source_id="synthetic_test_scale"),
            "flow:P": ResidualScale(value=1e-5, unit="m3/s", source_id="synthetic_test_scale"),
        },
    )
    fit = FitRequest(
        parameters=tuple(
            FitParameter(id=p, unit="1", lower=0.8, upper=1.2, initial=1.0) for p in ("demand", "roughness")
        ),
        provider_id="water",
        provider_version="1",
        fit_end_s=200,
        validation_start_s=300,
        validation_end_s=600,
    )
    result = fit_parameters(observed, fit, context(), forward)
    assert result["status"] == "fitted"
    assert result["fitted_parameters"] == pytest.approx({"demand": 1.12, "roughness": 0.93}, abs=0.003)
    assert file_digest(path) == before
    bad = fit.model_copy(
        update={"parameters": (fit.parameters[0].model_copy(update={"unit": "Pa"}), fit.parameters[1])}
    )
    with pytest.raises(ValueError, match="units differ"):
        fit_parameters(observed, bad, context(), forward)


def test_saved_water_fit_job_executes_through_cli(tmp_path):
    pytest.importorskip("wntr")
    import json
    from pathlib import Path

    from pipeline_integrity.cli import main

    output = tmp_path / "fit.json"
    job = Path(__file__).resolve().parents[1] / "examples/water_calibration.json"
    assert main(["water-fit", str(job), "--output", str(output)]) == 0
    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "fitted"
    assert result["fitted_parameters"] == pytest.approx({"demand": 1.12, "roughness": 0.93}, abs=0.003)


def test_water_binding_keeps_emitter_head_law_demand_group_and_flow_direction():
    pytest.importorskip("wntr")
    import math
    from pathlib import Path

    from pipeline_integrity.water import WaterRun
    from pipeline_integrity.water_inverse import WaterForward, WaterParameter, WaterSensor

    path = Path(__file__).resolve().parents[1] / "examples/water_transport.inp"
    forward = WaterForward(
        path,
        specification=WaterRun(),
        sensors=(
            WaterSensor(channel_id="head", element_id="J", quantity="pressure_head"),
            WaterSensor(channel_id="reverse_flow", element_id="P", quantity="volume_flow", flow_sign=-1),
        ),
        parameters=(
            WaterParameter(parameter_id="head", kind="reservoir_head_offset", element_ids=("R",)),
            WaterParameter(parameter_id="demand", kind="junction_demand_multiplier", element_ids=("J",)),
            WaterParameter(parameter_id="leak", kind="emitter_coefficient", element_ids=("J",)),
        ),
    )
    result = forward({"head": 10.0, "demand": 0.8, "leak": 0.001}, (0.0,), context())
    assert result.status == "computed"
    assert -result.values["reverse_flow"][0] - 0.008 == pytest.approx(
        0.001 * math.sqrt(result.values["head"][0]), rel=2e-5
    )
    assert result.values["head"][0] > 100.0
    with pytest.raises(ValueError, match="finite"):
        forward({"head": float("nan"), "demand": 0.8, "leak": 0.001}, (0.0,), context())


def test_initial_tank_level_binding_is_a_level_above_bottom(tmp_path):
    wntr = pytest.importorskip("wntr")
    from pipeline_integrity.water import WaterRun
    from pipeline_integrity.water_inverse import WaterForward, WaterParameter, WaterSensor

    m = wntr.network.WaterNetworkModel()
    m.add_tank("T", elevation=100.0, init_level=5.0, min_level=0.0, max_level=10.0, diameter=10.0)
    m.add_junction("J", base_demand=0.001)
    m.add_pipe("P", "T", "J", length=100.0, diameter=0.2, roughness=120.0)
    path = tmp_path / "tank_reference.inp"
    wntr.network.write_inpfile(m, str(path))
    forward = WaterForward(
        path,
        specification=WaterRun(),
        sensors=(WaterSensor(channel_id="level", element_id="T", quantity="water_level"),),
        parameters=(
            WaterParameter(parameter_id="level_offset", kind="initial_tank_level_offset", element_ids=("T",)),
        ),
    )
    result = forward({"level_offset": 0.5}, (0.0,), context())
    assert result.status == "computed" and result.values["level"][0] == pytest.approx(5.5, abs=1e-5)
    with pytest.raises(ValueError, match="outside"):
        forward({"level_offset": 6.0}, (0.0,), context())
