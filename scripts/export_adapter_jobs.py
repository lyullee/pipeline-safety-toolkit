"""Statically cross-check the adapter job allowlist against real source signatures."""

import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    package = ROOT / "src/pipeline_integrity"
    declarations = ast.parse((package / "adapter_jobs.py").read_text(encoding="utf-8-sig"))
    values = next(
        n.value
        for n in declarations.body
        if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "SPECS" for t in n.targets)
    )
    capability_ids = {
        r["id"]
        for r in json.loads((package / "catalog_data/capabilities.json").read_text(encoding="utf-8"))[
            "capabilities"
        ]
    }
    records = []
    for node in values.elts:
        if (
            not isinstance(node, ast.Call)
            or not isinstance(node.func, ast.Name)
            or node.func.id != "AdapterSpec"
            or node.keywords
        ):
            raise ValueError("adapter declarations must use literal positional AdapterSpec values")
        args = [ast.literal_eval(a) for a in node.args]
        identity, module, inputs, evaluate, verify = args[:5]
        roles = tuple(args[5]) if len(args) > 5 else ()
        payloads = tuple(args[6]) if len(args) > 6 else ()
        if identity not in capability_ids or not module.isidentifier():
            raise ValueError("adapter ID/module does not match the public capability scope")
        path = package / (module + ".py")
        raw = path.read_bytes()
        tree = ast.parse(raw.decode("utf-8-sig"))
        definitions = {
            n.name: n
            for n in tree.body
            if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in (inputs, *(model for _, model in payloads)):
            if not isinstance(definitions.get(name), ast.ClassDef):
                raise ValueError("declared adapter input/payload class is absent: " + module + "." + name)
        for function, expected in (
            (evaluate, set(roles) | {r for r, _ in payloads}),
            (verify, set(roles) | {"request", "context"}),
        ):
            target = definitions.get(function)
            if not isinstance(target, ast.FunctionDef):
                raise ValueError("declared synchronous adapter function is absent")
            positional = [*target.args.posonlyargs, *target.args.args]
            required_positional = len(positional) - len(target.args.defaults)
            minimum = 2 if function == evaluate else 1
            if target.args.vararg is None and (len(positional) < minimum or required_positional > minimum):
                raise ValueError(
                    "declared adapter function cannot accept the actual dispatcher positional arguments"
                )
            if target.args.vararg is not None and required_positional > minimum:
                raise ValueError("adapter function requires additional positional arguments")
            if expected & {a.arg for a in positional[:minimum]}:
                raise ValueError("dispatcher keyword would duplicate an occupied positional argument")
            names = {a.arg for a in (*target.args.args, *target.args.kwonlyargs)}
            if expected - names and target.args.kwarg is None:
                raise ValueError(
                    "job provider/payload/verifier keywords differ from source signature: "
                    + module
                    + "."
                    + function
                )
            available = expected | (
                {a.arg for a in positional[:2]}
                if function == evaluate
                else {positional[0].arg}
                if positional
                else set()
            )
            required_keywords = {
                a.arg
                for a, default in zip(target.args.kwonlyargs, target.args.kw_defaults, strict=True)
                if default is None
            }
            if required_keywords - available:
                raise ValueError("adapter has an unprovided mandatory keyword argument")
        records.append(
            {
                "id": identity,
                "module": "pipeline_integrity." + module,
                "parameters_contract": inputs,
                "evaluate": evaluate,
                "verify": verify,
                "provider_roles": roles,
                "payload_contracts": dict(payloads),
                "consumer_source_sha256": hashlib.sha256(raw).hexdigest(),
                "signature_check": "static_ast_only",
                "native_execution_verified": False,
            }
        )
    if len({r["id"] for r in records}) != len(records):
        raise ValueError("duplicate adapter job IDs")
    output = {
        "schema": "pipeline_integrity.adapter_job_inventory.v1",
        "kind": "static_source_allowlist_not_runtime_validation",
        "adapters": records,
        "tests_executed": 0,
    }
    (ROOT / "research/adapter_job_inventory.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps({"statically_checked_adapters": len(records), "tests_executed": 0}))


if __name__ == "__main__":
    main()
