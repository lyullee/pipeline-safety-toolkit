import math
from pathlib import Path

import pytest

from pipeline_integrity.adapters import monitoring_batch
from pipeline_integrity.contracts import RunContext
from pipeline_integrity.water import WaterRun, simulate_water_network
from pipeline_integrity.water_quality import (
    QualityStepPattern,
    WaterQualityRun,
    WaterQualitySource,
    quality_threshold_arrival,
    simulate_water_quality,
    water_quality_batch,
    water_quality_frames,
)


def context():
    return RunContext(
        asset_id="authored_water_line", scenario_id="reference", clock_id="elapsed", origin="synthetic"
    )


def network(tmp_path):
    wntr = pytest.importorskip("wntr")
    model = wntr.network.WaterNetworkModel()
    model.add_reservoir("R", base_head=100.0)
    model.add_junction("J", base_demand=0.01, elevation=0.0)
    model.add_pipe("P", "R", "J", length=100.0, diameter=0.2, roughness=120.0)
    path = tmp_path / "authored_water.inp"
    wntr.network.write_inpfile(model, str(path))
    return model, path


def spec(mode="concentration", **changes):
    return WaterQualityRun(
        **{
            "hydraulics": WaterRun(duration_s=1000.0, hydraulic_timestep_s=60.0, report_timestep_s=5.0),
            "mode": mode,
            "parameter_source_id": "synthetic_reference",
            "quality_timestep_s": 1.0,
            "quality_tolerance": 1e-8,
            "initial_default_value": 0.0,
            **(
                {
                    "species_id": "passive_marker",
                    "sources": (
                        WaterQualitySource(
                            source_id="inlet", node_id="R", source_type="CONCEN", strength=0.001, unit="kg/m3"
                        ),
                    ),
                }
                if mode == "concentration"
                else {}
            ),
            **({"trace_node_id": "R"} if mode == "source_fraction" else {}),
            **changes,
        }
    )


@pytest.mark.parametrize("mode", ["concentration", "age", "source_fraction"])
def test_plug_travel_time_units_and_native_balance(tmp_path, mode):
    _, path = network(tmp_path)
    initial = {"J": 50.0} if mode == "age" else {}
    result = simulate_water_quality(path, spec(mode, initial_node_values=initial), context())
    assert result["status"] == "computed" and result["checks"]["native_balance_within_tolerance"]
    assert result["native_diagnostics"]["native_version_code"] == 20200
    assert result["native_diagnostics"]["mass_balance_ratio"] == pytest.approx(1.0, abs=1e-7)
    travel = math.pi * 0.2**2 / 4 * 100 / 0.01
    final = result["node_quality"]["J"][-1]
    if mode == "age":
        assert result["node_quality"]["J"][0] == 50.0
        # EPANET 2.2 uses rounded 28.317 L/ft3 with exact 0.3048 m/ft in its native flow geometry.
        native_unit_reference = travel * 28.317 / (1000 * 0.3048**3)
        assert result["quantity_unit"] == "s" and final == pytest.approx(native_unit_reference, rel=2e-6)
        assert abs(final - travel) <= travel * (abs(28.317 / (1000 * 0.3048**3) - 1) + 2e-6)
    else:
        target = 0.001 if mode == "concentration" else 1.0
        assert final == pytest.approx(target, rel=1e-6)
        arrival = quality_threshold_arrival(result, "J", target / 2)
        assert arrival["sample_bracket_s"][0] <= travel <= arrival["sample_bracket_s"][1]
        assert not arrival["probability_calculated"]
        if mode == "source_fraction":
            with pytest.raises(ValueError, match="zero and one"):
                quality_threshold_arrival(result, "J", 50.0)


def test_first_order_decay_matches_characteristic_exponential(tmp_path):
    _, path = network(tmp_path)
    k = 0.001
    result = simulate_water_quality(path, spec(bulk_decay_per_s=k), context())
    assert result["status"] == "computed"
    travel = math.pi * 0.2**2 / 4 * 100 / 0.01
    concentration = result["node_quality"]["J"][-1]
    expected = 0.001 * math.exp(-k * travel)
    assert expected * math.exp(-k) <= concentration <= expected * math.exp(k)


