"""Build a fresh mandatory-dependency environment and validate an installed wheel."""

import argparse
import os
import subprocess
import venv
from datetime import datetime, timezone
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wheel", type=Path)
    parser.add_argument("--output", type=Path, default=Path("research/core_installation.json"))
    args = parser.parse_args()
    wheels = [args.wheel] if args.wheel else list(Path("dist").glob("*.whl"))
    if len(wheels) != 1 or not wheels[0].is_file():
        raise ValueError("Select exactly one built wheel")
    wheel = wheels[0].resolve()
    directory = Path("artifacts/isolated-core") / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if directory.exists():
        raise FileExistsError("A fresh isolated environment is required")
    venv.EnvBuilder(with_pip=True).create(directory)
    python = directory / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    subprocess.run([str(python), "-m", "pip", "install", str(wheel)], check=True)
    env = {**os.environ, "PYTHONPATH": "", "PYTHONUTF8": "1"}
    subprocess.run(
        [
            str(python),
            "scripts/check_core_installation.py",
            "--wheel",
            str(wheel),
            "--output",
            str(args.output),
        ],
        check=True,
        env=env,
    )


if __name__ == "__main__":
    main()
