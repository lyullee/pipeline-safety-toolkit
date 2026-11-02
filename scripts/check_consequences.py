"""Reproduce native source, compressible-plane, consequence and finite-window annual accounting."""

import argparse
import json
import math
import sys
from importlib.metadata import version
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.consequence_exchange import (
    AnnualCondition,
    AnnualEvent,
    AnnualRiskRequest,
    CompressiblePlane,
    ConsequenceCase,
    PRISMConsequenceClient,
)
from pipeline_integrity.contracts import RunContext, digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.equipment_exchange import EquipmentRequest, LH2EquipmentClient
from pipeline_integrity.release_exchange import PipeAccidentRequest, prism_release
from pipeline_integrity.risk_exchange import RiskFrequency


def main():
    parser = argparse.ArgumentParser()
    for name in ("lh2dt", "prism-source", "slabx", "slabx-lh2"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument('--output-dir',type=Path,default=Path('artifacts/consequences'))
    parser.add_argument('--backend-timeout-s',type=float,default=60.)
    args = parser.parse_args()
    sys.path[:0] = [str((args.lh2dt / "src").resolve()), str(args.prism_source.resolve())]
    from lh2_tno.backend import BackendConfig, IsolatedBackend, PackagePin, tree_digest

    packages = {
        "prism_qra": args.prism_source / "prism_qra",
        "prism_lh2_models": args.prism_source / "prism_lh2_models",
        "slabx": args.slabx / "src/slabx",
        "slabx_lh2": args.slabx_lh2 / "slabx_lh2",
    }
    config = BackendConfig(
        interpreter=sys.executable,
        timeout_s=args.backend_timeout_s,
        packages=tuple(
            PackagePin(name=k, root=str(p.resolve()), sha256=tree_digest(p)) for k, p in packages.items()
        ),
        required_versions={k: version(k) for k in ("numpy", "scipy", "CoolProp", "pydantic")},
    )
    context = RunContext(
        asset_id="consequence_pipe",
        scenario_id="synthetic_finite_window",
        clock_id="elapsed",
        origin="synthetic",
    )
    equipment = LH2EquipmentClient(
        fluid="Hydrogen", property_basis="CoolProp8_HEOS_Hydrogen_default_reference"
    )
    handle = equipment.build(
        EquipmentRequest(
            model_id="dynamic_hem_pipe",
            parameter_source="authored_finite_window_reference",
            parameters={
                "length_m": 1.0,
                "inner_diameter_m": 0.05,
                "roughness_m": 1e-6,
                "wall_heat_capacity_J_K": 1e4,
                "local_loss_coefficient": 0.0,
                "fluid_wall_UA_W_K": 0.0,
                "ambient_temperature_K": 288.15,
                "ambient_UA_W_K": 0.0,
            },
        ),
        context,
    )

    def source(step):
        history = equipment.export_accident_history(
            handle,
            PipeAccidentRequest(
                event_id="consequence_pipe/synthetic_leak",
                port_id="failed_opening",
                clock_id=context.clock_id,
                start_time_s=10.0,
                source_id="authored_native_source_reference",
                initial_pressure_pa_abs=1e6,
                initial_temperature_k=300.0,
                wall_temperature_k=300.0,
                ambient_pressure_pa_abs=101325.0,
                ambient_temperature_k=288.15,
                failure_opening_diameter_m=0.001,
                discharge_coefficient=0.8,
                horizon_s=1.0,
                time_step_s=step,
                position_enu_m=(0.0, 0.0, 1.0),
                direction_enu=(1.0, 0.0, 0.0),
            ),
        )
        assert history.status == "exchange_checked", history.issues
        return prism_release(history)

    base, refined = source(0.05), source(0.025)
    packet = base.native
    facility = {
        "id": "synthetic_conditional_facility",
        "configuration_origin": "synthetic",
        "environment": "outdoor",
        "components": [
            {
                "id": context.asset_id,
                "kind": "pipe",
                "position_enu_m": [0.0, 0.0, 1.0],
                "property_basis": packet.property_basis.model_dump(mode="json"),
                "parameters_si": {"length_m": 1.0, "diameter_m": 0.05},
            }
        ],
        "ports": [
            {
                "id": packet.port_id,
                "component_id": context.asset_id,
                "phase": "vapour",
                "direction": "out",
                "position_enu_m": [0.0, 0.0, 1.0],
            }
        ],
        "edges": [],
        "receptors": [
            {"id": "near", "position_enu_m": [0.1, 0.0, 1.0]},
            {"id": "middle", "position_enu_m": [1.0, 0.0, 1.0]},
            {"id": "far", "position_enu_m": [4.0, 1.0, 1.0]},
        ],
    }
    defaults = {
        "weather": {"wind_speed_m_s": 2.0, "wind_from_bearing_deg": 270.0},
        "ignition": {
            "direct_probability": 0.2,
            "delayed_probability_given_no_direct": 0.1,
            "explosion_probability_given_delayed": 0.25,
            "delayed_time_s": 0.5,
            "burn_back_duration_s": 0.2,
            "obstructed_energy_fraction": 0.05,
            "provenance": "authored conditional scenario assumptions, not observed frequencies",
        },
        "domain": {
            "downwind_bounds_m": [-2.0, 10.0],
            "crosswind_bounds_m": [-5.0, 5.0],
            "height_bounds_m": [0.0, 5.0],
            "cell_size_m": 0.5,
        },
        "evaluation_times_s": [round(i * 0.1, 12) for i in range(16)] + [2.0, 3.0],
        "radiative_fraction_range": [0.04, 0.08],
        "radiation_provenance": "authored uncalibrated radiation sensitivity",
        "release_termination": {
            "basis": "synthetic_diagnostic_horizon",
            "time_s": 1.0,
            "provenance": "finite diagnostic source window; physical cessation not established",
        },
        "source_operator": "integrated_transient",
        "dispersion_x_max_m": 20.0,
        "transition_dx_m": 0.1,
        "transition_x_max_m": 10.0,
        "n_puff_steps": 20,
    }
    client = PRISMConsequenceClient(IsolatedBackend(config))
    progress = args.output_dir
    progress.mkdir(parents=True, exist_ok=True)

    def retain(label, result):
        (progress / f"{label}.json").write_text(result.model_dump_json(indent=2) + "\n", encoding="utf-8")
        print(
            json.dumps(
                {
                    "case": label,
                    "status": result.status,
                    "issues": result.issues,
                    "error": result.native_result.get("message") if result.issues else None,
                }
            ),
            flush=True,
        )

    case = ConsequenceCase(
        id="wind_270",
        initiating_condition_id="release_initiated",
        facility=facility,
        defaults=defaults,
        evidence_ids=("authored:conditional_reference",),
    )
    without_plane = client.evaluate(base, case)
    retain("unadapted", without_plane)
    assert without_plane.status == "withheld"
    assert "compressible" in without_plane.native_result["message"]
    case = case.model_copy(
        update={"compressible_plane": CompressiblePlane(evidence_id="explicit:native_notional_plane")}
    )
    first = client.evaluate(base, case)
    retain("wind_270", first)
    assert first.status == "computed_diagnostic", first.issues
    alternate_defaults = dict(defaults) | {"weather": {"wind_speed_m_s": 2.0, "wind_from_bearing_deg": 180.0}}
    crosswind = client.evaluate(
        base, case.model_copy(update={"id": "wind_180", "defaults": alternate_defaults})
    )
    retain("wind_180", crosswind)
    assert crosswind.status == "withheld"
    assert "no positive downwind velocity" in crosswind.native_result["message"]
    alternate_defaults = dict(defaults) | {"weather": {"wind_speed_m_s": 2.0, "wind_from_bearing_deg": 250.0}}
    alternate = client.evaluate(
        base, case.model_copy(update={"id": "wind_250", "defaults": alternate_defaults})
    )
    retain("wind_250", alternate)
    assert alternate.status == "withheld"
    assert "handoff geometry mass residual" in alternate.native_result["message"]
    refinement = client.evaluate(refined, case.model_copy(update={"id": "time_refinement"}))
    retain("time_refinement", refinement)
    assert refinement.status == "computed_diagnostic", refinement.issues
    mass_difference = abs(packet.cumulative_mass_out_kg - refined.native.cumulative_mass_out_kg)
    assert math.isclose(
        packet.cumulative_mass_out_kg, refined.native.cumulative_mass_out_kg, rel_tol=0.01, abs_tol=0
    )
    # This is an assumed LOC rate for arithmetic, separate from HRA demand failures.
    rate_source = {"annual_frequency_per_year": 0.01, "basis": "authored synthetic LOC assumption"}
    frequency = RiskFrequency(
        id=packet.event_id,
        annual_frequency_per_year=0.01,
        condition_id="release_initiated",
        endpoint="loss_of_containment",
        evidence_ids=("assumed:loc_rate",),
        context=context,
        source_payload=rate_source,
        source_digest=digest(rate_source),
    )
    conditions = (
        AnnualCondition(
            id="wind_270",
            probability=1.0,
            occupancy_probability=0.5,
            occupancy_evidence_id="assumed:presence",
            consequence=first,
        ),
    )
    annual_event = AnnualEvent(
        event_id=packet.event_id,
        frequency=frequency,
        conditions=conditions,
        partition_evidence_id="assumed:single_known_condition_reference",
        frequency_condition_independence_evidence_id="assumed:frequency_weather_independence",
    )
    annual_request = AnnualRiskRequest(
        assessment_id="synthetic_window_accounting",
        events=(annual_event,),
        receptor_ids=("near", "middle", "far"),
        initiating_event_partition_evidence_id="authored:one_selected_event",
        time_scope_evidence_id="authored:one_second_source_and_three_second_harm_window",
    )
    annual = client.annual_risk(annual_request)
    missing = client.annual_risk(
        annual_request.model_copy(update={"events": (annual_event.model_copy(update={"frequency": None}),)})
    )
    assert missing["status"] == "needs_frequency"
    assert all(
        row["value_low_high"] is None and row["uncomputed_harm_envelope_low_high"] is None
        for row in missing["receptor_results"].values()
    )
    a = {r["id"]: r for r in first.native_result["data"]["receptors"]}
    for key, pair in annual["receptor_results"].items():
        for index in range(2):
            expected = 0.01 * 0.5 * (a[key]["harm_given_observed_release_low_high"][index])
            assert math.isclose(pair["value_low_high"][index], expected, rel_tol=1e-12, abs_tol=0)
    incomplete = client.annual_risk(
        annual_request.model_copy(
            update={
                "events": (
                    annual_event.model_copy(
                        update={
                            "conditions": (
                                conditions[0].model_copy(update={"probability": 0.6}),
                                conditions[0].model_copy(
                                    update={"id": "wind_180", "probability": 0.2, "consequence": crosswind}
                                ),
                                conditions[0].model_copy(
                                    update={"id": "wind_250", "probability": 0.2, "consequence": alternate}
                                ),
                            ),
                            "partition_evidence_id": "assumed:three_condition_partition",
                        }
                    ),
                )
            }
        )
    )
    assert incomplete["status"] == "needs_consequence"
    for key, row in incomplete["receptor_results"].items():
        assert row["value_low_high"] is None
        expected_low = 0.01 * 0.5 * 0.6 * a[key]["harm_given_observed_release_low_high"][0]
        expected_high = 0.01 * 0.5 * (0.6 * a[key]["harm_given_observed_release_low_high"][1] + 0.4)
        assert math.isclose(
            row["uncomputed_harm_envelope_low_high"][0], expected_low, rel_tol=1e-12, abs_tol=0
        )
        assert math.isclose(
            row["uncomputed_harm_envelope_low_high"][1], expected_high, rel_tol=1e-12, abs_tol=0
        )

    def check_blocked(action):
        try:
            action()
        except ValueError as error:
            return str(error)
        raise AssertionError("an incompatible risk/frequency binding was accepted")

    wrong_rate = RiskFrequency(
        **(frequency.model_dump(exclude={"binding_digest"}) | {"endpoint": "event_occurrence"})
    )
    guard = check_blocked(
        lambda: client.annual_risk(
            annual_request.model_copy(
                update={"events": (annual_event.model_copy(update={"frequency": wrong_rate}),)}
            )
        )
    )
    root = Path("artifacts/consequences")
    root.mkdir(parents=True, exist_ok=True)
    full = {
        "source": base.record(),
        "refined_source": refined.record(),
        "unadapted": without_plane.model_dump(mode="json"),
        "crosswind_uncomputed": crosswind.model_dump(mode="json"),
        "conditional": [first.model_dump(mode="json"), alternate.model_dump(mode="json")],
        "refinement": refinement.model_dump(mode="json"),
        "annual": annual,
        "incomplete_annual": incomplete,
        "missing_frequency": missing,
    }
    target = root / "native_chain.json"
    target.write_text(json.dumps(full, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    record = {
        "schema": "pipeline_integrity.consequence_checks.v1",
        "sources": {
            "PRISM": revision(args.prism_source.parent),
            "lh2dt_models": revision(args.lh2dt),
            "slabx": revision(args.slabx),
            "slabx_lh2_commit": _git_commit(args.slabx_lh2),
        },
        "backend_packages": [{"name": p.name, "source_sha256": p.sha256} for p in config.packages],
        "required_versions": config.required_versions,
        "unadapted_status": without_plane.status,
        "adapted_statuses": {
            "wind_270": first.status,
            "wind_180": crosswind.status,
            "wind_250": alternate.status,
            "refinement": refinement.status,
        },
        "source_interval_counts": [len(packet.steps), len(refined.native.steps)],
        "released_mass_kg": [packet.cumulative_mass_out_kg, refined.native.cumulative_mass_out_kg],
        "mass_timestep_difference_kg": mass_difference,
        "mass_timestep_relative_tolerance": 0.01,
        "plane_energy_residual_j_kg": first.plane_evidence["generation_audit"][
            "max_energy_residual_j_per_kg"
        ],
        "conditional_harm_sensitivity": {key: a[key]["harm_given_observed_release_low_high"] for key in a},
        "oblique_plane_status": alternate.status,
        "oblique_plane_reason": "wind_projection_geometry_mass_mismatch",
        "oblique_cosine_mass_difference_percent": 100.0 * (1.0 - math.cos(math.radians(20.0))),
        "annualized_window_rate_sensitivity": {
            k: row["value_low_high"] for k, row in annual["receptor_results"].items()
        },
        "crosswind_status": crosswind.status,
        "crosswind_reason": "no_positive_downwind_plane_velocity",
        "incomplete_condition_status": incomplete["status"],
        "incomplete_condition_envelope": {
            k: row["uncomputed_harm_envelope_low_high"] for k, row in incomplete["receptor_results"].items()
        },
        "mass_residuals_kg": [
            r.native_result["data"]["mass_ledger"]["end_to_end_residual_kg"] for r in (first, refinement)
        ],
        "missing_frequency_status": missing["status"],
        "wrong_frequency_guard": guard,
        "local_native_record_sha256": file_digest(target),
        "implementation_sha256": file_digest(Path("src/pipeline_integrity/consequence_exchange.py")),
        "complete_accident_or_facility_risk": False,
        "independent_physical_validation": False,
        "external_sources_modified": False,
    }
    Path("research/consequence_results.json").write_text(
        json.dumps(record, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {k: v for k, v in record.items() if k not in {"sources", "backend_packages", "required_versions"}}
        )
    )


def _git_commit(path):
    import subprocess

    return subprocess.run(
        ["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


if __name__ == "__main__":
    main()
