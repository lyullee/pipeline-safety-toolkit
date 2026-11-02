"""Fit declared water-network parameters using earlier SCADA rows and report later-row errors."""

import argparse
import json
from pathlib import Path

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.datasets import catalog, file_digest
from pipeline_integrity.inverse import (
    FitParameter,
    FitRequest,
    ResidualScale,
    fit_parameters,
    observations_from_frames,
)
from pipeline_integrity.loaders import iter_battledim_frames
from pipeline_integrity.water import WaterRun
from pipeline_integrity.water_inverse import WaterForward, WaterParameter, WaterSensor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("data/raw/battledim"))
    args = parser.parse_args()
    import wntr

    frames = tuple(iter_battledim_frames(args.root, 2018, maximum_rows=576))[::6]
    units = frames[0].units
    sensor = []
    for name in units:
        quantity, element = name.split(":", 1)
        sensor.append(WaterSensor(channel_id=name, element_id=element, quantity=quantity))
    scales = {
        name: ResidualScale(
            value=0.001 if unit == "m3/s" else 0.1 if name.startswith("water_level:") else 1.0,
            unit=unit,
            source_id="declared_equal_channel_weight_scales_not_measurement_standard_deviation",
        )
        for name, unit in units.items()
    }
    sources = {
        name: file_digest(args.root / name)
        for name in (
            "L-TOWN.inp",
            "README.txt",
            "2018_SCADA_Pressures.csv",
            "2018_SCADA_Flows.csv",
            "2018_SCADA_Levels.csv",
        )
    }
    observations = observations_from_frames(
        frames,
        observation_id="battledim_first_two_days_every_sixth_report",
        asset_id="nominal_L_TOWN",
        source_digests=sources,
        scales=scales,
    )
    specification = WaterRun(
        duration_s=frames[-1].time_s,
        hydraulic_timestep_s=300,
        report_timestep_s=300,
        solver_relative_accuracy=1e-6,
        solver_head_error_m=1e-5,
        solver_flow_change_m3_s=1e-8,
    )
    network = wntr.network.WaterNetworkModel(str(args.root / "L-TOWN.inp"))
    parameters = (
        WaterParameter(parameter_id="demand_scale", kind="demand_multiplier"),
        WaterParameter(
            parameter_id="roughness_scale",
            kind="pipe_roughness_multiplier",
            element_ids=tuple(network.pipe_name_list),
        ),
    )
    forward = WaterForward(
        args.root / "L-TOWN.inp", specification=specification, sensors=tuple(sensor), parameters=parameters
    )
    request = FitRequest(
        parameters=tuple(
            FitParameter(id=p.parameter_id, unit=p.unit, lower=0.9, upper=1.1, initial=1.0)
            for p in parameters
        ),
        provider_id="water_forward",
        provider_version="1",
        fit_end_s=84600,
        validation_start_s=86400,
        validation_end_s=frames[-1].time_s,
        maximum_model_evaluations=150,
        maximum_optimizer_evaluations=20,
        parameter_tolerance=1e-4,
        difference_step_fraction=0.01,
    )
    dataset = next(d for d in catalog() if d.id == "battledim")
    context = RunContext(
        asset_id="nominal_L_TOWN",
        scenario_id="first_day_parameter_fit_second_day_check",
        clock_id=observations.clock_id,
        origin="simulation",
        evidence=(
            EvidenceRef(
                id="battledim", citation=dataset.citation, origin="simulation", license_id=dataset.license_id
            ),
        ),
    )
    count = 0

    def predict(values, times, ctx):
        nonlocal count
        count += 1
        result = forward(values, times, ctx)
        if count == 1 or count % 5 == 0:
            print(
                json.dumps({"model_evaluations": count, "status": result.status, "parameters": values}),
                flush=True,
            )
        return result

    predict.parameter_units = forward.parameter_units
    result = fit_parameters(observations, request, context, predict)
    folder = Path("artifacts/calibration")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "observations.json").write_text(observations.model_dump_json(indent=2), encoding="utf-8")
    (folder / "fit.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    record = {
        "schema": "pipeline_integrity.calibration_application.v1",
        "fit": result,
        "forward_specification": specification.model_dump(mode="json"),
        "sensor_mapping": [s.model_dump(mode="json") for s in sensor],
        "source_citation": dataset.citation,
        "license_id": dataset.license_id,
        "data_origin": "simulation",
        "independent_field_validation": False,
        "temporal_holdout_does_not_remove_shared_network_model_bias": True,
        "leak_labels_or_hidden_generating_network_used": False,
        "residual_scales_are_measurement_uncertainties": False,
        "source_engines_modified": False,
        "raw_or_transformed_external_rows_in_public_summary": False,
        "implementation_files_sha256": {
            name: file_digest(Path("src/pipeline_integrity") / name)
            for name in ("inverse.py", "water_inverse.py", "water.py", "epanet.py")
        },
    }
    Path("research/calibration_results.json").write_text(
        json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": result["status"],
                "parameters": result["fitted_parameters"],
                "model_evaluations": result["model_evaluations"],
                "public_summary": "research/calibration_results.json",
            }
        )
    )


if __name__ == "__main__":
    main()
