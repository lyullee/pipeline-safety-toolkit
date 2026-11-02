"""Export concrete owner-session requests from reviewed local contracts, without imports."""

import ast
import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    document = json.loads((ROOT / "research/owner_extension_requests.json").read_text(encoding="utf-8"))
    capabilities = {
        r["id"]
        for r in json.loads(
            (ROOT / "src/pipeline_integrity/catalog_data/capabilities.json").read_text(encoding="utf-8")
        )["capabilities"]
    }
    requests = document["requests"]
    if len({r["id"] for r in requests}) != len(requests):
        raise ValueError("duplicate request IDs")
    folder = ROOT / "docs/owner_requests"
    pending_documents = {}
    contracts = []
    links = []
    for request in requests:
        if not re.fullmatch(r"PIPE-EXT-[0-9]{3}", request["id"]):
            raise ValueError("request ID must be a safe generated document name")
        guide = Path(request["guide"])
        if guide.parent != Path("docs") or guide.suffix != ".md" or not (ROOT / guide).is_file():
            raise ValueError("request must link one existing dedicated guide")
        if set(request["capability_ids"]) - capabilities:
            raise ValueError("request references an unknown capability: " + request["id"])
        snapshots = []
        for module, names in request["consumer_contracts"].items():
            if not module.isidentifier():
                raise ValueError("consumer module must be a top-level package module name")
            path = ROOT / "src/pipeline_integrity" / f"{module}.py"
            raw = path.read_bytes()
            tree = ast.parse(raw.decode("utf-8-sig"))
            available = {
                n.name: n
                for n in tree.body
                if isinstance(n, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            }
            if set(names) - set(available):
                raise ValueError(
                    f"{request['id']}: missing exact contracts in {module}: {set(names) - set(available)}"
                )
            snapshots.append(
                {
                    "module": "pipeline_integrity." + module,
                    "source_path": path.relative_to(ROOT).as_posix(),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "contract_names": names,
                }
            )
        contracts.append(
            {
                "request_id": request["id"],
                "consumer_sources": snapshots,
                "owner_implementation_verified": False,
                "tests_executed": 0,
            }
        )
        lines = [
            "# " + request["id"] + " — " + request["title_ko"],
            "",
            "상태: 원 소유 모듈의 변경 요청입니다. 이 문서의 생성은 구현·시험·승인 증거가 아닙니다.",
            "",
            "대상 컴포넌트: " + ", ".join(request["target_components"]),
            "",
            "이 세션에서는 아래 소비자 계약을 유지하며 대상 원 엔진을 확장하세요. 같은 물리/통계 엔진을 복제하지 마세요. 대상이 이 저장소 내부인 항목은 여기서 구현할 로컬 작업으로 구분하고, 외부 저장소에 잘못 넘기지 마세요.",
            "",
            "## 소비자 계약",
            "",
        ]
        for item in snapshots:
            lines.append(
                "- `"
                + item["module"]
                + "`: "
                + ", ".join("`" + n + "`" for n in item["contract_names"])
                + " — [소스](../../"
                + item["source_path"]
                + "), SHA-256 `"
                + item["sha256"]
                + "`."
            )
        if request.get("already_available_here_pending_numerical_validation"):
            lines += ["", "## 이 저장소에 이미 작성한 범위", ""] + [
                "- " + item for item in request["already_available_here_pending_numerical_validation"]
            ]
        for heading, key in [
            ("요구 동작", "requirements"),
            ("필요한 수락 증거", "acceptance_evidence_required"),
            ("공통 계약", "shared_invariants"),
        ]:
            lines += ["", "## " + heading, ""] + ["- " + v for v in request[key]]
        lines += [
            "",
            "## 인수인계",
            "",
            "[상세 매뉴얼](../"
            + Path(request["guide"]).name
            + ")을 먼저 읽고 현재 계약의 지원/미지원 범위를 유지하세요. 구현한 원 API·버전·소스·라이선스·실제 시험 결과와 실패/보류를 기록한 뒤 소비자 어댑터와 함께 검토하세요. 현재 수치 시험은 누리온 대기이며 실행하지 않았습니다.",
            "",
            "연결 기술: " + ", ".join("`" + v + "`" for v in request["capability_ids"]) + ".",
            "",
        ]
        pending_documents[request["id"] + ".md"] = "\n".join(lines)
        links.append("- [" + request["id"] + " — " + request["title_ko"] + "](" + request["id"] + ".md)")
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in pending_documents.items():
        (folder / name).write_text(content, encoding="utf-8")
    (folder / "README.md").write_text(
        "# 원 소유 모듈 확장 요청\n\n각 문서는 다른 세션에 그대로 전달할 수 있는 작업 계약입니다. 외부 저장소에는 아직 메시지·이슈·수정을 보내지 않았습니다. 공통 필수 조건과 실제 consumer 소스 SHA를 포함합니다. 원 엔진의 구현 완료 또는 시험 통과를 뜻하지 않습니다.\n\n"
        + "\n".join(links)
        + "\n",
        encoding="utf-8",
    )
    (ROOT / "research/owner_extension_contracts.json").write_text(
        json.dumps(
            {
                "schema": "pipeline_integrity.owner_contract_snapshots.v1",
                "kind": "static_source_contracts_not_execution",
                "requests": contracts,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"request_documents": len(requests), "tests_executed": 0, "owner_repositories_modified": False}
        )
    )


if __name__ == "__main__":
    main()
