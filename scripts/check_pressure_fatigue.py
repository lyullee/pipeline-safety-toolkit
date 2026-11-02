"""Exercise actual material-owned fatigue models, liquid pressure histories and unresolved observations."""

import argparse
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
from check_integrations import revision

from pipeline_integrity.adapters import MaterialClient
from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.datasets import catalog, file_digest
from pipeline_integrity.fatigue_exchange import (
    FatigueGeometry,
    PressureHistory,
    assess_pressure_fatigue,
    pressure_history_from_transient,
)
from pipeline_integrity.loaders import hydraulic_cycle
from pipeline_integrity.transient import LiquidTransient, simulate_liquid_transient


def replace_history(history, **changes):
    return PressureHistory.model_validate_json(
        json.dumps(history.model_dump(mode="json") | {"binding_digest": None} | changes)
    )


def summary(result):
    cycles = result.values.get("cycles", [])
    return dict(
        status=result.status,
        result_digest=result.result_digest,
        history_digest=result.history_digest,
        source_sha256=result.source_sha256,
        pressure_origin=result.pressure_origin,
        temperature_origin=result.temperature_origin,
        native_history_origin=result.native_history_origin,
        curve_basis=result.curve_basis,
        counted_cycles=result.values.get("counted_cycles"),
        initial_damage=result.values.get("initial_damage"),
        damage_increment=result.values.get("damage_increment"),
        total_damage=result.values.get("total_damage"),
        counted_cycle_records=len(cycles),
        stress_ratio_min=None if not cycles else min(row["stress_ratio"] for row in cycles),
        stress_ratio_max=None if not cycles else max(row["stress_ratio"] for row in cycles),
        field_validation=result.field_validation,
        failure_probability_calculated=result.failure_probability_calculated,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path, required=True)
    args = parser.parse_args()
    sys.path[:0] = [str((args.materials / "src").resolve()), str(Path("examples").resolve())]
    import rainflow
    from pressure_fatigue import make_case

    before = revision(args.materials)
    root = Path("artifacts/fatigue")
    root.mkdir(parents=True, exist_ok=True)
    request, context = make_case()
    client = MaterialClient()
    reference = assess_pressure_fatigue(request, context, client=client)
    reference.check_integrity()
    assert reference.status == "computed"
    assert reference.values["counted_cycles"] == 2.0
    assert math.isclose(reference.values["damage_increment"], 0.002, abs_tol=1e-14)
    assert math.isclose(reference.values["total_damage"], 0.102, abs_tol=1e-14)
    assert [row["count"] for row in reference.values["cycles"]] == [0.5] * 4
    owner_cases = {
        "mechanics.thin_cylinder": dict(
            pressure_difference_Pa=4e6,
            mean_radius_m=0.1,
            wall_thickness_m=0.005,
            allowable_hoop_stress_Pa=200e6,
        ),
        "fatigue.rainflow": dict(values=[40e6, 80e6, 40e6, 80e6, 40e6], quantity_unit="Pa"),
        "fatigue.basquin": dict(
            stress_range_Pa=40e6,
            reference_range_Pa=40e6,
            reference_cycles=1000.0,
            exponent=3.0,
        ),
        "fatigue.miner": dict(cycle_counts=[2.0], cycles_to_failure=[1000.0], declared_damage_limit=1.0),
    }
    owned = {
        name: client.evaluate(name, parameters, context, location_id=request.history.location_id)
        for name, parameters in owner_cases.items()
    }
    assert all(r.status == "computed" for r in owned.values())
    assert owned["mechanics.thin_cylinder"].payload["values"]["hoop_stress_Pa"] == 80e6
    assert owned["fatigue.rainflow"].payload["values"]["cycle_count"] == 2.0
    assert owned["fatigue.basquin"].payload["values"]["cycles_to_failure"] == 1000.0
    assert owned["fatigue.miner"].payload["values"]["damage"] == 0.002
    doubled = request.model_copy(
        update={"history": replace_history(request.history, pressure_pa=[2e6, 6e6, 2e6, 6e6, 2e6])}
    )
    scaled = assess_pressure_fatigue(doubled, context, client=client)
    assert scaled.status == "computed" and math.isclose(
        scaled.values["damage_increment"], 0.016, abs_tol=1e-14
    )
    overloaded = assess_pressure_fatigue(
        request.model_copy(update={"curve": request.curve.model_copy(update={"reference_cycles": 0.001})}),
        context,
        client=client,
    )
    assert overloaded.status == "computed" and overloaded.values["total_damage"] > 1
    assert overloaded.values["damage_margin"] < 0 and not overloaded.failure_probability_calculated
    rejected = {}
    for key, changes in {
        "stress_range": {"curve": request.curve.model_copy(update={"minimum_stress_range_pa": 50e6})},
        "stress_ratio": {"curve": request.curve.model_copy(update={"minimum_stress_ratio": 0.6})},
        "temperature": {"history": replace_history(request.history, temperature_k=[310.0] * 5)},
        "wall_geometry": {"geometry": request.geometry.model_copy(update={"wall_thickness_m": 0.02})},
        "external_pressure": {"external_pressure_pa_abs": 5e6},
    }.items():
        rejected[key] = assess_pressure_fatigue(request.model_copy(update=changes), context, client=client)
        assert rejected[key].status == "out_of_scope", key
    job = {"specification": request.model_dump(mode="json"), "context": context.model_dump(mode="json")}
    Path("examples/pressure_fatigue.json").write_text(json.dumps(job, indent=2) + "\n", encoding="utf-8")
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str((args.materials / "src").resolve()), str(Path("src").resolve())]
    )
    call = subprocess.run(
        [
            sys.executable,
            "-m",
            "pipeline_integrity.cli",
            "pressure-fatigue",
            "examples/pressure_fatigue.json",
            "--output",
            str(root / "cli_result.json"),
        ],
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert call.returncode == 0, call.stderr
    assert (
        json.loads((root / "cli_result.json").read_text(encoding="utf-8"))["result_digest"]
        == reference.result_digest
    )

    motion_job = json.loads(Path("examples/valve_transient.json").read_text(encoding="utf-8"))
    liquid_context = RunContext.model_validate_json(json.dumps(motion_job["context"]))
    spec = LiquidTransient.model_validate_json(json.dumps(motion_job["specification"]))
    liquid = {}
    for name, step in (("base", spec.requested_timestep_s), ("refined", spec.requested_timestep_s / 2)):
        selected = spec.model_copy(update={"requested_timestep_s": step})
        transient = simulate_liquid_transient(selected, liquid_context)
        pipe = selected.pipes[0]
        h = pressure_history_from_transient(
            transient,
            selected,
            pipe_id=pipe.id,
            endpoint="end",
            temperature_k=293.15,
            temperature_evidence_id="liquid_assumptions",
            source_id="liquid_source",
        )
        ctx = liquid_context.model_copy(
            update={
                "evidence": liquid_context.evidence
                + (
                    EvidenceRef(
                        id="liquid_source",
                        citation="Original synthetic liquid characteristic calculation",
                        origin="simulation",
                        sha256=h.source_sha256,
                        license_id="MIT",
                    ),
                    EvidenceRef(
                        id="liquid_assumptions",
                        citation="Declared wall, uniform temperature and artificial fatigue curve",
                        origin="synthetic",
                        license_id="MIT",
                    ),
                )
            }
        )
        fatigue = request.model_copy(
            update={
                "history": h,
                "geometry": FatigueGeometry(
                    mean_radius_m=pipe.diameter_m / 2 + 0.0025,
                    wall_thickness_m=0.005,
                    maximum_thickness_to_radius_ratio=0.1,
                    evidence_id="liquid_assumptions",
                ),
                "curve": request.curve.model_copy(
                    update={
                        "minimum_stress_range_pa": 1e-6,
                        "maximum_stress_range_pa": 1e9,
                        "evidence_id": "liquid_assumptions",
                    }
                ),
                "external_pressure_evidence_id": "liquid_assumptions",
                "initial_damage_evidence_id": "liquid_assumptions",
                "damage_limit_evidence_id": "liquid_assumptions",
            }
        )
        liquid[name] = assess_pressure_fatigue(fatigue, ctx, client=client)
        assert liquid[name].status == "computed"
        (root / f"liquid_{name}.json").write_text(
            liquid[name].model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
    assert math.isclose(
        liquid["base"].values["damage_increment"],
        liquid["refined"].values["damage_increment"],
        rel_tol=1e-10,
        abs_tol=1e-14,
    )

    archive = Path("data/raw/hydraulic/source.zip")
    dataset = next(d for d in catalog() if d.id == "hydraulic")
    source_hash = file_digest(archive)
    assert dataset.assets[0].checksum == f"sha256:{source_hash}"
    observed = hydraulic_cycle(archive, 0, ("PS1",))["sensors"]["PS1"]
    source = "hydraulic_observations"
    assumptions = "hydraulic_hypothetical_mapping"
    observed_context = RunContext(
        asset_id="hypothetical_pipe_for_hydraulic_signal",
        scenario_id="conditional_signal_mapping",
        clock_id=observed["clock_id"],
        origin="laboratory",
        evidence=(
            EvidenceRef(
                id=source,
                citation=dataset.citation,
                origin="laboratory",
                locator="PS1.txt; cycle_index=0; 100 Hz; 6000 pressure samples",
                sha256=source_hash,
                license_id=dataset.license_id,
            ),
            EvidenceRef(
                id=assumptions,
                citation="Explicit hypothetical pressure reference, uniform temperature, pipe and fatigue curve",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    size = len(observed["time_s"])
    h = PressureHistory(
        asset_id=observed_context.asset_id,
        location_id="PS1_hypothetical_wall",
        scenario_id=observed_context.scenario_id,
        clock_id=observed_context.clock_id,
        time_s=tuple(observed["time_s"]),
        pressure_pa=tuple(observed["values"] * 1e5),
        pressure_reference="unspecified",
        temperature_k=(None,) * size,
        valid=tuple(bool(v) for v in observed["valid"]),
        pressure_origin="laboratory",
        temperature_origin="user_declared",
        source_id=source,
        source_sha256=source_hash,
        temperature_evidence_id=assumptions,
    )
    base = request.model_copy(
        update={
            "history": h,
            "geometry": request.geometry.model_copy(update={"evidence_id": assumptions}),
            "curve": request.curve.model_copy(update={"evidence_id": assumptions}),
            "external_pressure_evidence_id": assumptions,
            "initial_damage_evidence_id": assumptions,
            "damage_limit_evidence_id": assumptions,
        }
    )
    unresolved = assess_pressure_fatigue(base, observed_context, client=client)
    assert unresolved.status == "needs_data" and unresolved.native_result is None
    conditional = {}
    for pressure_reference in ("absolute", "gauge"):
        converted = replace_history(
            h,
            pressure_reference=pressure_reference,
            temperature_k=[293.15] * size,
            ambient_pressure_pa_abs=101325.0 if pressure_reference == "gauge" else None,
        )
        result = assess_pressure_fatigue(
            base.model_copy(update={"history": converted}), observed_context, client=client
        )
        assert result.status == "computed" and result.native_history_origin == "model_prediction"
        conditional[pressure_reference] = result
        (root / f"hydraulic_{pressure_reference}.json").write_text(
            result.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
    assert math.isclose(
        conditional["absolute"].values["damage_increment"],
        conditional["gauge"].values["damage_increment"],
        rel_tol=1e-10,
        abs_tol=1e-14,
    )
    assert revision(args.materials)["source_tree_sha256"] == before["source_tree_sha256"]
    (root / "reference.json").write_text(reference.model_dump_json(indent=2) + "\n", encoding="utf-8")
    result = dict(
        schema="pipeline_integrity.pressure_fatigue_checks.v1",
        materials_source=before,
        materials_history_sha256=file_digest(args.materials / "src/matintegrity/models/history.py"),
        native_runtime=liquid["base"].native_result.payload["runtime"],
        rainflow_source_sha256=file_digest(Path(rainflow.__file__)),
        rainflow_license="MIT; Piotr Janiszewski",
        verification_script_sha256=file_digest(Path(__file__)),
        exchange_sha256=file_digest(Path("src/pipeline_integrity/fatigue_exchange.py")),
        reference=summary(reference),
        native_model_checks={
            name: dict(status=r.status, values=r.payload["values"], payload_digest=r.payload_digest)
            for name, r in owned.items()
        },
        doubled_range=summary(scaled),
        above_one_damage=summary(overloaded),
        native_domain_rejections={key: r.status for key, r in rejected.items()},
        actual_cli_result_matches=True,
        liquid={key: summary(value) for key, value in liquid.items()},
        hydraulic=dict(
            source_sha256=source_hash,
            source_citation=dataset.citation,
            creators=dataset.creators,
            license_id=dataset.license_id,
            cycle_index=0,
            pressure_samples=size,
            sample_rate_hz=observed["sample_rate_hz"],
            pressure_min_bar=float(np.min(observed["values"])),
            pressure_max_bar=float(np.max(observed["values"])),
            unresolved=summary(unresolved),
            hypothetical_only={key: summary(value) for key, value in conditional.items()},
            same_damage_does_not_resolve_pressure_reference=True,
            actual_geometry_temperature_and_material_calibration_missing=True,
        ),
        original_observation_rows_excluded_from_release=True,
        independent_material_or_field_validation=False,
        fatigue_damage_is_failure_probability=False,
    )
    Path("research/pressure_fatigue_results.json").write_text(
        json.dumps(result, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "reference": result["reference"],
                "liquid": result["liquid"],
                "hydraulic_unresolved_status": unresolved.status,
                "hydraulic_samples": size,
            }
        )
    )


if __name__ == "__main__":
    main()
