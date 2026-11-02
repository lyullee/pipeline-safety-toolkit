"""Compare exact sensor-member content across two Mendeley leak-testbed deposits."""

import argparse
import json
import shutil
from pathlib import Path

from pipeline_integrity.leak_deposit_crosswalk import compare_leak_testbed_deposits


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout-s", type=float, default=3600.0)
    args = parser.parse_args()
    root = args.raw_root.resolve(strict=True)
    tar = shutil.which("tar")
    if tar is None:
        parser.error("a reviewed tar/bsdtar executable is required to read the pinned 7z")
    record = compare_leak_testbed_deposits(
        root / "leak_multisensor_transients"
        / "Dataset of Leak Simulations in Experimental Testbed Water Network.7z",
        root / "leak_testbed" / "leak_testbed-v1.zip",
        tar_executable=Path(tar),
        timeout_s=args.timeout_s,
    )
    target = args.output
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write("\n")
    temporary.replace(target)
    print(
        json.dumps(
            {
                "output": str(target),
                "identity_classification": record["identity_classification"],
                "member_comparison": record["member_comparison"],
                "record_digest": record["record_digest"],
                "raw_signal_values_included": record["raw_signal_values_included"],
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
