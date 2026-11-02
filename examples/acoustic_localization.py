"""Example entry point for a caller-supplied acoustic localization job."""

import argparse
import json
from pathlib import Path

from pipeline_integrity.acoustics import (
    DelayRequest,
    PipeSpan,
    SampledTrace,
    localize_acoustic_leak,
)
from pipeline_integrity.contracts import RunContext


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("job", type=Path, help="JSON with context, two traces, delay request and span")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    payload = json.loads(args.job.read_text(encoding="utf-8"))
    if set(payload) != {"context", "left", "right", "specification", "span"}:
        raise ValueError("job must contain exactly context, left, right, specification and span")
    result = localize_acoustic_leak(
        SampledTrace.model_validate(payload["left"]),
        SampledTrace.model_validate(payload["right"]),
        DelayRequest.model_validate(payload["specification"]),
        PipeSpan.model_validate(payload["span"]) if payload["span"] is not None else None,
        RunContext.model_validate(payload["context"]),
    )
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")


if __name__ == "__main__":
    main()
