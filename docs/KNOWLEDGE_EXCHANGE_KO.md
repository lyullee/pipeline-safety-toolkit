# SAGA 지식 교환과 검토 경계

`knowledge_exchange`는 SAGA의 실제 로컬 지식 검색·인용·HAZOP 후보 검토를
배관의 설비·시계·자료 권리·유효기간과 연결한다. 이쪽에서 검색 순위, 벡터,
지식 추론, 규칙 판정 알고리즘을 다시 만들지 않는다. 원 `Database.hybrid_search`,
`RagPipeline.citations`, `HazopRuleInput`, `HazopEngine.monitoring_readiness`,
`HazopEngine.propose_threshold`를 직접 사용한다. 원 `Citation`, `ChatRequest`,
`AnswerReview`, `GroundedClaims`, `DigitalTwinHazopRequest` 등의 JSON 스키마 해시도
남긴다. 이 단계의 적절한 전체 범위 상태는 `partial`이다.

검색과 후보 준비도 검사는 실행됐지만 외부 추론 서비스, 최신 법령·기술규격의
정확성, 실제 문헌 답변의 완전성·인용 충실도·현장 적합성은 검증하지 않았다.
생성 답변을 꾸며 성공으로 세지 않았다. `computed`인 후보 검토는 진단 작업을
수행했다는 뜻이며 해당 후보가 유효한 경보 설정이라는 뜻이 아니다.

## 소유 API와 출처

