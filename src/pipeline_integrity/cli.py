"""JSON interfaces with no executable configuration or implicit data downloads."""

import argparse
import json
import sys
from pathlib import Path

from . import create_registry
from .contracts import RunContext
from .datasets import catalog
from .workflow import Workflow, run_workflow


def main(argv=None):
    parser = argparse.ArgumentParser(prog="pipeline-integrity")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("models")
    commands.add_parser("adapters")
    code_progress = commands.add_parser("code-progress")
    code_progress.add_argument("--scope", choices=("engineering_methods", "capabilities"),
                               default="engineering_methods")
    commands.add_parser("engineering-methods")
    method_describe = commands.add_parser("engineering-method-describe")
    method_describe.add_argument("method_id")
    adapter_describe = commands.add_parser("adapter-describe")
    adapter_describe.add_argument("adapter_id")
    describe = commands.add_parser("describe")
    describe.add_argument("model_id")
    commands.add_parser("datasets")
    commands.add_parser("capabilities")
    for name in (
        "evaluate",
        "adapter-run",
        "adapter-verify",
        "workflow",
        "liquid-transient",
        "water-quality",
        "water-fit",
        "acoustic-delay",
        "risk",
        "pressure-fatigue",
        "gas-network",
        "schedule",
    ):
        command = commands.add_parser(name)
        command.add_argument("input", type=Path)
        command.add_argument("--output", type=Path)
        if name in {"adapter-run", "adapter-verify"}:
            command.add_argument("--maximum-input-bytes", type=int, default=64_000_000)
    args = parser.parse_args(argv)
    try:
        engine = create_registry() if args.command in {"models", "describe", "evaluate", "workflow"} else None
        if args.command == "models":
            result = [m.model_dump(mode="json") for m in engine.models()]
        elif args.command == "adapters":
            from .adapter_jobs import adapter_specs

            result = adapter_specs()
        elif args.command == "code-progress":
            from . import code_implementation_progress

            result = code_implementation_progress(scope=args.scope)
        elif args.command == "engineering-methods":
            from dataclasses import asdict
            from . import engineering_method_specs

            result = [asdict(row) for row in engineering_method_specs()]
        elif args.command == "engineering-method-describe":
            from . import describe_engineering_method

            result = describe_engineering_method(args.method_id)
        elif args.command == "adapter-describe":
            from .adapter_jobs import describe_adapter

            result = describe_adapter(args.adapter_id)
        elif args.command == "describe":
            result = engine.describe(args.model_id)
        elif args.command == "datasets":
            result = [row.model_dump(mode="json") for row in catalog()]
        elif args.command == "capabilities":
            from .capabilities import capabilities

            result = [row.model_dump(mode="json") for row in capabilities()]
        else:
            if args.command in {"adapter-run", "adapter-verify"} and (
                not 0 < args.maximum_input_bytes <= 1_000_000_000
                or args.input.stat().st_size > args.maximum_input_bytes
            ):
                raise ValueError("adapter input exceeds its explicit byte budget")
            if args.command in {"adapter-run", "adapter-verify"}:
                if not args.input.is_file():
                    raise ValueError("adapter input must be an ordinary file")
                with args.input.open("rb") as stream:
                    raw = stream.read(args.maximum_input_bytes + 1)
                if len(raw) > args.maximum_input_bytes:
                    raise ValueError("adapter input exceeds its explicit byte budget")
                document = raw.decode("utf-8-sig")
            else:
                document = args.input.read_text(encoding="utf-8-sig")
            if args.command == "adapter-run":
                from .adapter_jobs import run_adapter_job

                result = run_adapter_job(document, maximum_job_bytes=args.maximum_input_bytes)
            elif args.command == "adapter-verify":
                from .adapter_jobs import decode_adapter_json, verify_adapter_job_record

                result = verify_adapter_job_record(
                    decode_adapter_json(document), maximum_job_bytes=args.maximum_input_bytes
                )
            elif args.command == "liquid-transient":
                from .transient import LiquidTransient, simulate_liquid_transient

                request = json.loads(document)
                if set(request) != {"specification", "context"}:
                    raise ValueError("transient JSON requires exactly specification and context")
                result = simulate_liquid_transient(
                    LiquidTransient.model_validate_json(json.dumps(request["specification"])),
                    RunContext.model_validate_json(json.dumps(request["context"])),
                )
            elif args.command == "schedule":
                from .scheduling import ScheduleRequest, solve_schedule

                request = json.loads(document)
                if set(request) != {"specification", "context"}:
                    raise ValueError("schedule JSON requires exactly specification and context")
                result = solve_schedule(
                    ScheduleRequest.model_validate_json(json.dumps(request["specification"])),
                    RunContext.model_validate_json(json.dumps(request["context"])),
                ).model_dump(mode="json")
            elif args.command == "gas-network":
                from .gas_network import GasNetwork, solve_gas_network

                request = json.loads(document)
                if set(request) != {"specification", "context"}:
                    raise ValueError("gas-network JSON requires exactly specification and context")
                result = solve_gas_network(
                    GasNetwork.model_validate_json(json.dumps(request["specification"])),
                    RunContext.model_validate_json(json.dumps(request["context"])),
                ).model_dump(mode="json")
            elif args.command == "pressure-fatigue":
                from .fatigue_exchange import PressureFatigueRequest, assess_pressure_fatigue

                request = json.loads(document)
                if set(request) != {"specification", "context"}:
                    raise ValueError("pressure-fatigue JSON requires exactly specification and context")
                result = assess_pressure_fatigue(
                    PressureFatigueRequest.model_validate_json(json.dumps(request["specification"])),
                    RunContext.model_validate_json(json.dumps(request["context"])),
                ).model_dump(mode="json")
            elif args.command == "water-quality":
                from .water_quality import WaterQualityRun, simulate_water_quality

                request = json.loads(document)
                if set(request) != {"network", "specification", "context"}:
                    raise ValueError("water-quality JSON requires exactly network, specification and context")
                result = simulate_water_quality(
                    args.input.parent / request["network"],
                    WaterQualityRun.model_validate_json(json.dumps(request["specification"])),
                    RunContext.model_validate_json(json.dumps(request["context"])),
                )
            elif args.command == "water-fit":
                from .inverse import FitRequest, Observations, fit_parameters
                from .water import WaterRun
                from .water_inverse import WaterForward, WaterParameter, WaterSensor

                request = json.loads(document)
                if set(request) != {
                    "network",
                    "specification",
                    "sensors",
                    "bindings",
                    "observations",
                    "fit",
                    "context",
                }:
                    raise ValueError(
                        "water-fit JSON requires network, specification, sensors, bindings, observations, fit and context"
                    )
                forward = WaterForward(
                    args.input.parent / request["network"],
                    specification=WaterRun.model_validate_json(json.dumps(request["specification"])),
                    sensors=tuple(WaterSensor.model_validate_json(json.dumps(s)) for s in request["sensors"]),
                    parameters=tuple(
                        WaterParameter.model_validate_json(json.dumps(p)) for p in request["bindings"]
                    ),
                )
                result = fit_parameters(
                    Observations.model_validate_json(json.dumps(request["observations"])),
                    FitRequest.model_validate_json(json.dumps(request["fit"])),
                    RunContext.model_validate_json(json.dumps(request["context"])),
                    forward,
                )
            elif args.command == "acoustic-delay":
                from .acoustics import (
                    DelayRequest,
                    PipeSpan,
                    SampledTrace,
                    localize_acoustic_leak,
                    verify_acoustic_localization_record,
                )

                request = json.loads(document)
                if set(request) != {"left", "right", "specification", "span", "context"}:
                    raise ValueError(
                        "acoustic-delay JSON requires left, right, specification, span and context"
                    )
                context = RunContext.model_validate_json(json.dumps(request["context"]))
                result = localize_acoustic_leak(
                    SampledTrace.model_validate_json(json.dumps(request["left"])),
                    SampledTrace.model_validate_json(json.dumps(request["right"])),
                    DelayRequest.model_validate_json(json.dumps(request["specification"])),
                    PipeSpan.model_validate_json(json.dumps(request["span"]))
                    if request["span"] is not None
                    else None,
                    context,
                )
                if verify_acoustic_localization_record(result) is not True:
                    raise ValueError("acoustic localization receipt did not verify")
            elif args.command == "risk":
                from .risk_exchange import run_risk_job

                result = run_risk_job(json.loads(document))
            elif args.command == "workflow":
                result = run_workflow(engine, Workflow.model_validate_json(document))
            else:
                request = json.loads(document)
                if set(request) != {"model_id", "parameters", "context"}:
                    raise ValueError("evaluation JSON requires exactly model_id, parameters and context")
                context = RunContext.model_validate_json(json.dumps(request["context"]))
                result = engine.evaluate(
                    request["model_id"], json.dumps(request["parameters"]), context, json_input=True
                ).model_dump(mode="json")
        rendered = json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
        if getattr(args, "output", None):
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        return 0
    except (
        ValueError,
        KeyError,
        TypeError,
        OSError,
        ImportError,
        RuntimeError,
        ArithmeticError,
        AttributeError,
    ) as error:
        print(
            json.dumps({"error_type": type(error).__name__, "error": str(error)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
