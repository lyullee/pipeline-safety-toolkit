"""Exercise every registered model with declared demonstration parameters."""

import json
from pathlib import Path

from pipeline_integrity import create_registry
from pipeline_integrity.contracts import RunContext


def main():
    data = json.loads(Path("examples/engineering_cases.json").read_text(encoding="utf-8"))
    engine = create_registry()
    if set(data["cases"]) != {row.id for row in engine.models()}:
        raise ValueError("provide a demonstration case for every registered calculation")
    context = RunContext.model_validate_json(json.dumps(data["context"]))
    results = []
    for model_id, parameters in data["cases"].items():
        result = engine.evaluate(model_id, json.dumps(parameters), context, json_input=True)
        assert result.status == "computed", (model_id, result.notes)
        results.append(result.model_dump(mode="json"))
    target = Path("research/example_results.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(
            {"schema": "pipeline_integrity.examples.v1", "results": results, "field_validation_claim": False},
            indent=2,
            allow_nan=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"computed_examples": len(results), "output": str(target)}))


if __name__ == "__main__":
    main()
