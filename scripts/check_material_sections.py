"""Execute owned solid mechanics/thermal models, independent balances and source-preserving rod readers."""

import argparse
import csv
import io
import json
import math
import sys
import zipfile
from copy import deepcopy
from pathlib import Path

from check_integrations import revision
from scipy.integrate import quad, simpson

from pipeline_integrity.adapters import MaterialClient, hra_signal_series, monitoring_batch
from pipeline_integrity.contracts import EvidenceRef
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.material_exchange import (
    evaluate_material_job,
    evaluate_pressure_section,
    prepare_material_job,
    solid_thermal_frames,
    thermal_rod_frames,
)


def main():
    parser = argparse.ArgumentParser()
    for key in ("materials", "monitoring", "hra"):
        parser.add_argument("--" + key, type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw/thermal_rod"))
    args = parser.parse_args()
    roots = {key: getattr(args, key) for key in ("materials", "monitoring", "hra")}
    before = {key: revision(path) for key, path in roots.items()}
    sys.path[:0] = [str((p / "src").resolve()) for p in roots.values()] + [str(Path("examples").resolve())]
    from material_sections import context, heat_case, pressure_case, thermal_stress_case

    client = MaterialClient()
    ctx = context()
    private = Path("artifacts/material_sections")
    private.mkdir(parents=True, exist_ok=True)
    receipts = {}
    summary = dict(
        schema="pipeline_integrity.material_sections_verification.v1", model_cases={}, pressure_sections={}
    )

    def run(name, model, parameters):
        receipt = evaluate_material_job(
            prepare_material_job(
                model, parameters, ctx, location_id="section", evidence_ids=("authored",), client=client
            ),
            ctx,
            client=client,
        )
        receipt.check_integrity()
        receipts[name] = receipt
        summary["model_cases"][name] = dict(
            model_id=model,
            status=receipt.native.status,
            result_digest=receipt.result_digest,
            authored_reference_values=receipt.native.payload["values"],
        )
        return receipt.native.payload["values"]

    for label, pi, pe, closed in [
        ("closed", 2e6, 1e5, True),
        ("open_zero_axial", 2e6, 1e5, False),
        ("hydrostatic", 3e5, 3e5, True),
        ("external_dominant", 1e5, 2e6, True),
    ]:
        section = evaluate_pressure_section(
            pressure_case(count=129, pi=pi, pe=pe, closed=closed), ctx, client=client
        )
        assert section["status"] == "computed"
        values = [r["native"]["payload"]["values"] for r in section["material_records"]]
        radii = section["specification"]["evaluation_radii_m"]
        force = float(simpson([v["hoop_stress_Pa"] for v in values], x=radii))
        expected = pi * radii[0] - pe * radii[-1]
        assert math.isclose(force, expected, rel_tol=1e-9, abs_tol=1e-6)
        axial = values[0]["axial_stress_Pa"] * math.pi * (radii[-1] ** 2 - radii[0] ** 2)
        axial_ref = math.pi * (pi * radii[0] ** 2 - pe * radii[-1] ** 2) if closed else 0.0
        assert math.isclose(axial, axial_ref, rel_tol=1e-12, abs_tol=1e-8)
        if label == "hydrostatic":
            assert max(v["von_mises_Pa"] for v in values) < 1e-8
        (private / (label + ".json")).write_text(json.dumps(section, indent=2) + "\n", encoding="utf-8")
        summary["pressure_sections"][label] = dict(
            sampled_radii=len(radii),
            boundary_residuals=section["boundary_traction_residuals"],
            hoop_resultant_residual_n_m=force - expected,
            axial_force_residual_n=axial - axial_ref,
            maximum_evaluated_von_mises_pa=max(v["von_mises_Pa"] for v in values),
            record_digest=section["record_digest"],
        )

    x = run("pure_shear", "mechanics.plane_stress", dict(stress_x_Pa=0.0, stress_y_Pa=0.0, shear_xy_Pa=3e6))
    assert math.isclose(x["von_mises_Pa"], math.sqrt(3) * 3e6, rel_tol=1e-14)
    for fraction in (0.0, 1.0):
        x = run(
            "uniform_restraint_" + str(fraction),
            "mechanics.thermal_restraint",
            dict(
                young_modulus_Pa=2e11,
                expansion_coefficient_per_K=1e-5,
                temperature_change_K=20.0,
                restraint_fraction=fraction,
            ),
        )
        assert math.isclose(x["restrained_uniaxial_stress_Pa"], -4e7 * fraction, abs_tol=1e-7)
    x = run(
        "planar_layers",
        "thermal.planar_layers",
        dict(
            thickness_m=[0.01, 0.02],
            conductivity_W_m_K=[10.0, 20.0],
            area_m2=2.0,
            temperature_difference_K=100.0,
        ),
    )
    assert math.isclose(x["heat_rate_W"], 1e5, rel_tol=1e-14)
    lumped = dict(
        initial_temperature_K=340.0,
        ambient_temperature_K=300.0,
        heat_capacity_J_K=1000.0,
        surface_area_m2=1.0,
        convection_W_m2_K=10.0,
        solid_conductivity_W_m_K=10.0,
        characteristic_length_m=0.01,
        maximum_declared_biot=0.1,
        exposure_s=100.0,
    )
    x = run("lumped_tau", "thermal.lumped_temperature", lumped)
    assert math.isclose(x["temperature_K"], 300 + 40 / math.e, rel_tol=1e-14)
    run("lumped_biot_outside", "thermal.lumped_temperature", lumped | {"characteristic_length_m": 1.0})
    assert receipts["lumped_biot_outside"].native.status == "out_of_scope"

    stress = thermal_stress_case()
    x = run("plane_free_uniform", "mechanics.through_wall_thermal_stress", stress)
    assert max(abs(v) for v in x["stress_Pa"]) < 1e-6
    clamped = deepcopy(stress)
    for key in ("membrane_force_N_m", "bending_moment_N"):
        clamped.pop(key)
    clamped.update(control="kinematic_controlled", midplane_total_strain=0.0, curvature_1_m=0.0)
    x = run("plane_clamped_uniform", "mechanics.through_wall_thermal_stress", clamped)
    assert max(abs(v + 4e7) for v in x["stress_Pa"]) < 1e-6
    run(
        "plane_review_missing",
        "mechanics.through_wall_thermal_stress",
        stress | {"plane_sections_reviewed": False},
    )
    assert receipts["plane_review_missing"].native.status == "needs_data"
    run(
        "plane_elastic_limit",
        "mechanics.through_wall_thermal_stress",
        clamped | {"temperature_K": [500.0] * 5},
    )
    assert receipts["plane_elastic_limit"].native.status == "out_of_scope"

    uniform = heat_case()
    x = run("solid_adiabatic", "thermal.transient_conduction_1d", uniform)
    assert max(abs(t - 300.0) for row in x["temperature_history_K"] for t in row) < 1e-10
    summary["zero_heat_adiabatic_issue"] = dict(
        native_status=receipts["solid_adiabatic"].native.status,
        absolute_energy_residual_j=x["maximum_absolute_discrete_energy_balance_error_J"],
        relative_energy_residual=x["maximum_relative_discrete_energy_balance_error"],
        status_overridden=False,
        owner_extension_request="PI-X04",
        interpretation="Near-zero heat transfer needs a reviewed absolute-plus-relative energy criterion; preserve native status.",
    )
    heat = deepcopy(uniform)
    heat["left_boundary"] = dict(
        boundary_id="heat", kind="prescribed_heat_flux", heat_flux_into_solid_W_m2=[100.0] * 3
    )
    x = run("solid_flux_energy", "thermal.transient_conduction_1d", heat)
    stored = 2e6 * 0.02 * (sum(x["temperature_history_K"][-1]) / 10 - 300.0)
    assert abs(stored - 200.0) < 1e-5
    run(
        "solid_property_outside",
        "thermal.transient_conduction_1d",
        uniform | {"initial_temperature_K": [500.0] * 10},
    )
    assert receipts["solid_property_outside"].native.status == "out_of_scope"
    run(
        "solid_boundary_unreviewed",
        "thermal.transient_conduction_1d",
        uniform | {"boundary_conditions_reviewed": False},
    )
    assert receipts["solid_boundary_unreviewed"].native.status == "needs_data"
    radial_errors = []
    radial_average_errors = []
    for count in (10, 20):
        radial = heat_case(count=count, radial=True)
        radial.update(time_s=[0.0, 100000.0], maximum_step_s=1000.0)
        radial["left_boundary"] = dict(
            boundary_id="inner", kind="prescribed_temperature", temperature_K=[340.0] * 2
        )
        radial["right_boundary"] = dict(
            boundary_id="outer", kind="prescribed_temperature", temperature_K=[300.0] * 2
        )
        x = run("solid_radial_" + str(count), "thermal.transient_conduction_1d", radial)
        exact = [300 + 40 * math.log(0.07 / r) / math.log(0.07 / 0.05) for r in x["cell_centers_m"]]
        radial_errors.append(
            max(abs(a - b) for a, b in zip(x["temperature_history_K"][-1], exact, strict=True))
        )
        edges = radial["cell_edges_m"]
        averages = [
            quad(lambda radius: radius * (300 + 40 * math.log(0.07 / radius) / math.log(1.4)), a, b)[0]
            / (0.5 * (b * b - a * a))
            for a, b in zip(edges, edges[1:], strict=False)
        ]
        radial_average_errors.append(
            max(abs(a - b) for a, b in zip(x["temperature_history_K"][-1], averages, strict=True))
        )
    assert radial_average_errors[1] < radial_average_errors[0] / 3
    summary["radial_steady_center_diagnostic_max_error_k"] = radial_errors
    summary["radial_steady_volume_average_max_error_k"] = radial_average_errors

    archive = args.raw_root / "sbcf36976g-1.zip"
    with zipfile.ZipFile(archive) as source:
        members = sorted(n for n in source.namelist() if "/Raw_Data/" in n and n.endswith(".csv"))
        run_summaries = []
        for member in members:
            run_id = Path(member).stem
            frames, metadata = thermal_rod_frames(archive, run_id)
            raw = list(csv.reader(io.StringIO(source.read(member).decode("utf-8-sig"))))
            assert len(raw) - 1 == len(frames)
            for frame, row in zip(frames, raw[1:], strict=True):
                assert [frame.time_s, *frame.values.values()] == [float(v) for v in row]
            run_summaries.append(
                dict(
                    run_id=run_id,
                    rows=len(frames),
                    time_start_s=frames[0].time_s,
                    time_end_s=frames[-1].time_s,
                    negative_excess_values=sum(
                        v < 0 for f in frames for k, v in f.values.items() if "excess" in k
                    ),
                    clock_id=metadata["clock_id"],
                    source_member_sha256=metadata["source"]["source_member_sha256"],
                )
            )
            (private / (run_id + "_mapping.json")).write_text(
                json.dumps(metadata, indent=2) + "\n", encoding="utf-8"
            )
    frames, mapping = thermal_rod_frames(archive, "50cu3")
    sample = tuple(frames[i] for i in range(0, len(frames), 100))
    if sample[-1] != frames[-1]:
        sample = (*sample, frames[-1])
    conditioned = heat_case(count=10)
    conditioned["cell_edges_m"] = [0.02 * i for i in range(11)]
    conditioned["time_s"] = [f.time_s for f in sample]
    conditioned["maximum_step_s"] = 1.0
    conditioned["left_boundary"] = dict(
        boundary_id="assumed_sensor_face",
        kind="prescribed_temperature",
        temperature_K=[293.15 + f.values["tc1_excess_temperature_K"] for f in sample],
    )
    conditioned["initial_temperature_K"] = [293.15] * 10
    conditioned["right_boundary"] = dict(boundary_id="assumed_far_end", kind="adiabatic")
    conditioned["distributed_exchange"] = dict(
        area_per_volume_m_inv=[333.3333333333] * 10,
        convection_W_m2_K=[10.0] * len(sample),
        ambient_temperature_K=[293.15] * len(sample),
    )
    conditioned["materials"][0].update(
        conductivity_W_m_K=[385.0, 385.0], volumetric_heat_capacity_J_m3_K=[3.4e6, 3.4e6]
    )
    conditional_context = ctx.model_copy(
        update={
            "scenario_id": "declared-rod-boundary-hypothesis",
            "evidence": ctx.evidence
            + (
                EvidenceRef(
                    id="rod-source",
                    citation="Tomanek and Stutts, 2021, DOI 10.17632/sbcf36976g.1",
                    origin="laboratory",
                    sha256=file_digest(archive),
                    license_id="CC-BY-4.0",
                ),
            ),
        }
    )
    receipt = evaluate_material_job(
        prepare_material_job(
            "thermal.transient_conduction_1d",
            conditioned,
            conditional_context,
            location_id="hypothetical_rod_axis",
            evidence_ids=("authored", "rod-source"),
            client=client,
        ),
        conditional_context,
        client=client,
    )
    assert receipt.native.status == "computed"
    receipts["rod_boundary_hypothesis"] = receipt
    simulated = tuple(solid_thermal_frames(receipt))
    batch = monitoring_batch(frames)
    series = hra_signal_series(frames, "tc1_excess_temperature_K")
    assert batch.metadata["clock_id"] == series.clock_id == frames[0].clock_id
    summary["rod_data"] = dict(
        source="https://doi.org/10.17632/sbcf36976g.1",
        license_id="CC-BY-4.0",
        creators=["Lauren B. Tomanek", "Daniel S. Stutts"],
        archive_sha256=file_digest(archive),
        raw_runs=len(run_summaries),
        total_rows=sum(r["rows"] for r in run_summaries),
        negative_excess_values=sum(r["negative_excess_values"] for r in run_summaries),
        full_native_reader_values_equal_original_csv=True,
        monitoring_rows=len(frames),
        hra_samples=len(series.time_s),
        native_thermal_output_rows=len(simulated),
        source_status="needs_data",
        conditioned_input_points=len(sample),
        ambient_geometry_properties_sensor_face_and_transfer_are_hypotheses=True,
        controller_counts_converted_to_heat=False,
        independent_prediction_accuracy_scored=False,
        excess_temperature_reference_needs_receiver_metadata=True,
    )
    (private / "rod_source_review.json").write_text(
        json.dumps(run_summaries, indent=2) + "\n", encoding="utf-8"
    )
    for name, receipt in receipts.items():
        (private / (name + ".json")).write_text(receipt.model_dump_json(indent=2) + "\n", encoding="utf-8")
    assert before == {key: revision(path) for key, path in roots.items()}
    summary["source_versions"] = before
    summary["module_sha256"] = file_digest(Path("src/pipeline_integrity/material_exchange.py"))
    summary["example_sha256"] = file_digest(Path("examples/material_sections.py"))
    summary["script_sha256"] = file_digest(Path(__file__))
    summary["field_or_design_qualification"] = False
    Path("research/material_sections_results.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
