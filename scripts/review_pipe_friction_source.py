"""Read pinned source files or an explicitly supplied conditional study request.

No supplier MATLAB program is executed. This driver was authored but not run
during implementation; default invocation reads observations only.
"""

import argparse
import json
from pathlib import Path

from pipeline_integrity.contracts import RunContext
from pipeline_integrity.friction_data import read_friction_observations
from pipeline_integrity.friction_study import FrictionStudyRequest, evaluate_friction_study


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument(
        "--request",
        type=Path,
        help="typed study JSON: explicit operating_point_calibration or actual-time calibration; never inferred",
    )
    parser.add_argument("--context", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if (args.request is None) != (args.context is None):
        parser.error("--request and --context must be supplied together")
    if args.request is None:
        result = read_friction_observations(args.source_root)
    else:
        if args.request.stat().st_size > 2 * 1024 * 1024 or args.context.stat().st_size > 1024 * 1024:
            parser.error("request/context JSON exceeds the explicit input byte budget")
        q = FrictionStudyRequest.model_validate_json(args.request.read_text(encoding="utf-8-sig"))
        c = RunContext.model_validate_json(args.context.read_text(encoding="utf-8-sig"))
        result = evaluate_friction_study(q, c, source_root=args.source_root)
    text = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output is None:
        print(text)
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
