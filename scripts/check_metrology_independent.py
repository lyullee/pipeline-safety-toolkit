"""Read-only independent checks of the current measurement budget implementation."""

import copy
import hashlib
import json
import math
import runpy
from pathlib import Path

from pipeline_integrity.contracts import digest
from pipeline_integrity.metrology import (
    StandardCovariance,
    propagate_measurement_uncertainty,
    verify_measurement_uncertainty,
)

ROOT = Path(__file__).resolve().parents[1]
helpers = runpy.run_path(str(ROOT / "tests/test_metrology.py"))
request, context = helpers["request"], helpers["context"]
r, c = request(), context()
checks = {}
source = ROOT / "src/pipeline_integrity/metrology.py"
before = hashlib.sha256(source.read_bytes()).hexdigest()
for name, bad_request, bad_context in (
    (
        "nan_covariance",
        r.model_copy(
            update={
                "covariance": r.covariance.model_copy(update={"values": ((float("nan"), 0.01), (0.01, 0.09))})
            }
        ),
        c,
    ),
    ("inf_metadata", r, c.model_copy(update={"metadata": {"invalid": float("inf")}})),
):
    try:
        value = propagate_measurement_uncertainty(bad_request, bad_context)
        checks[name] = {"rejected": False, "status": value["status"]}
    except ValueError:
        checks[name] = {"rejected": True}
r = request(jacobian=((0.11432784272625618, 0.1917743575935109), (0.1953941544050074, 0.47713398189152945)))
record = propagate_measurement_uncertainty(r, c)
matrix = record["output_covariance"]
StandardCovariance(
    input_ids=("out0", "out1"), values=tuple(tuple(v for v in row) for row in matrix), evidence_id="authored"
)
checks["symmetry_and_chaining"] = {"passed": matrix[0][1] == matrix[1][0], "covariance": matrix}
forged = copy.deepcopy(record)
forged["outputs"]["result_0"]["standard_uncertainty"] *= 1.1
forged["record_digest"] = digest({key: value for key, value in forged.items() if key != "record_digest"})
try:
    verify_measurement_uncertainty(forged)
    checks["rehashed_uncertainty"] = {"rejected": False}
except ValueError:
    checks["rehashed_uncertainty"] = {"rejected": True}
r = request(matrix=((1e-320, 0.0), (0.0, 0.0)), jacobian=((1e-10, 0.0),))
record = propagate_measurement_uncertainty(r, c)
checks["subnormal_range"] = {
    "status": record["status"],
    "output_covariance": record["output_covariance"],
    "reported_standard_uncertainty": record["outputs"].get("result_0", {}).get("standard_uncertainty"),
    "expected_nonzero_standard_uncertainty": 1e-10 * math.sqrt(1e-320),
}
after = hashlib.sha256(source.read_bytes()).hexdigest()
result = {"source_sha256": before, "source_unchanged": before == after, "checks": checks}
(ROOT / "artifacts/verification/metrology_external_review.json").write_text(
    json.dumps(result, indent=2), encoding="utf-8"
)
result["review_script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
result["all_passed"] = all(
    (
        checks["nan_covariance"]["rejected"],
        checks["inf_metadata"]["rejected"],
        checks["symmetry_and_chaining"]["passed"],
        checks["rehashed_uncertainty"]["rejected"],
        checks["subnormal_range"]["status"] == "out_of_scope",
        result["source_unchanged"],
    )
)
(ROOT / "research/metrology_independent_review.json").write_text(
    json.dumps(result, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps({"all_passed": result["all_passed"], "source_sha256": before}))
if not result["all_passed"]:
    raise AssertionError("independent metrology review failed")
