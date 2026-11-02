"""Build source-linked input/output documentation without executing model solvers.

AST inspection preserves declared names and defaults. Return-key lists are unions
of statically visible branches, not promises that every status has every key.
Registry units are authoritative; suffix units elsewhere are explicitly labelled.
"""
from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "src/pipeline_integrity/catalog_data/engineering_methods.json"


def code(node):
    return ast.unparse(node) if node is not None else ""


def md(value):
    return str(value).replace("|", "&#124;").replace("\n", " ")


class SourceIndex:
    def __init__(self):
        self.modules = {}
        for path in sorted((ROOT / "src/pipeline_integrity").rglob("*.py")):
            module = ".".join(path.relative_to(ROOT / "src").with_suffix("").parts)
            tree = ast.parse(path.read_text(encoding="utf-8"))
            definitions = {n.name: n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))}
            imports = {}
            for n in ast.walk(tree):
                if not isinstance(n, ast.ImportFrom):
                    continue
                package = module.split(".")[:-1]
                prefix = package[:len(package) - n.level + 1] if n.level else []
                base = ".".join(prefix + ((n.module or "").split(".") if n.module else []))
                for alias in n.names:
                    imports[alias.asname or alias.name] = (base, alias.name)
            self.modules[module] = (path, definitions, imports)

    def resolve(self, module, name, seen=()):
        if (module, name) in seen or module not in self.modules:
            return None
        _, definitions, imports = self.modules[module]
        first, *tail = name.split(".")
        if first in definitions:
            node = definitions[first]
            for part in tail:
                node = next((n for n in getattr(node, "body", []) if getattr(n, "name", None) == part), None)
                if node is None:
                    return None
            return module, node
        if first in imports:
            other, original = imports[first]
            return self.resolve(other, ".".join([original, *tail]), (*seen, (module, name)))
        return None

    def fields(self, module, name, seen=()):
        resolved = self.resolve(module, name)
        if not resolved:
            return []
        module, node = resolved
        if not isinstance(node, ast.ClassDef) or (module, node.name) in seen:
            return []
        result = {}
        for base in node.bases:
            for field in self.fields(module, code(base), (*seen, (module, node.name))):
                result[field["name"]] = field
        for field in node.body:
            if not isinstance(field, ast.AnnAssign) or not isinstance(field.target, ast.Name):
                continue
            if field.target.id.startswith("_") or field.target.id == "model_config":
                continue
            default = field.value
            required = default is None
            default_text = code(default)
            if isinstance(default, ast.Call) and code(default.func).split(".")[-1] == "Field":
                kws = {k.arg: k.value for k in default.keywords}
                actual = kws.get("default", default.args[0] if default.args else None)
                required = actual is None and "default_factory" not in kws
                default_text = code(actual) if actual else ("factory: " + code(kws["default_factory"]) if "default_factory" in kws else "")
                if actual is not None and code(actual) == "...":
                    required, default_text = True, ""
            result[field.target.id] = {
                "name": field.target.id, "type": code(field.annotation),
                "required": required, "default": default_text,
                "declaration": code(field.value), "module": module,
                "line": field.lineno,
            }
        return list(result.values())

    def link(self, module, node, prefix="../"):
        path = self.modules[module][0].relative_to(ROOT).as_posix()
        return f"{prefix}{path}#L{node.lineno}"

    def output_keys(self, module, node, depth=0, active=()):
        """Follow local return expressions and helpers; do not scan arbitrary strings."""
        if depth > 5 or (module, node.name) in active:
            return set()
        active = (*active, (module, node.name))
        assigned = {}
        def walk(root):
            yield root
            for child in ast.iter_child_nodes(root):
                if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                    yield from walk(child)
        nodes = list(walk(node))
        for n in nodes:
            if isinstance(n, ast.Assign):
                for target in n.targets:
                    if isinstance(target, ast.Name):
                        assigned.setdefault(target.id, []).append(n.value)
            if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name) and n.value:
                assigned.setdefault(n.target.id, []).append(n.value)
        def keys(value, seen=()):
            if isinstance(value, ast.Dict):
                out = {k.value for k in value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)}
                for k, v in zip(value.keys, value.values):
                    if k is None:
                        out |= keys(v, seen)
                return out
            if isinstance(value, ast.Name) and value.id not in seen:
                out = set().union(*(keys(v, (*seen, value.id)) for v in assigned.get(value.id, [])))
                for statement in nodes:
                    if isinstance(statement, ast.Assign):
                        for target in statement.targets:
                            if (isinstance(target, ast.Subscript) and isinstance(target.value, ast.Name)
                                    and target.value.id == value.id and isinstance(target.slice, ast.Constant)
                                    and isinstance(target.slice.value, str)):
                                out.add(target.slice.value)
                    if (isinstance(statement, ast.Call) and isinstance(statement.func, ast.Attribute)
                            and isinstance(statement.func.value, ast.Name) and statement.func.value.id == value.id
                            and statement.func.attr == "update"):
                        for arg in statement.args:
                            out |= keys(arg, (*seen, value.id))
                        out |= {kw.arg for kw in statement.keywords if kw.arg}
                return out
            if isinstance(value, ast.BinOp) and isinstance(value.op, ast.BitOr):
                return keys(value.left, seen) | keys(value.right, seen)
            if isinstance(value, ast.Call):
                name = code(value.func)
                if name in ("json_value", "json.loads", "json.dumps") and value.args:
                    return keys(value.args[0], seen)
                if isinstance(value.func, ast.Attribute):
                    if value.func.attr in ("model_validate", "model_validate_json"):
                        model = self.fields(module, code(value.func.value))
                        if model:
                            return {f["name"] for f in model}
                    if value.func.attr in ("model_copy", "model_dump"):
                        return keys(value.func.value, seen)
                if name == "dict":
                    out = {k.arg for k in value.keywords if k.arg}
                    for item in value.args:
                        out |= keys(item, seen)
                    for item in value.keywords:
                        if item.arg is None:
                            out |= keys(item.value, seen)
                    return out
                if name.split(".")[-1] == "Calculation":
                    vals = next((x.value for x in value.keywords if x.arg == "values"), None)
                    return {"values." + k for k in keys(vals, seen)}
                resolved = self.resolve(module, name)
                if resolved:
                    mod, target = resolved
                    if isinstance(target, ast.ClassDef):
                        return {f["name"] for f in self.fields(mod, target.name)}
                    found = self.output_keys(mod, target, depth + 1, active)
                    # A sealing helper may return its same input mapping after
                    # attaching receipt metadata; map that argument explicitly.
                    positional = target.args.posonlyargs + target.args.args
                    for ret in ast.walk(target):
                        if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Name):
                            for arg, supplied in zip(positional, value.args):
                                if ret.value.id == arg.arg:
                                    found |= keys(supplied, seen)
                        if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Dict):
                            unpacked = [v.id for k, v in zip(ret.value.keys, ret.value.values)
                                        if k is None and isinstance(v, ast.Name)]
                            for arg, supplied in zip(positional, value.args):
                                if arg.arg in unpacked:
                                    found |= keys(supplied, seen)
                    return found
            if isinstance(value, ast.IfExp):
                return keys(value.body, seen) | keys(value.orelse, seen)
            return set()
        out = set()
        annotation = code(getattr(node, "returns", None))
        if annotation:
            out |= {f["name"] for f in self.fields(module, annotation)}
        for n in nodes:
            if isinstance(n, ast.Return):
                out |= keys(n.value)
        return out

    def api(self, api):
        parts = api.split(".")
        for cut in range(len(parts) - 1, 0, -1):
            resolved = self.resolve(".".join(parts[:cut]), ".".join(parts[cut:]))
            if resolved:
                break
        if not resolved:
            raise ValueError(f"Unresolved API: {api}")
        module, node = resolved
        original = node
        if isinstance(node, ast.ClassDef):
            node = next((n for n in node.body if isinstance(n, ast.FunctionDef) and n.name == "__init__"), node)
        args = []
        if isinstance(node, ast.FunctionDef):
            a = node.args
            positional = a.posonlyargs + a.args
            defaults = [None] * (len(positional) - len(a.defaults)) + list(a.defaults)
            for arg, default in zip(positional + a.kwonlyargs, defaults + list(a.kw_defaults)):
                if arg.arg in ("self", "cls"):
                    continue
                annotation = code(arg.annotation)
                # Several adapters deliberately accept an untyped request and
                # immediately validate its exact declared contract.
                if not annotation:
                    for call in ast.walk(node):
                        if not isinstance(call, ast.Call) or not isinstance(call.func, ast.Attribute):
                            continue
                        if call.func.attr not in ("model_validate", "model_validate_json") or not call.args:
                            continue
                        if arg.arg not in {n.id for n in ast.walk(call.args[0]) if isinstance(n, ast.Name)}:
                            continue
                        annotation = code(call.func.value)
                        break
                overrides = {
                    ("pipeline_integrity.buckling.evaluate_buckling", "request"): "BucklingRequest",
                    ("pipeline_integrity.soil_pipe.evaluate_soil_pipe", "request"): "SoilPipeRequest",
                    ("pipeline_integrity.profile_capacity.evaluate_profile_capacity", "request"): "ProfileCapacityRequest",
                    ("pipeline_integrity.surface_flaw_proof.evaluate_proof_limited_growth", "request"): "SurfaceFlawProofRequest",
                    ("pipeline_integrity.surface_flaw_proof.evaluate_proof_limited_growth", "template"): "CrackGrowthRequest",
                    ("pipeline_integrity.surface_flaw_proof.evaluate_proof_limited_tearing", "request"): "SurfaceFlawProofRequest",
                    ("pipeline_integrity.surface_flaw_proof.evaluate_proof_limited_tearing", "template"): "PressureHistoryTearingRequest",
                }
                annotation = overrides.get((api, arg.arg), annotation)
                if arg.arg == "context" and not annotation:
                    annotation = "RunContext"
                args.append({"name": arg.arg, "type": annotation or "annotation 없음", "required": default is None,
                             "default": code(default), "fields": self.fields(module, annotation)})
            outputs = sorted(self.output_keys(module, node))
            return_type = code(node.returns)
        else:
            args = [{"name": "constructor fields", "type": original.name, "required": True,
                     "default": "", "fields": self.fields(module, original.name)}]
            outputs = []
            return_type = original.name + " instance"
        notes = {
            "pipeline_integrity.nominal_pipe_pressure.nominal_hoop_stress_Pa": "단일 float: 지정한 직경 기준의 공칭 원주응력 [Pa]. 두께 방향 실제 응력장이 아니다.",
            "pipeline_integrity.nominal_pipe_pressure.nominal_wall_thickness_m": "단일 float: 사용자가 제공한 공칭응력으로 역산한 대수적 관벽 두께 [m]. 부식 여유·최소 제작두께는 포함하지 않는다.",
            "pipeline_integrity.gas_restricted_vent.ideal_vent_capacity": "4개 tuple: (보정한 질량유량 [kg/s], 유동 상태 문자열, 보정 전 질량유량 [kg/s], 정규화 계수 [1]).",
            "pipeline_integrity.liquid_moc_friction.LiquidMOCQuasisteadyDarcy": "생성자는 마찰 공급자 객체를 반환한다. 객체(state)의 결과는 셀별 운동량 감쇠율 tuple [1/s]이다.",
            "pipeline_integrity.liquid_moc_friction.LiquidMOCQuasisteadyDarcy.__call__": "셀별 운동량 감쇠율 tuple [1/s]. 입력 state와 셀 순서가 같다. 압력손실 Pa를 반환하는 API가 아니다.",
            "pipeline_integrity.gas_pipe_friction.StateDependentDarcyDecay": "생성자는 상태 의존 마찰 공급자 객체를 반환한다. 객체(state)의 결과는 셀별 운동량 감쇠율 tuple [1/s]이다.",
            "pipeline_integrity.buckling.evaluate_buckling": "기록 dict. solution이 계산되면 critical_positive_load_factor [1], modes, eigenvalue_counts와 평형 잔차를 포함한다. 보류 상태에서 solution은 None일 수 있다.",
            "pipeline_integrity.soil_pipe.evaluate_soil_pipe": "기록 dict. solution의 soil_response, members, physical_static_balance, physical_nodal_balance를 통해 지반 반력·부재 응답·전체/절점 평형을 확인한다. 불완전 입력에서는 solution을 보류한다.",
        }
        return {"api": api, "module": module, "line": original.lineno,
                "source": self.link(module, original, prefix=""), "parameters": args,
                "return_type": return_type, "visible_return_keys": outputs,
                "output_note": notes.get(api, ""),
                "source_sha256": hashlib.sha256(self.modules[module][0].read_bytes()).hexdigest()}


