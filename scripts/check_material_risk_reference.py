"""Exercise two owner primitives without creating duplicate production risk gates."""

import argparse
import json
import math
import runpy
import sys
from pathlib import Path

from check_integrations import revision

from pipeline_integrity.datasets import file_digest
from pipeline_integrity.material_exchange import evaluate_material_job, prepare_material_job
from pipeline_integrity.material_source import material_source_guard
from pipeline_integrity.risk_exchange import FaultNode, HRARiskClient


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--materials", required=True, type=Path)
    parser.add_argument("--hra", required=True, type=Path)
    args = parser.parse_args()
    before = {name: revision(path) for name, path in (("materials", args.materials), ("hra", args.hra))}
    sys.path[:0] = [str((p / "src").resolve()) for p in (args.materials, args.hra)]
    import human_reliability

    from pipeline_integrity.adapters import MaterialClient

    assert Path(human_reliability.__file__).resolve().is_relative_to((args.hra / "src").resolve())
    material, risk = MaterialClient(), HRARiskClient()
    parameters, gates, rate, tree, context = runpy.run_path("examples/material_risk_reference.py")[
        "make_cases"
    ]()
    records, receivers, references, refusals = {}, {}, {}, []
    support_files = [
        args.materials / "LICENSE",
        args.materials / "THIRD_PARTY_NOTICES.md",
        *sorted((args.materials / "src").rglob("*.json")),
    ]
    support_before = {p.relative_to(args.materials).as_posix(): file_digest(p) for p in support_files}

    def native(name, model, p):
        job = prepare_material_job(
            model, p, context, location_id="authored_reference", evidence_ids=("authored",), client=material
        )
        receipt = evaluate_material_job(job, context, client=material)
        receipt.check_integrity()
        records[name] = receipt.model_dump(mode="json")
        return receipt.native.payload

    def reference(name, actual, expected):
        assert math.isclose(actual, expected, rel_tol=1e-12, abs_tol=0), (name, actual, expected)
        references[name] = {"actual": actual, "expected": expected, "absolute_error": abs(actual - expected)}

    def refused(name, model, p):
        try:
            native(name, model, p)
        except ValueError:
            refusals.append(name)
        else:
            raise AssertionError("invalid owner reference input accepted: " + name)

    with material_source_guard(
        args.materials, before["materials"]["source_tree_sha256"], client=material
    ) as source_pin:
        for gate, expected in (("AND", 0.02), ("OR", 0.28)):
            output = native(gate, "reliability.independent_gate", parameters[gate])
            assert output["status"] == "computed" and output["probability_semantics"] == {
                "probability": "unscoped_event_probability"
            }
            receiver = risk.fault_tree(gates[gate], context)
            assert receiver["status"] == "exchange_checked"
            receivers[gate] = receiver
            reference("material_" + gate, output["values"]["probability"], expected)
            reference("scoped_hra_" + gate, receiver["probability"]["probability"], expected)
        unknown = native(
            "undeclared_independence",
            "reliability.independent_gate",
            parameters["OR"] | {"independence_declared": False},
        )
        assert unknown["status"] == "needs_data" and unknown["values"] == {}
        receiver_unknown = risk.fault_tree(
            gates["OR"].model_copy(update={"dependence": "joint_states"}), context
        )
        assert receiver_unknown["status"] == "withheld" and receiver_unknown["probability"] is None
        receivers["missing_joint_model"] = receiver_unknown
        shared = gates["OR"].model_copy(
            update={
                "root": FaultNode(
                    kind="OR",
                    children=(FaultNode(kind="event", event_id="A"), FaultNode(kind="event", event_id="A")),
                ),
                "events": (gates["OR"].events[0],),
            }
        )
        shared_result = risk.fault_tree(shared, context)
        receivers["shared_atomic_identity"] = shared_result
        reference("shared_identity_hra", shared_result["probability"]["probability"], 0.1)
        independent_duplicates = native(
            "two_independent_copies",
            "reliability.independent_gate",
            parameters["OR"] | {"probabilities": [0.1, 0.1]},
        )
        reference(
            "distinct_independent_inputs_material", independent_duplicates["values"]["probability"], 0.19
        )
        assert shared_result["probability"]["probability"] != independent_duplicates["values"]["probability"]
        for name, p, gate, expected in (
            ("zero_or", [0.0, 0.0], "OR", 0.0),
            ("unit_or", [0.0, 1.0], "OR", 1.0),
            ("zero_and", [0.0, 1.0], "AND", 0.0),
            ("unit_and", [1.0, 1.0], "AND", 1.0),
        ):
            reference(
                name,
                native(
                    name,
                    "reliability.independent_gate",
                    {"probabilities": p, "gate": gate, "independence_declared": True},
                )["values"]["probability"],
                expected,
            )
        paths = native("paths", "reliability.exclusive_paths", parameters["paths"])
        assert paths["status"] == "computed"
        event_tree = risk.event_tree(rate, tree)
        assert event_tree["status"] == "exchange_checked"
        receivers["paths"] = event_tree
        seconds = context.metadata["seconds_per_reference_year"]
        for i, expected in enumerate((2.4e-8, 1.2e-8, 4e-9)):
            reference(f"material_path_{i}_per_s", paths["values"]["path_frequencies_per_s"][i], expected)
            reference(
                f"scoped_hra_path_{i}_per_s",
                event_tree["frequencies"][i]["annual_frequency_per_year"] / seconds,
                expected,
            )
        zero_rate = native(
            "zero_rate",
            "reliability.exclusive_paths",
            parameters["paths"] | {"initiating_frequency_per_s": 0.0},
        )
        assert zero_rate["values"]["path_frequencies_per_s"] == [0.0] * 3
        refused(
            "missing_probability",
            "reliability.independent_gate",
            parameters["OR"] | {"probabilities": [0.1, None]},
        )
        refused(
            "non_boolean_independence",
            "reliability.independent_gate",
            parameters["OR"] | {"independence_declared": 1},
        )
        refused(
            "incomplete_partition",
            "reliability.exclusive_paths",
            parameters["paths"] | {"conditional_probabilities": [0.6, 0.3]},
        )
        refused(
            "negative_frequency",
            "reliability.exclusive_paths",
            parameters["paths"] | {"initiating_frequency_per_s": -1.0},
        )
    after = {name: revision(path) for name, path in (("materials", args.materials), ("hra", args.hra))}
    support_after = {p.relative_to(args.materials).as_posix(): file_digest(p) for p in support_files}
    assert before == after and support_before == support_after
    folder = Path("artifacts/material_risk_reference")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "material_receipts.json").write_text(
        json.dumps(records, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (folder / "scoped_hra_results.json").write_text(
        json.dumps(receivers, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    (folder / "independent_references.json").write_text(
        json.dumps(references, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    summary = {
        "schema": "pipeline_integrity.material_risk_reference_verification.v1",
        "status": "passed",
        "native_models": {
            model: {
                "model_version": next(
                    r["job"]["model_version"] for r in records.values() if r["job"]["model_id"] == model
                )
            }
            for model in ("reliability.independent_gate", "reliability.exclusive_paths")
        },
        "native_case_count": len(records),
        "scoped_hra_case_count": len(receivers),
        "independent_reference_count": len(references),
        "maximum_reference_absolute_error": max(r["absolute_error"] for r in references.values()),
        "refusal_count": len(refusals),
        "refusals": refusals,
        "material_source_pin": source_pin,
        "production_risk_owner": "human-reliability-toolkit",
        "additional_production_gate_implementation": False,
        "unscoped_material_probability_transferred_to_scoped_risk": False,
        "shared_event_is_two_independent_events": False,
        "unknown_independence_and_incomplete_partition_withheld": True,
        "seconds_per_declared_reference_year": context.metadata["seconds_per_reference_year"],
        "observed_event_frequency_or_field_validation": False,
        "limitations": [
            "Numerical equality compares independently authored inputs with explicitly chosen conditions; it does not add missing scope to a material primitive.",
            "Repeated atomic identity and two independent events are different cases; their OR probabilities deliberately differ.",
            "The complete exclusive path partition and initiating rate are authored assumptions, not observed pipeline or human-error frequencies.",
        ],
        "external_source_before": before,
        "external_source_after": after,
        "external_source_unchanged": True,
        "external_support_files_before": support_before,
        "external_support_files_after": support_after,
        "material_job_implementation_sha256": file_digest(
            Path("src/pipeline_integrity/material_exchange.py")
        ),
        "risk_implementation_sha256": file_digest(Path("src/pipeline_integrity/risk_exchange.py")),
        "source_guard_sha256": file_digest(Path("src/pipeline_integrity/material_source.py")),
        "script_sha256": file_digest(Path(__file__)),
        "example_sha256": file_digest(Path("examples/material_risk_reference.py")),
    }
    Path("research/material_risk_reference_results.json").write_text(
        json.dumps(summary, indent=2, allow_nan=False) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "passed",
                "models": 2,
                "native_cases": len(records),
                "scoped_hra_cases": len(receivers),
                "references": len(references),
                "refusals": len(refusals),
            }
        )
    )


if __name__ == "__main__":
    main()
