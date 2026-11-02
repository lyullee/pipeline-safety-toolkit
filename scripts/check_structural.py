"""Execute native static references, observed-load hypotheses and real signal-owner exchanges."""

import argparse
import json
import runpy
import sys
from pathlib import Path

import numpy as np
from check_integrations import revision
from scipy.integrate import quad

from pipeline_integrity.adapters import hra_signal_series, monitoring_batch
from pipeline_integrity.contracts import EvidenceRef, digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.fracture_exchange import crack_arrest_frames
from pipeline_integrity.structural import (
    FrameCombination,
    FrameLineLoad,
    FrameSignalLoad,
    FrameSupport,
    frame_signal_frame,
    solve_frame,
    solve_frame_signal_snapshot,
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", type=Path, required=True)
    parser.add_argument("--monitoring", type=Path, required=True)
    parser.add_argument("--hra", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, default=Path("data/raw/crack_arrest"))
    args = parser.parse_args()
    roots = {name: getattr(args, name) for name in ("materials", "monitoring", "hra")}
    before = {key: revision(path) for key, path in roots.items()}
    sys.path[:0] = [str((path / "src").resolve()) for path in roots.values()]
    example = runpy.run_path("examples/pipe_frame.py")
    request, context = example["make_case"](), example["context"]()
    private = Path("artifacts/structural")
    private.mkdir(parents=True, exist_ok=True)
    cases = {}

    def run(name, case=request):
        result = solve_frame(case, context)
        assert result["record_digest"] == digest({k: v for k, v in result.items() if k != "record_digest"})
        (private / (name + ".json")).write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
        cases[name] = dict(
            status=result["status"],
            issues=result["issues"],
            record_digest=result["record_digest"],
            combinations=result["combinations"],
        )
        return result

    reference = run("cantilever_point")
    assert reference["status"] == "computed"
    e, s = request.materials[0].young_modulus_pa, request.sections[0]
    ei, length = e * s.iz_m4, 4.0
    numerical_errors = {}

    def close(name, actual, expected):
        error = abs(actual - expected)
        assert np.isclose(actual, expected, rtol=1e-10, atol=1e-12), (name, actual, expected)
        numerical_errors[name] = dict(actual=actual, independent_reference=expected, absolute_error=error)

    close(
        "point_tip_dy_m",
        reference["combinations"]["service"]["nodes"]["tip"]["translation_m"][1],
        -1000 * length**3 / (3 * ei),
    )
    for direction in ("FX", "FZ", "MX", "MZ"):
        load = request.node_loads[0].model_copy(update={"direction": direction})
        result = run("cantilever_" + direction, request.model_copy(update={"node_loads": (load,)}))
        assert result["status"] == "computed"
    close(
        "axial_tip_dx_m",
        cases["cantilever_FX"]["combinations"]["service"]["nodes"]["tip"]["translation_m"][0],
        -1000 * length / (e * s.area_m2),
    )
    g = e / (2 * (1 + request.materials[0].poisson_ratio))
    close(
        "torsion_tip_rx_rad",
        cases["cantilever_MX"]["combinations"]["service"]["nodes"]["tip"]["rotation_rad"][0],
        -1000 * length / (g * s.torsion_constant_m4),
    )
    line = FrameLineLoad(
        member_id="pipe",
        global_direction="FY",
        start_n_m=-100.0,
        end_n_m=-100.0,
        case_id="point",
        evidence_id="reference",
    )
    uniform = run("uniform", request.model_copy(update={"node_loads": (), "line_loads": (line,)}))
    close(
        "uniform_tip_dy_m",
        uniform["combinations"]["service"]["nodes"]["tip"]["translation_m"][1],
        -100 * length**4 / (8 * ei),
    )
    triangle = line.model_copy(
        update={"start_n_m": 0.0, "end_n_m": -300.0, "start_fraction": 0.2, "end_fraction": 0.8}
    )
    partial = run(
        "partial_triangle", request.model_copy(update={"node_loads": (), "line_loads": (triangle,)})
    )
    expected = quad(lambda x: -300 * (x - 0.8) / 2.4 * x**2 * (12 - x) / (6 * ei), 0.8, 3.2)[0]
    close(
        "partial_triangle_tip_dy_m",
        partial["combinations"]["service"]["nodes"]["tip"]["translation_m"][1],
        expected,
    )
    simple_supports = (
        FrameSupport(node_id="anchor", fixed_dofs=("DX", "DY", "DZ", "RX"), evidence_id="reference"),
        FrameSupport(node_id="tip", fixed_dofs=("DY", "DZ"), evidence_id="reference"),
    )
    simple = run(
        "simply_supported",
        request.model_copy(update={"node_loads": (), "line_loads": (line,), "supports": simple_supports}),
    )
    close(
        "simply_supported_mid_dy_m",
        simple["combinations"]["service"]["members"]["pipe"]["samples"][2]["local_translation_m"][1],
        -5 * 100 * length**4 / (384 * ei),
    )
    spring = FrameSupport(node_id="tip", translation_springs_n_m={"DY": 100000.0}, evidence_id="reference")
    sprung = run("tip_spring", request.model_copy(update={"supports": request.supports + (spring,)}))
    close(
        "sprung_tip_dy_m",
        sprung["combinations"]["service"]["nodes"]["tip"]["translation_m"][1],
        -1000 / (3 * ei / length**3 + 100000),
    )
    movement = FrameSupport(
        node_id="tip", fixed_dofs=("DY",), imposed_translations_m={"DY": -0.001}, evidence_id="reference"
    )
    settlement = run(
        "imposed_movement",
        request.model_copy(
            update={
                "node_loads": (),
                "supports": request.supports + (movement,),
                "combinations": (
                    FrameCombination(id="service", factors={"point": 0.0}, evidence_id="reference"),
                ),
            }
        ),
    )
    close(
        "imposed_tip_dy_m", settlement["combinations"]["service"]["nodes"]["tip"]["translation_m"][1], -0.001
    )
    assert (
        run("unreviewed", request.model_copy(update={"supports_reviewed": False}))["status"] == "needs_data"
    )
    unknown = request.node_loads[0].model_copy(update={"value": None})
    assert (
        run("missing_load", request.model_copy(update={"node_loads": (unknown,)}))["status"] == "needs_data"
    )
    assert run("unstable", request.model_copy(update={"supports": ()}))["status"] == "out_of_scope"
    large = request.node_loads[0].model_copy(update={"value": -1e8})
    assert (
        run("large_response", request.model_copy(update={"node_loads": (large,)}))["status"] == "out_of_scope"
    )
    assert not any(
        frame_signal_frame(
            solve_frame(request.model_copy(update={"node_loads": (large,)}), context), "service", context
        ).valid.values()
    )

    from matintegrity.fracture_data import list_crack_arrest_runs

    archive = args.raw_root / "g69875w65c-1.zip"
    runs = list_crack_arrest_runs(archive)
    specimen = str(runs.iloc[0].specimen_id)
    observed, metadata = crack_arrest_frames(archive, specimen)
    evidence = EvidenceRef(
        id="machine-force",
        citation="Puyi Gao (2023), Mendeley Data V1, DOI 10.17632/g69875w65c.1",
        locator="https://data.mendeley.com/datasets/g69875w65c/1",
        sha256=file_digest(archive),
        origin="laboratory",
        license_id="CC-BY-4.0",
        limitations=(
            "Machine loading is applied only to an independently declared static axial frame hypothesis; original fixture geometry and dynamic validity are unresolved.",
        ),
    )
    source_context = context.model_copy(
        update={
            "clock_id": observed[0].clock_id,
            "origin": "laboratory",
            "evidence": context.evidence + (evidence,),
        }
    )
    axial = request.node_loads[0].model_copy(update={"direction": "FX", "value": None})
    template = request.model_copy(update={"clock_id": source_context.clock_id, "node_loads": (axial,)})
    mapping = FrameSignalLoad(
        source_stream_id=observed[0].stream_id,
        source_channel="force_N",
        source_evidence_id=evidence.id,
        node_load_index=0,
        quasistatic_snapshot_hypothesis_reviewed=True,
    )
    frames, errors = [], []
    selected = observed[:32]
    for index, frame in enumerate(selected):
        result = solve_frame_signal_snapshot(template, frame, mapping, source_context)
        assert result["status"] == "computed"
        assert result["request"]["time_s"] == frame.time_s
        assert result["request"]["node_loads"][0]["value"] == frame.values["force_N"]
        output = frame_signal_frame(result, "service", source_context)
        expected = frame.values["force_N"] * length / (e * s.area_m2)
        errors.append(abs(output.values["tip.dx"] - expected))
        frames.append(output)
        (private / f"source_snapshot_{index:03d}.json").write_text(
            json.dumps(result, indent=2) + "\n", encoding="utf-8"
        )
    assert max(errors) < 1e-12
    batch = monitoring_batch(frames)
    series = hra_signal_series(frames, "tip.dx")
    assert np.array_equal(batch.time_s, [f.time_s for f in selected])
    assert tuple(series.time_s) == tuple(f.time_s for f in selected)
    assert batch.origin == series.origin == "simulation"
    assert batch.metadata["clock_id"] == series.clock_id == source_context.clock_id
    assert len(batch.channels) == 24
    altered = selected[0].model_copy(update={"valid": {**selected[0].valid, "force_N": False}})
    withheld = solve_frame_signal_snapshot(template, altered, mapping, source_context)
    assert withheld["status"] == "needs_data" and not any(
        frame_signal_frame(withheld, "service", source_context).valid.values()
    )
    assert before == {key: revision(path) for key, path in roots.items()}
    summary = dict(
        schema="pipeline_integrity.structural_verification.v1",
        authored_cases=cases,
        independent_references=numerical_errors,
        backend=reference["backend"],
        primary_load_exchange=dict(
            source="https://data.mendeley.com/datasets/g69875w65c/1",
            creator="Puyi Gao",
            license_id="CC-BY-4.0",
            archive_sha256=file_digest(archive),
            specimen_id=specimen,
            source_rows=len(observed),
            contiguous_source_rows_used=len(selected),
            negative_source_force_samples=sum(f.values["force_N"] < 0 for f in selected),
            source_clock_id=source_context.clock_id,
            selected_source_rows_preserved=True,
            maximum_axial_reference_error_m=max(errors),
            monitoring_rows=len(frames),
            monitoring_channels=len(batch.channels),
            hra_samples=len(series.time_s),
            source_origin="laboratory",
            predicted_origin="simulation",
            invalid_source_load_status=withheld["status"],
            actual_fixture_geometry_or_quasistatic_validity_qualified=False,
            structural_experiment_validated=False,
            source_header_representation_normalized=metadata["native_header_representation_normalized"],
        ),
        source_versions=before,
        module_sha256=file_digest(Path("src/pipeline_integrity/structural.py")),
        example_sha256=file_digest(Path("examples/pipe_frame.py")),
        script_sha256=file_digest(Path(__file__)),
        code_or_field_acceptance=False,
        failure_probability_inferred=False,
    )
    Path("research/structural_results.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            dict(
                authored_cases=len(cases),
                independent_references=len(numerical_errors),
                source_snapshots=len(selected),
                native_version=summary["backend"]["version"],
                maximum_axial_reference_error_m=max(errors),
            )
        )
    )


if __name__ == "__main__":
    main()
