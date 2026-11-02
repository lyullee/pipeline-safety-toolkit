"""Publish bounded physical progress from verified execution evidence."""
from pathlib import Path
from datetime import datetime, timezone
import hashlib
import json

ROOT=Path(__file__).resolve().parents[1]
def load(p):return json.loads(p.read_text(encoding="utf-8"))
def save(p,d):p.write_text(json.dumps(d,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
def main():
    pointer=load(ROOT/"artifacts/physical-campaign-223/latest.json")
    proof=ROOT/pointer["receipt"]
    assert hashlib.sha256(proof.read_bytes()).hexdigest()==pointer["sha256"]
    r=load(proof)
    assert not r["source_files_changed_during_run"], "Unstable source cannot update official progress"
    runs={x["model"]:x for x in r["runs"]}
    passed={u for u,x in runs.items() if x["exit_code"]==0 and x["cases"] and all(c["status"]=="passed" for c in x["cases"])}
    assert len(passed)==r["passed_bounded_profiles"]
    p=ROOT/"research/physical_verification_223.json";d=load(p)
    previous={x["id"] for x in d["models"] if x["physical_profile_status"]=="passed_within_stated_scope"}
    d.update(completed_bounded_profiles=len(passed),pending_profiles=223-len(passed),scope_coverage_percent=100*len(passed)/223,latest_executed_evidence=pointer)
    for row in d["models"]:
        u=row["id"]
        if u in runs:
            x=runs[u];row.update(physical_profile_status="passed_within_stated_scope" if u in passed else "failed_or_incomplete",scope=x["scope"],reviewed_criteria=x["criteria"],executable_cases=x["selectors"],evidence_receipt=x.get("evidence_receipt",pointer["receipt"]),remaining_review="Other variants, material coefficient calibration and applicability outside the stated scope require separate review.")
    save(p,d)
    p=ROOT/"research/public_validation_status.json";d=load(p);d["prepared_at_utc"]=datetime.now(timezone.utc).isoformat()
    d["bounded_physical_campaign"].update(completed_profiles=len(passed),scope_percent=100*len(passed)/223,cases=r["cases"],passed=r["passed"],failure=r["failure"],error=r["error"],skipped=r["skipped"],source_receipt_sha256=pointer["sha256"],elapsed_wall_s=r["elapsed_wall_s"],parallel_subprocesses=r["parallel_subprocesses"],dependency_source_frozen=True,reused_profiles=len(r.get("reused_profiles",[])),executed_profiles_this_batch=len(r.get("executed_profiles_this_batch",runs)))
    save(p,d)
    p=ROOT/"research/physical_verification_backlog_223.json";q=load(p)
    for row in q["models"]:row["status"]="passed_within_stated_scope" if row["id"] in passed else (row.get("status","pending") if row.get("status","").startswith("pending") else "pending")
    q.update(current_completed=len(passed),current_pending=223-len(passed),latest_executed_evidence=pointer);save(p,q)
    text="# 남은 물리검증 작업 순서 — 2026-11-03\n\n"
    text+=f"현재 제한 영역 완료 **{len(passed)}/223({100*len(passed)/223:.2f}%)**, 남은 **{223-len(passed)}개**. 최초 정렬 대상은173개이며 통과 항목을 아래 목록에서 제외했다.\n\n"
    text+="예상 시간은 모델별 기준 검토·보완·실행 작업시간이며, 측정 계산시간이나 완료 약속이 아니다. 묶음 실행의 실제 시간은 별도 기록한다. 경험계수의 실측 적합성은 수식 재현으로 대체하지 않는다.\n\n| 순서 | 모델 ID | 예상 작업시간(분) | 묶음 |\n|---:|---|---:|---:|\n"
    for i,row in enumerate((x for x in q["models"] if x["status"]!="passed_within_stated_scope"),1):text+=f"| {i} | `{row['id']}` | {row['estimated_review_minutes_low']}–{row['estimated_review_minutes_high']} | {row['priority_group']} |\n"
    text+="\n묶음1: 평형·기본 관계,2: 독립 적분·ODE,3: 연성·망·수렴,4: 경험식/보정 검토,5: 직접 후보 부재。후보 호출과 독립 기준 검토가 끝난 모델만 실행·집계한다. 최초173개 목록과 후보 시험은 `research/physical_verification_backlog_223.json`에 보존한다.\n"
    (ROOT/"docs/PHYSICAL_VERIFICATION_BACKLOG_KO.md").write_text(text,encoding="utf-8")
    new=passed-previous
    summary=f"제한 영역 물리시험 **{len(passed)}/223({100*len(passed)/223:.2f}%)**, 미검토{223-len(passed)}개. 이번 신규{len(new)}개. 누적 모델별 시험{r['passed']}/{r['cases']}건 통과. 이번 배치{r['elapsed_wall_s']:.3f}초, 재사용{len(r.get('reused_profiles',[]))}개, 병렬{r['parallel_subprocesses']}개. 실패{r['failure']}·오류{r['error']}·건너뜀{r['skipped']}·실행 중 소스 변경0건. 코드 구현223/223(100%), 기존 실측 적합성 실패4건은 유지한다."
    header="## 2026-11-03 — 물리검증 연속 캠페인 최신 집계\n\n"+summary+"\n\n최신 근거와 모델별 제한 영역은 `research/physical_verification_223.json`, 남은 순서는 `docs/PHYSICAL_VERIFICATION_BACKLOG_KO.md`다. 전체 변형·현장 적합성 승인은 아니다.\n\n"
    for name in ("docs/STATUS_KO.md","docs/PLAN_KO.md","docs/CONTINUATION_KO.md"):
        p=ROOT/name;p.write_text(header+p.read_text(encoding="utf-8"),encoding="utf-8")
    print(json.dumps({"completed":len(passed),"pending":223-len(passed),"percent":100*len(passed)/223,"new_models":len(new),"new_cases":sum(len(runs[u]['cases']) for u in new),"cases":r["cases"],"passed":r["passed"],"elapsed_s":r["elapsed_wall_s"]},ensure_ascii=False))
if __name__=="__main__":main()
