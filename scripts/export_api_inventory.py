"""Export source-level Python API signatures without importing numerical engines."""

import ast
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def signature(node):
    return {
        "name": node.name,
        "parameters": ast.unparse(node.args),
        "returns": None if node.returns is None else ast.unparse(node.returns),
        "summary": (ast.get_docstring(node) or "").split("\n")[0],
        "async": isinstance(node, ast.AsyncFunctionDef),
    }


def inventory(root):
    package = Path(root) / "src/pipeline_integrity"
    modules = []
    for path in sorted(package.rglob("*.py")):
        relative = path.relative_to(package)
        if any(part.startswith("_") and part != "__init__.py" for part in relative.parts):
            continue
        raw = path.read_bytes()
        tree = ast.parse(raw.decode("utf-8-sig"), filename=str(relative))
        functions = []
        classes = []
        for node in tree.body:
            if not isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ) or node.name.startswith("_"):
                continue
            if isinstance(node, ast.ClassDef):
                classes.append(
                    {
                        "name": node.name,
                        "bases": [ast.unparse(b) for b in node.bases],
                        "fields": [
                            {
                                "name": ast.unparse(item.target),
                                "annotation": ast.unparse(item.annotation),
                                "default_expression": None if item.value is None else ast.unparse(item.value),
                            }
                            for item in node.body
                            if isinstance(item, ast.AnnAssign)
                            and isinstance(item.target, ast.Name)
                            and not item.target.id.startswith("_")
                        ],
                        "methods": [
                            signature(item)
                            for item in node.body
                            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                            and not item.name.startswith("_")
                        ],
                    }
                )
            else:
                functions.append(signature(node))
        modules.append(
            {
                "module": ".".join(
                    ("pipeline_integrity", *[p for p in relative.with_suffix("").parts if p != "__init__"])
                ),
                "source_path": path.relative_to(root).as_posix(),
                "source_sha256": hashlib.sha256(raw).hexdigest(),
                "summary": (ast.get_docstring(tree) or "").split("\n")[0],
                "functions": functions,
                "classes": classes,
            }
        )
    return {
        "schema": "pipeline_integrity.static_api_inventory.v1",
        "kind": "static_source_index_not_runtime_validation",
        "numerical_engines_imported": False,
        "tests_executed": 0,
        "modules": modules,
    }


def main():
    result = inventory(ROOT)
    target = ROOT / "research/api_inventory.json"
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "static_modules": len(result["modules"]),
                "tests_executed": 0,
                "output": "research/api_inventory.json",
            }
        )
    )


if __name__ == "__main__":
    main()