def main():
    index = SourceIndex()
    methods = json.loads(CATALOG.read_text(encoding="utf-8"))["methods"]
    names = sorted({a for row in methods for a in row["apis"]})
    apis = {name: index.api(name) for name in names}
    ids = {name: f"api-{n:03}" for n, name in enumerate(names, 1)}
    detail_paths = {name: f"module_io/API_{((n-1)//50)*50+1:03}_{min(((n-1)//50+1)*50,len(names)):03}.md"
                    for n, name in enumerate(names, 1)}
    text = ["# 모듈별 입력·출력 상세 명세", "",
            "README의 223개 방법과 연결된 공개 API를 실제 Python 선언에서 추출했다. 계산 solver를 실행하지 않는다.", "",
            "필수 여부는 Python/Pydantic 선언 기준이다. 기본값이 None이거나 검토 플래그가 False인 입력은 생략 가능한 선언이어도 실제 계산에는 추가 자료가 필요할 수 있다. 필드 간 조건은 연결한 validator와 분야별 안내를 함께 따른다.", "",
            "`Positive`는 유한한 수 >0, `Nonnegative`는 ≥0, `Fraction`은 0–1, `Name`은 공백만으로 이루어지지 않은 문자열이다. `None`은 0이 아니다. `_pa`/`_Pa`는 Pa, `_m`은 m, `_s`는 s, `_k`/`_K`는 K 등 필드의 단위 표기를 따른다. 압력 reference와 경험계수 단위는 별도 근거가 필요하다.", "",
            "출력 키 목록은 반환문·로컬 helper·반환 계약에서 정적으로 확인되는 **분기별 합집합**이다. 성공·보류·범위 밖 상태에 모두 존재한다는 뜻이 아니다. 외부 provider의 동적 결과를 정적 목록으로 전부 규정하지 않으며, 그 경우 반환 형식과 원 API를 함께 확인한다. `Calculation` 출력은 `values.` 경로로 표시한다.", "",
            f"전체: **{len(methods)}개 방법 / {len(apis)}개 고유 API**. [README](../README.md#모델별-목적입력출력-223개) · [분야별 안내](INDEX_KO.md)", ""]
    for name, item in apis.items():
        text.extend([f"<a id=\"{ids[name]}\"></a>", f"## `{name}`", "", f"[실제 구현]({ '../' + item['source']})", "", "### 호출 인자", "",
                     "| 인자 | 형식 | 필수 여부 / 기본값 |", "|---|---|---|"])
        for param in item["parameters"]:
            state = "필수" if param["required"] else "기본값: `" + md(param["default"]) + "`"
            text.append(f"| `{param['name']}` | `{md(param['type'])}` | {state} |")
        visited = set()
        queue = [(p["type"], p["fields"]) for p in item["parameters"] if p["fields"]]
        while queue:
            typename, fields = queue.pop(0)
            if typename in visited:
                continue
            visited.add(typename)
            text.extend(["", f"### `{md(typename)}` 필드", "", "| 파라미터 | 형식·허용 선택지 | 필수 | 기본값 | 추가 Field 제약 |", "|---|---|---|---|---|"])
            for f in fields:
                text.append(f"| `{f['name']}` | `{md(f['type'])}` | {'예' if f['required'] else '아니요'} | {('`' + md(f['default']) + '`') if f['default'] else '—'} | {('`' + md(f['declaration']) + '`') if f['declaration'].startswith('Field(') else '—'} |")
                # Expand nested local/imported contracts, including tuple items.
                try:
                    symbols = sorted({n.id for n in ast.walk(ast.parse(f["type"], mode="eval")) if isinstance(n, ast.Name)})
                except SyntaxError:
                    symbols = []
                for symbol in symbols:
                    nested = index.fields(f["module"], symbol)
                    if nested and symbol not in visited:
                        queue.append((symbol, nested))
        text.extend(["", "### 출력", "", "- 반환 형식: `" + md(item["return_type"] or "명시적 반환 annotation 없음; 반환문과 아래 키 참조") + "`."])
        if item["visible_return_keys"]:
            text.append("- 소스에서 확인한 반환 키(분기별 합집합): " + ", ".join(f"`{md(k)}`" for k in item["visible_return_keys"]) + ".")
        else:
            text.append("- 정적 고정 키 목록을 추출할 수 없는 반환값이다. 위 실제 구현의 반환 형식/`__call__`과 provider 계약을 따른다.")
        if item["output_note"]:
            text.append("- 결과 해석: " + item["output_note"])
        related = [m["id"] for m in methods if name in m["apis"]]
        text.extend(["- 연결 방법: " + ", ".join(f"`{m}`" for m in related) + ".", ""])
    # Keep each rendered page small enough for normal GitHub Markdown viewing.
    first = text.index(f'<a id="{ids[names[0]]}"></a>')
    header = text[:first]
    folder = ROOT / "docs/module_io"
    folder.mkdir(exist_ok=True)
    for offset in range(0, len(names), 50):
        start = text.index(f'<a id="{ids[names[offset]]}"></a>')
        stop = text.index(f'<a id="{ids[names[offset+50]]}"></a>') if offset+50 < len(names) else len(text)
        page = "\n".join(header + text[start:stop])
        page = page.replace('(../src/', '(../../src/').replace('(../README.md', '(../../README.md').replace('(INDEX_KO.md)', '(../INDEX_KO.md)')
        (ROOT / "docs" / detail_paths[names[offset]]).write_text(page, encoding="utf-8")
    navigation = header + ["## API 선택", "", "| API | 전체 파라미터와 반환 계약 |", "|---|---|"]
    for name in names:
        navigation.append(f"| `{name}` | [입력·출력 상세]({detail_paths[name]}#{ids[name]}) |")
    (ROOT / "docs/MODULE_IO_REFERENCE_KO.md").write_text("\n".join(navigation) + "\n", encoding="utf-8")

    table = ["<!-- BEGIN GENERATED MODEL IO -->", "## 모델별 목적·입력·출력: 223개", "",
             "각 행은 공학 방법 하나다. 같은 계산 API를 여러 방법이 공유할 수 있으므로 행 수가 독립 solver 수를 뜻하지 않는다. **입력은 첫 번째 API의 호출 인자와 request의 전체 최상위 필드**, 출력은 그 API의 계산 결과 키다. 모든 대체 API와 필수·선택 입력, 기본값, 중첩 자료 구조는 각 행의 **전체 명세**에 있다. API를 바꿔 호출할 때는 그 API의 계약을 사용한다.", "",
             "표의 입력은 필수 필드를 먼저, 그 뒤 기본값이 있는 선택 필드(`?`)를 표시한다. request 안의 중첩 구조는 전체 명세에서 펼쳐 설명한다. 출력은 공통 상태·출처·검토 플래그를 제외한 반환 키이며, 상태에 따라 일부 키가 없을 수 있다. 선언되지 않은 출력이나 기본 물성은 만들지 않는다.", "",
             "| # | 모델 ID / 사용 목적 | 입력 파라미터·계약 | 계산 출력 / 모든 API 명세 |", "|---:|---|---|---|"]
    for n, row in enumerate(methods, 1):
        name = row["apis"][0]
        item = apis[name]
        params = []
        for p in item["parameters"]:
            if p["name"] == "context":
                params.append("`context: RunContext`")
            elif p["fields"]:
                ordered = sorted(p["fields"], key=lambda f: not f["required"])
                labels = [f"`{f['name']}`" + ("?" if not f["required"] else "") for f in ordered]
                params.append(f"`{md(p['type'])}`: " + ", ".join(labels))
            else:
                params.append(f"`{md(p['name'])}: {md(p['type'])}`" + ("?" if not p["required"] else ""))
        exclusions = {"status", "issues", "notes", "basis_id", "reviewed", "qualified", "schema", "request", "context",
                      "backend", "source", "implementation", "output_origin", "runtime", "provider", "native_receipt"}
        outputs = [k for k in item["visible_return_keys"] if k not in exclusions and not k.endswith(
            ("_id", "_reviewed", "_qualified", "_sha256", "_digest", "_inferred", "_claim", "_qualification", "_calculated"))]
        def result_order(key):
            if key in ("values", "solution"):
                return (0, key)
            unit_endings = ("_m", "_m2", "_m3", "_s", "_pa", "_n", "_w", "_j", "_kg", "_m_s", "_kg_s", "_n_m", "_m3_s")
            return (1 if key.lower().endswith(unit_endings) else 2, key)
        outputs.sort(key=result_order)
        output = ", ".join(f"`{md(k)}`" for k in outputs)
        if not output:
            output = md(item["output_note"]) if item["output_note"] else ("`" + md(item["return_type"]) + "`" if item["return_type"] else "반환 계약/동적 결과는 전체 명세 참조")
        links = " · ".join(f"[{a.replace('pipeline_integrity.', '')}](docs/{detail_paths[a]}#{ids[a]})" for a in row["apis"])
        purpose = " ".join(row["supported_domain"])
        table.append(f"| {n} | **`{row['id']}`**<br>{md(purpose)} | {'; '.join(params)} | {output}<br>**전체 명세:** {links} |")
    table.extend(["", "<!-- END GENERATED MODEL IO -->"])
    readme = ROOT / "README.md"
    content = readme.read_text(encoding="utf-8")
    start, end = "<!-- BEGIN GENERATED MODEL IO -->", "<!-- END GENERATED MODEL IO -->"
    generated = "\n".join(table)
    if start in content:
        before, rest = content.split(start, 1)
        _, after = rest.split(end, 1)
        content = before + generated + after
    else:
        content = content.replace("## 검증 상태와 사용 범위", generated + "\n\n## 검증 상태와 사용 범위", 1)
    # The public registry exposes exact units and machine-readable Pydantic
    # schemas without importing optional numerical engines or running a model.
    sys.path.insert(0, str(ROOT / "src"))
    from pipeline_integrity import create_registry
    registry = create_registry()
    titles = {
        "construction.trench_volume": "선언한 사다리꼴 굴착 단면의 원지반·느슨한 토량",
        "economics.cashflow": "기간별 현금흐름의 할인현재가치",
        "economics.energy": "운전 에너지·비용·주어진 배출계수의 배출량",
        "equipment.compression": "실제 유체의 등엔트로피 기준 압축 동력과 출구 상태",
        "equipment.control_valve_gas": "가스 밸브의 설치 조건을 반영한 요구 계수",
        "equipment.control_valve_liquid": "액체 밸브의 설치 조건을 반영한 요구 계수",
        "equipment.pump_affinity": "주어진 속도·기하 비율에 따른 펌프 운전량 환산",
        "equipment.pump_npsh": "가용 흡입수두와 사용자가 준 요구 NPSH의 여유",
        "equipment.pump_power": "유량·양정·효율에 따른 유체·축·전기 동력",
        "equipment.pump_system": "동일 펌프와 등가 시스템 곡선의 운전점",
        "equipment.relief_gas": "주어진 가스 조건의 방출 면적 계산",
        "geometry.pipe": "배관 유로·관벽의 면적, 체적과 관벽 질량",
        "hydraulics.gas_correlation": "선택한 경험식에 따른 가스 배관 유량",
        "hydraulics.hydrostatic": "밀도·표고 차에 따른 정수압",
        "hydraulics.isothermal_gas": "등온 가스 배관의 질량 유량",
        "hydraulics.liquid_head_loss": "밀도 없이 계산하는 부호 있는 손실수두와 필요수두",
        "hydraulics.liquid_loss": "마찰·국부손실·표고의 압력 예산과 소산 동력",
        "instrument.liquid_dp_meter": "차압 계측값에서 액체 질량·체적 유량 환산",
        "inventory.segmented": "구간별 물성으로 합산한 실제 유체 재고량",
        "inventory.uniform": "균일 상태 실제 유체의 재고와 기준 체적",
        "thermal.flow_cooling": "상수 물성 가정의 수송 유체 출구 온도와 열 교환",
        "thermal.free_expansion": "구속되지 않은 관의 선형 열팽창",
        "thermal.internal_convection": "선택한 관내 유동 상관식의 대류 열전달",
        "transient.surge_pulse": "단순 압력파 크기와 밸브 폐쇄 시간척도 비교",
        "transient.wave_speed": "관벽 탄성을 고려한 액체 압력파 속도",
    }
    quick = ["<!-- BEGIN GENERATED REGISTRY IO -->", "## 바로 호출하는 기본 계산 모듈: 25개", "",
             "아래 ID는 `create_registry().evaluate(ID, inputs, context)`에 넣는 실제 계산 ID다. **223개 공학 방법을 찾는 ID와 별도 목록이며 두 수를 더하지 않는다.** 모든 입력·출력 이름과 단위/의미는 등록된 계약 그대로다. 성공 결과의 계산값은 `result.values`, 출력 단위는 `result.output_units`에 있다.", "",
             "| 계산 ID | 목적 | 입력 파라미터 `[단위/의미]` | 출력 `result.values` `[단위/의미]` |", "|---|---|---|---|"]
    registry_records = []
    for info in registry.models():
        desc = registry.describe(info.id)
        required = set(desc["input_schema"].get("required", []))
        inputs = ", ".join(f"`{key}`{'?' if key not in required else ''} [{md(unit)}]" for key, unit in info.input_units.items())
        outputs = ", ".join(f"`{key}` [{md(unit)}]" for key, unit in info.output_units.items())
        quick.append(f"| `{info.id}` | {titles[info.id]} | {inputs} | {outputs} |")
        registry_records.append(desc)
    quick.extend(["", "`?` 表示에 해당하는 항목은 **기본값이 있는 선택 입력**이다. 실제 기본값·허용 선택지·수치 제약은 아래 `describe()` 예시나 [전체 레지스트리 스키마](research/registry_io_reference.json)를 확인한다. 단위 `1`은 무차원이며, 문자열로 표시한 basis/metadata는 수치 단위가 아닌 의미 계약이다.", "", "<!-- END GENERATED REGISTRY IO -->"])
    quick_text = "\n".join(quick).replace('`?` 表示에 해당하는 항목은', '`?`가 붙은 항목은')
    if "<!-- BEGIN GENERATED REGISTRY IO -->" in content:
        before, rest = content.split("<!-- BEGIN GENERATED REGISTRY IO -->", 1)
        _, after = rest.split("<!-- END GENERATED REGISTRY IO -->", 1)
        content = before + quick_text + after
    else:
        content = content.replace("<!-- BEGIN GENERATED MODEL IO -->", quick_text + "\n\n<!-- BEGIN GENERATED MODEL IO -->", 1)
    readme.write_text(content, encoding="utf-8")
    (ROOT / "research/registry_io_reference.json").write_text(json.dumps({"models": registry_records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    report = {"methods": len(methods), "unique_apis": len(apis), "api_contracts": apis,
              "extraction": "Static Python declarations; return keys are branch unions, not all-status schemas."}
    (ROOT / "research/module_io_reference.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"documented_methods": len(methods), "documented_apis": len(apis),
                      "apis_with_input_fields": sum(any(p["fields"] for p in a["parameters"]) for a in apis.values()),
                      "apis_with_visible_output_keys": sum(bool(a["visible_return_keys"]) for a in apis.values()),
                      "readme_bytes": len(content.encode("utf-8"))}))


if __name__ == "__main__":
    main()
