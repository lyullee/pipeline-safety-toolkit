"""Publish aggregate test outcomes from the actual JUnit artifact."""

import json
import xml.etree.ElementTree as ET
from pathlib import Path


def main():
    root = ET.parse("artifacts/verification/tests.xml").getroot()
    cases = root.findall(".//testcase")
    results = []
    for case in cases:
        state = (
            "failed"
            if case.find("failure") is not None
            else "error"
            if case.find("error") is not None
            else ("skipped" if case.find("skipped") is not None else "passed")
        )
        results.append({"module": case.get("classname"), "test": case.get("name"), "outcome": state})
    payload = {
        "schema": "pipeline_integrity.verification.v1",
        "tests": len(results),
        "passed": sum(r["outcome"] == "passed" for r in results),
        "failures": sum(r["outcome"] in {"failed", "error"} for r in results),
        "skipped": sum(r["outcome"] == "skipped" for r in results),
        "cases": results,
        "field_validation_claim": False,
    }
    Path("research/verification_results.json").write_text(
        json.dumps(payload, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({key: payload[key] for key in ("tests", "passed", "failures", "skipped")}))
    raise SystemExit(0 if payload["tests"] and not payload["failures"] else 1)


if __name__ == "__main__":
    main()