원 출처는 [SAGA-PY 저장소](https://github.com/lyullee/saga-py)다. 확인한 로컬
`CITATION.cff`는 2.0.1, 2026-10-01, MIT, Ugwiyeon Lee를 기록한다.
원 README의 보관 식별자는 [10.5281/zenodo.23084441](https://doi.org/10.5281/zenodo.23084441)다.
이 문서에서는 원격 최신 버전이나 DOI의 현행 해석을 검증했다고 주장하지 않는다.
실행한 바이트와 라이선스 해시는 공개 결과 JSON의 `source_fingerprint`가 기준이다.

로컬 소스에는 별도 `AGENTS.md`가 없었다. 프로젝트 선언 버전은 2.0.1이지만
실제 로딩한 `saga.__version__`은 2.0.0이다. 두 값을 다르게 유지한다.
`api.py`는 UI 정적 자산을 전제로 하며 이 소스 묶음에는 그 자산이 없다.
따라서 FastAPI 서버를 우회 구현하지 않고 지원되는 라이브러리 API를 호출했다.
원 서버·HTTP 인증·스트리밍·업로드·OCR 경로는 이번 실행 대상이 아니다.

원 SAGA 소프트웨어는 MIT이며 Copyright (c) 2026 Ugwiyeon Lee 고지를 보존한다.
MIT의 허가와 무보증 조건은 원 `LICENSE`를 따른다. 이쪽은 원 코드를 배포하거나
복제하지 않는다. 별도 환경의 OpenAI SDK는 import 의존성이며 서비스 호출은 없다.
PyMuPDF는 원 RAG import 계보상 필요했고, 그 패키지의 별도 AGPL/상업 조건을
SAGA의 MIT로 재표시하지 않는다. PDF 색인·원문 추출은 실행하지 않았다.
코드 배포에는 이 외부 환경이나 라이브러리 바이너리를 넣지 않는다.

## 호출

공개 함수는 다음과 같다.

- `evaluate_knowledge(request, context, *, client=None) -> dict`: 계약을 재검증하고
  검토·권리·유효기간·범위가 해소된 경우에만 전달한다.
- `verify_knowledge_record(record) -> True`: 입력·시계·해시·인용 위치·권한·원 상태를
  다시 검사한다. 해시는 무결성 확인이며 서명이나 실행 사실의 독립적인 증명은 아니다.
- `knowledge_source_fingerprint(source_root) -> dict`: 원 Python 소스와 선언·라이선스·
  인용 메타데이터·README·별칭 JSON을 경로와 바이트 순서로 고정한다.
- `SagaOfflineClient(source_root=..., python_executable=..., work_root=..., timeout_s=30)`:
  별도 Python으로 JSON worker를 실행한다. 원 프로젝트 아래를 작업 저장소로 쓰면 거부한다.

`context`는 기존 `RunContext`다. `asset_id`, `clock_id`는 요청과 같아야 하고
`evidence_ids`와 모든 검토 근거는 `context.evidence`에 존재해야 한다.
`scenario_id`, origin과 근거의 출처·locator·권리도 요청 digest와 별도로 보존한다.
유효기간은 명시한 timezone-aware 시각으로 비교하며 시스템 현재 시각을 추측하지 않는다.

방법별 의미는 다음과 같다.

| method | 실제 경로 | 결과 해석 |
|---|---|---|
| `retrieve` | SQLite FTS + 원 hash-ngram hybrid 검색, 원 인용 생성 | 검색된 자료 후보이며 답변·관련성 확률이 아님 |
| `candidate_review` | 원 HAZOP 입력 스키마 + 임시 DB + 원 monitoring_readiness | 입력 후보를 `pending`으로 둔 준비도 진단 |
| `threshold_proposal` | 원 propose_threshold, reasoner 없음 | 검색 근거를 반환하고 추론 미구성 `needs_data` |
| `grounded_answer` | 원 ChatRequest 스키마 검증, 서비스 실행 없음 | 인용 검색과 추론 요청만 보존하고 `needs_data` |

HAZOP 후보를 넣는 DB는 이 작업의 임시 저장소다. 원 프로젝트 DB나 실제 운전의
HAZOP 표를 수정하지 않는다. 후보의 인용문이 지정 원문에 존재하는지 검사하지만
원 준비도 API는 그 문장이 수치의 물리적·법적 타당성을 입증하는지 판단하지 않는다.
이 한계를 수치 승인으로 바꾸지 않는다. 원 후보 생성기의 수치·인용 검사 전체는
실제 추론 서비스가 없으므로 이번에 검증하지 못했다.

## 모든 입력 필드

`KnowledgeRequest`는 frozen/strict/extra-forbid 계약이다. Python의 `model_copy`로
바뀐 객체도 실행 직전에 재검증한다. JSON round-trip은 별도 검증 함수가 지원한다.

| 필드 | 의미·제약 |
|---|---|
| `schema_version` | `pipeline_integrity.knowledge_request.v1` 고정 |
| `method` | 위의 네 방법 |
| `asset_id`, `location_id`, `clock_id`, `domain_id` | 설비·검토 위치·시계·적용 분야; 미상 값을 다른 분야로 확장하지 않음 |
| `as_of_utc` | timezone-aware 검토 시각; 자료의 `[valid_from, valid_until)` 안에 있어야 함 |
| `question` | 비공백 1~2,000자; 문서 내용은 지시가 아닌 검색 대상 |
| `sources` | `KnowledgeSource` 최대 20개; 기본 빈 값은 보류 |
| `allowed_source_ids` | 명시적으로 허용한 source ID; 기본 빈 값은 보류 |
| `document_codes` | 선택 문서의 hard filter; 빈 값은 허용 corpus 전체 |
| `candidates` | caller가 제공한 후보 최대 20개; candidate_review에는 필요 |
| `applicability_reviews` | 아래 네 bool 검토; 기본 미입력은 모두 미검토 |
| `review_evidence_ids` | 각 검토를 뒷받침하는 선택 근거 ID |
| `evidence_ids` | 1~100개, 중복 없는 context 근거 선택 |
| `retrieval_limit` | 1~20개, 기본 5; bool을 개수로 허용하지 않음 |
| `maximum_corpus_chars` | 기본 100,000자, 허용 1,000~250,000; 조용히 자르지 않음 |
| `maximum_passages` | 기본 100개, 허용 1~500; 전체 문단 예산 |
| `owner_source_sha256` | 검토한 원 소스/자원 pin; 기본 null은 native 호출 전 보류 |

검토 키는 `source_rights_and_permitted_use`, `current_version_and_period`,
`asset_clock_and_domain_applicability`, `source_locator_and_fidelity`다.
`True`와 그 검토의 근거가 모두 필요하다. 선언된 권리 검토를 실제 법률 감사로
승격하지 않으며, 저작권 조건이 미상인 문서를 단지 검색 가능하다는 이유로 허용하지 않는다.

`KnowledgeSource` 필드는 다음과 같다.

| 필드 | 의미·제약 |
|---|---|
| `source_id`, `source_version` | 문서와 판의 식별; 버전 null은 보류 |
| `document_code`, `title`, `source_uri` | 원 인용의 문서 코드·제목·위치; 같은 코드의 다른 판을 한 요청에 혼합하지 않음 |
| `license_id`, `rights_statement` | 권리 식별자와 검토한 허용 조건; null은 보류 |
| `asset_id`, `domain_id`, `origin` | 적용 설비·분야·synthetic/literature/user_declared 기원 |
| `permitted_operations` | 허용한 방법 목록; 이번 요청 방법이 없으면 보류 |
| `valid_from_utc`, `valid_until_utc` | timezone-aware 양의 유효기간; 어느 한쪽 null이면 보류 |
| `passages` | 최대 100개 `KnowledgePassage`; 빈 문서 불가 |
| `content_sha256` | `digest(passages)`; null은 보류, 불일치는 오류 |

문단에는 `passage_id`, 정수 `page>=1`, `hierarchy`, 원문 `locator`,
`text`(1~12,000자)가 있다. 페이지는 PDF 추출을 했다는 뜻이 아니다. 이 실행은
독립 작성 문단의 위치 표기다. 자료를 PDF 페이지로 바꾼 경우에는 별도 추출·위치 검토가 필요하다.

`KnowledgeCandidate`의 `candidate_id`, `source_id`, `passage_id`, `exact_quote`는
근거에 결합된다. exact_quote는 지정 문단의 연속 원문이어야 한다.
`tag_id`, `variable`, `threshold_type`(STATIC/DELTA_START/DELTA_PREV),
`threshold_value`, `unit`, `compare_dir`, `rationale`는 caller 선언을 보존한다.
숫자 0은 누락이 아니며 null은 미상이다. False·문자열 숫자·NaN·Inf는 수치로 허용하지
않는다. `compare_dir="High"`는 원 검토 API가 준비도 공백으로 돌려준다.
후보 수치의 단위는 caller가 Pa/K/m/s 등 SI로 명시한다. Pa의 절대·상대·차압 의미는
variable/원문/근거에 명시해야 하며 이 어댑터는 상대압을 절대압으로 자동 변환하지 않는다.
원 HAZOP 스키마의 일반 단위 문자열 지원을 물리 단위 적합성 검증으로 세지 않는다.

## 결과와 client 교체

결과는 `pipeline_integrity.knowledge_exchange.v1`이다. request와 context 원문,
각 digest, status, authority, notes, native_receipt, receipt_digest가 있다.
원 receipt는 `saga.offline_exchange.v1`, 요청/context digest, status,
owner_version, schema_digests, loaded_modules, runtime, source_pin을 담는다.
source_pin의 before/after는 원 호출이 끝난 뒤에만 채워 직렬화한다.
원 출처·소스·라이선스·자원의 전후 해시와 실제 import 파일의 소속을 확인한다.

`citations`는 원 Citation의 number/document_id/chunk_id/doc_type/doc_code/title/
hierarchy/page/filename/excerpt/score/source_url을 보존하고 source_id/version/
sha256/license_id/passage_id/locator를 추가한다. 본문·위치·판·권리의 변경을
결과 검증기가 거부한다. 점수는 원 검색 순위의 척도이며 관련성 확률·신뢰도·위험도와 다르다.
원 인용 생성기의 제한된 excerpt를 전체 원문이나 문헌의 완전한 적용 범위로 취급하지 않는다.

`candidate_audit`는 원 준비도 응답, `rule_candidates`는 요청 후보 원본,
`threshold_proposal`는 실제 원 무서비스 응답이다. `native_request`에 원 HAZOP/Chat
스키마로 전달한 형태를 보존한다. 후보 저장 시 confidence는 항상 pending이다.
원 ready가 향후 true가 되더라도 운전 승인 권한은 별도다.

후보의 원 HAZOP 투영에서 순번·scenario·tag·변수·단위·threshold type/value·비교 방향·
설명·출처·pending·저장 경로 식별자를 입력과 대조한다. 원 스키마가 추가한 기본 필드는
그대로 남기되, caller가 제공한 필드는 생략하거나 다른 값으로 바꾸지 못한다.
canonical JSON 비교로 null·숫자 0·bool False를 구분한다. 준비도 응답도 같은 scenario,
ready 0건과 미준비 후보 수를 유지해야 한다. proposal의 설비·시나리오·태그·질문과
명시한 빈 변수/단위·미선택 모델도 원 요청과 결합한다. reasoning_executed=false인
proposal은 ready=false·빈 candidates·needs_data여야 하며 사람 검토 요구를 유지한다.
바깥 receipt 해시를 다시 계산했더라도 이 의미 검사는 생략하지 않는다.

`KnowledgeClient.run(payload) -> dict`를 구현하면 공급자를 교체할 수 있다.
payload는 strict request/context와 각 digest이며 변경하면 오류다. 이 계약이 외부
서비스 사용 권한을 부여하지 않는다. 현재 실행 정책은 네트워크 호출 0의 offline 경계다.
로컬 추론을 연결하는 client도 같은 source pin, 인용·권한·스키마·버전 계약을 반환해야 한다.
완성 답변을 반환하려면 reasoning_executed=true, answer, reasoning_service의 id/version,
bounded claims의 claim/citation_number/exact_quote, answer_review의 approved가 필요하다.
claim은 실제 인용에 결합돼야 한다. 기계 AnswerReview.approved는 사람의 규칙 승인과 다르다.
온라인 provider는 명시적인 별도 권한·정보 공개·예산 정책을 확장한 다음 연결해야 한다.
이 단계에서는 그 provider나 모델 API를 호출하지 않았다.

모든 결과의 authority는 advisory, requires_human_review=true,
approved_rule=false, control_command=false, legal_determination=false다.
답변·후보 수치를 경보 설정·작업 지시·제어 명령에 자동 전달하는 함수는 없다.

`verify_knowledge_replay(record, client=...)`는 구조 검증 후 동일 request/context와 공급자를 다시 호출하고 전체 receipt digest가 정확히 일치하는지 확인한다. 이 호출은 provider를 실제 재실행하므로 caller가 승인한 결정적 offline client에만 사용한다. 확률적 추론이나 변경된 corpus/model/provider 버전에서는 결과가 달라질 수 있다. replay 실패와 과거 receipt의 구조적 무결성은 별개로 기록한다.
미실행 receipt는 provider 없이만 재생하고, native receipt가 있는 실행 건은 명시적인 replay client를 요구한다. 따라서 잘못 전달된 client가 미실행 기록을 검증하다가 새 provider 호출을 일으키는 일을 차단한다.

## 재현

메인 패키지 환경과 SAGA import 의존성을 분리했다. 다음은 검증한 별도 환경
버전이다. `SAGA_SOURCE`는 적법하게 확보한 원 소스 위치이며 공개 배포에 포함하지 않는다.

```powershell
python -m venv PATH_TO_KNOWLEDGE_ENV
PATH_TO_KNOWLEDGE_ENV\Scripts\python.exe -m pip install pydantic==2.14.1 pydantic-settings==2.16.1 openai==2.54.0 pymupdf==1.28.2
python scripts/check_knowledge_exchange.py --saga SAGA_SOURCE --python PATH_TO_KNOWLEDGE_ENV\Scripts\python.exe
python -m pytest -q tests/test_knowledge_exchange.py
python -m ruff check src/pipeline_integrity/knowledge_exchange.py src/pipeline_integrity/knowledge_worker.py tests/test_knowledge_exchange.py examples/knowledge_exchange.py scripts/check_knowledge_exchange.py
python -m ruff format --check src/pipeline_integrity/knowledge_exchange.py src/pipeline_integrity/knowledge_worker.py tests/test_knowledge_exchange.py examples/knowledge_exchange.py scripts/check_knowledge_exchange.py
```

다음 Python 예시는 실제 실행한 문서 예제다. 환경키나 credential은 읽거나 출력하지 않는다.

```python
import runpy
from pipeline_integrity.knowledge_exchange import evaluate_knowledge, verify_knowledge_record, verify_knowledge_replay

requests, context = runpy.run_path("examples/knowledge_exchange.py")["make_cases"]()
record = evaluate_knowledge(requests["retrieve"], context)
assert record["status"] == "needs_data"
assert record["native_receipt"] is None
assert verify_knowledge_record(record)
assert verify_knowledge_replay(record)
assert record["authority"]["approved_rule"] is False
```

독립 작성 MIT corpus는 2문서·4문단이며 원문·DB·상세 요청/응답은
`artifacts/knowledge_exchange`에만 생성한다. 입력 corpus의 이용 권한과 허용
작업을 별도로 선언한다. 원본 전체 파일과 원 프로젝트 DB의
실행 전후 해시/존재 상태도 바뀌지 않았다. 공개 research에는 집계·단위·스키마와
소스/권리/자체 파일 해시·한계만 기록한다. 상세 답변·관측·입력 corpus는 배포에서 제외한다.

실행은 native 8요청, native 전 보류 2요청, computed 6/needs_data 4다.
작성한 계약 기준 28개와 실제 receipt 의미 오염 33개 거부를 확인한다.
검색 범위 filter, 원 인용 권리/판/locator, 미검토 보류, 후보 0/null/High,
pending 유지와 미서비스 반환, 원 요청 투영·단위·시나리오·준비도 변조 거부를 확인했다.
이는 실제 안전 지식의 정확도 지표가 아니다. 집중 pytest는 63개이며 최신 근거 수와
파일 해시는 `knowledge_exchange_results.json`을 따른다.
등록 계산 개수나 전체 완료율을 이 문서에서 다시 추정하지 않는다.

## 실패와 후속 PI-X07

처음에는 Windows isolated Python이 UTF-8 환경변수를 무시해 한국어 경로 오류를
stderr에서 읽는 데 실패했다. 명령에 `-X utf8`을 명시하고 private stderr 바이트를
보존했다. 다음에는 네트워크 차단이 Windows asyncio의 내부 wake-up socket 생성도
막았다. 이벤트 루프의 내부 통신 경로를 먼저 만든 뒤 이후 socket 연결을 차단해
원 무서비스 제안을 실행했다. 외부 LLM/웹/API/인간 메시지는 호출하지 않았다.

PI-X07의 수신자인 SAGA에 다음 사항을 요청한다. 안정된 버전의 독립 library API에서
검색 요청·출처 식별·corpus 버전·허용 source·unit/context와 reasoning execution을
한 응답에 명시해 달라. package/project/API 버전 불일치를 정리하고 정적 UI 없는
설치에서도 정식 API 서버를 import할 수 있게 해 달라. 인용 원문·페이지를 수리하거나
정리한 경우 원 locator/원문 digest와 변환 계보를 함께 반환해 달라.

후보 생성·기계 검토·사람 승인·운전 반영을 별도 상태/권한으로 제공하고, 숫자/단위/
적용 분야가 미확정이면 needs_data를 보존해 달라. source allowlist·명시 기간·문헌 권리와
asset/clock이 추론 서비스로도 전달됐음을 확인할 계약이 필요하다. 외부 provider의
승인된 실행 예산·정보 전달·모델/프롬프트/서비스 버전·실패·재시도·취소 정책도
현재 offline 계약과 분리해 명시해 달라. 실제 corpus와 독립 작성 평가질문, 한국어/영어
인용 충실도·누락·틀린 근거·정정/폐지 판·자료 미상 사례를 검증한 다음 자격을 다시 판단한다.
서비스 미확인 경로를 mock 답변으로 대체해 완료했다고 표시하지 않는다.
