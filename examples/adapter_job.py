"""Write an authored JSON job; computation requires the explicit --execute option."""

import argparse
import json
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

from pipeline_integrity.adapter_jobs import AdapterJob, run_adapter_job, verify_adapter_job_record


def make_job(*, reviewed=False):
    location = Path(__file__).with_name("route_planning.py")
    spec = spec_from_file_location("authored_routes_for_adapter_job", location)
    example = module_from_spec(spec)
    spec.loader.exec_module(example)
    request, context = example.make_case(reviewed=reviewed)
    return AdapterJob(
        id="authored_route_job",
        adapter_id="construction.route",
        parameters=request.model_dump(mode="json"),
        context=context,
    )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--reviewed", action="store_true")
    args = parser.parse_args()
    job = make_job(reviewed=args.reviewed)
    result = job.model_dump(mode="json")
    if args.execute:
        result = run_adapter_job(job)
        verify_adapter_job_record(result, job=job)
    text = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