def test_symmetric_two_inlet_mixing_is_not_a_source_probability(tmp_path):
    wntr = pytest.importorskip("wntr")
    model, path = network(tmp_path)
    model.add_reservoir("clean", base_head=100.0)
    model.add_pipe("second", "clean", "J", length=100.0, diameter=0.2, roughness=120.0)
    wntr.network.write_inpfile(model, str(path))
    result = simulate_water_quality(
        path,
        spec(hydraulics=WaterRun(duration_s=2000.0, report_timestep_s=10.0, hydraulic_timestep_s=60.0)),
        context(),
    )
    assert result["status"] == "computed"
    assert result["node_quality"]["J"][-1] == pytest.approx(0.0005, rel=2e-6)
    assert result["hydraulics"]["flow_m3_s"]["P"][-1] == pytest.approx(0.005, rel=1e-5)


def test_source_event_grid_preserves_existing_demand_period_and_phase(tmp_path):
    wntr = pytest.importorskip("wntr")
    model, path = network(tmp_path)
    model.options.time.pattern_timestep = 60
    model.options.time.pattern_start = 30
    model.add_pattern("demand_cycle", [1.0, 2.0])
    model.get_node("J").demand_timeseries_list[0].pattern_name = "demand_cycle"
    wntr.network.write_inpfile(model, str(path))
    injection = WaterQualitySource(
        source_id="pulse",
        node_id="R",
        source_type="CONCEN",
        strength=0.001,
        unit="kg/m3",
        pattern=QualityStepPattern(time_s=(0.0, 20.0, 40.0), factors=(0.0, 1.0, 0.0)),
    )
    result = simulate_water_quality(
        path,
        spec(
            sources=(injection,),
            hydraulics=WaterRun(duration_s=180.0, hydraulic_timestep_s=10.0, report_timestep_s=10.0),
        ),
        context(),
    )
    assert result["status"] == "computed" and result["pattern_clock"]["pattern_timestep_s"] == 10
    for t, flow in zip(result["elapsed_time_s"], result["hydraulics"]["flow_m3_s"]["P"], strict=True):
        assert flow == pytest.approx(0.01 * [1.0, 2.0][int((t + 30) // 60) % 2], rel=1e-5)


def test_quality_frames_keep_context_offset_units_and_reject_mutated_results(tmp_path):
    _, path = network(tmp_path)
    result = simulate_water_quality(path, spec(start_time_s=200.0), context())
    frames = tuple(water_quality_frames(result, node_ids=("J",), stream_id="water_quality_stream"))
    assert frames[0].time_s == 200.0 and frames[-1].time_s == 1200.0
    assert frames[0].clock_id == "elapsed" and frames[0].origin == "simulation"
    assert frames[0].units == {"concentration:J": "kg/m3"}
    batch = water_quality_batch(result, node_ids=("J",), factory=lambda **kwargs: kwargs)
    assert batch["metadata"]["source_result_digest"] == result["result_digest"]
    assert batch["metadata"]["quantity_meaning"] == "constituent_mass_per_carrier_volume"
    assert batch["origin"] == "simulation" and batch["metadata"]["input_context_origin"] == "synthetic"
    with pytest.raises(ValueError, match="contradicts"):
        monitoring_batch(frames, metadata={"clock_id": "different"}, factory=lambda **kwargs: kwargs)
    result["node_quality"]["J"][0] = 100.0
    with pytest.raises(ValueError, match="changed"):
        tuple(water_quality_frames(result, stream_id="changed"))


def test_quality_configuration_does_not_silently_change_meaning_or_resource_budget(tmp_path):
    for changes in ({"sources": spec().sources}, {"initial_default_value": 0.5}, {"bulk_decay_per_s": 0.1}):
        with pytest.raises(ValueError):
            spec("source_fraction", **changes)
    with pytest.raises(ValueError, match="unit"):
        WaterQualitySource(source_id="bad", node_id="R", source_type="MASS", strength=0.1, unit="kg/m3")
    _, path = network(tmp_path)
    with pytest.raises(ValueError, match="budget"):
        simulate_water_quality(path, spec(maximum_returned_values=1), context())
    with pytest.raises(ValueError, match="unknown"):
        simulate_water_quality(path, spec(initial_node_values={"absent": 1.0}), context())


def test_hydraulic_reports_do_not_inherit_average_statistics_or_delayed_start(tmp_path):
    wntr = pytest.importorskip("wntr")
    model, path = network(tmp_path)
    model.options.time.statistic = "AVERAGED"
    model.options.time.report_start = 60
    wntr.network.write_inpfile(model, str(path))
    result = simulate_water_network(path, WaterRun(duration_s=120.0, report_timestep_s=60.0))
    assert result["time_s"] == [0.0, 60.0, 120.0]


@pytest.mark.parametrize(
    "source_type,strength,unit",
    [("MASS", 1e-5, "kg/s"), ("FLOWPACED", 0.001, "kg/m3"), ("SETPOINT", 0.001, "kg/m3")],
)
def test_dose_source_types_keep_mass_rate_and_concentration_conventions(
    tmp_path, source_type, strength, unit
):
    wntr = pytest.importorskip("wntr")
    model, path = network(tmp_path)
    model.add_source("removed_source", "R", "CONCEN", 0.5)
    wntr.network.write_inpfile(model, str(path))
    injection = WaterQualitySource(
        source_id="declared_dose", node_id="J", source_type=source_type, strength=strength, unit=unit
    )
    result = simulate_water_quality(path, spec(sources=(injection,)), context())
    assert result["status"] == "computed"
    assert result["node_quality"]["J"][-1] == pytest.approx(0.001, rel=1e-6)
    assert len(result["input_quality_reset"]["removed_source_ids"]) == 1
    assert list(result["input_quality_reset"]["removed_source_nodes"].values()) == ["R"]


def test_quality_cli_resolves_network_relative_to_saved_job(tmp_path):
    pytest.importorskip("wntr")
    from pipeline_integrity.cli import main

    output = tmp_path / "quality_result.json"
    request = Path(__file__).resolve().parents[1] / "examples/water_quality.json"
    assert main(["water-quality", str(request), "--output", str(output)]) == 0
    import json

    result = json.loads(output.read_text(encoding="utf-8"))
    assert result["status"] == "computed" and result["quantity_unit"] == "kg/m3"


def test_nonfinite_hydraulic_budget_is_withheld_and_serializes_as_missing(tmp_path):
    import json
    from types import SimpleNamespace

    import pandas as pd

    from pipeline_integrity.water import _water_results

    model, path = network(tmp_path)
    native = SimpleNamespace(
        link={"flowrate": pd.DataFrame({"P": [float("nan")]}, index=[0])},
        node={
            "demand": pd.DataFrame({"R": [-0.01], "J": [0.01]}, index=[0]),
            "pressure": pd.DataFrame({"R": [0.0], "J": [99.0]}, index=[0]),
        },
        error_code=None,
    )
    result = _water_results(path, model, native, WaterRun())
    assert result["status"] == "withheld" and result["maximum_junction_continuity_residual_m3_s"] is None
    assert result["flow_m3_s"]["P"] == [None]
    json.dumps(result, allow_nan=False)


def test_native_warning_blocks_hydraulic_and_transport_handoff_despite_small_continuity(tmp_path):
    wntr = pytest.importorskip("wntr")
    model, path = network(tmp_path)
    model.options.hydraulic.unbalanced = "CONTINUE"
    model.options.hydraulic.unbalanced_value = 10
    wntr.network.write_inpfile(model, str(path))
    hydraulics = WaterRun(duration_s=60, report_timestep_s=30, solver_maximum_trials=1)
    water = simulate_water_network(path, hydraulics)
    result = simulate_water_quality(path, spec(hydraulics=hydraulics), context())
    assert water["status"] == result["status"] == "withheld"
    assert water["native_diagnostics"]["warning_count"] > 0
    assert result["native_diagnostics"]["warning_count"] > 0
    assert water["maximum_junction_continuity_residual_m3_s"] < hydraulics.continuity_tolerance_m3_s
    with pytest.raises(ValueError, match="computed"):
        water_quality_batch(result, factory=lambda **kwargs: kwargs)


def test_native_hydraulic_period_budget_is_enforced(tmp_path):
    _, path = network(tmp_path)
    with pytest.raises(ValueError, match="period budget"):
        simulate_water_network(
            path,
            WaterRun(
                duration_s=120, hydraulic_timestep_s=30, report_timestep_s=30, maximum_hydraulic_periods=1
            ),
        )
