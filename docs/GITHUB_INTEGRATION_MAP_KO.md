# lyullee GitHub 모듈 연동 지도

## 확인 범위

2026-10-29 GitHub public REST API의 계정 저장소 목록에서 공개 저장소 27개를 확인했다. 기계 판독 목록과 핵심 연동 저장소의 현재 `main` HEAD는 `research/github_repository_inventory.json`에 기록했다. `pipeline-integrity-toolkit`은 계정 목록에 없고 이 작업 폴더에도 git remote가 없다. 따라서 이 자료는 원격 checkout 전체를 현재 작업 상태라고 주장하지 않으며, push·release를 수행하지 않았다.

핵심 API 경로와 기존 실행 증거는 다음과 같다.

| 소유 저장소 | 기존 책임 | 이 toolkit의 재사용 경계 | 근거 및 남은 작업 |
|---|---|---|---|
| [human-reliability-toolkit](https://github.com/lyullee/human-reliability-toolkit) | 인적 수행·인지·시계·실패확률·기회분모·FTA/ETA/LOPA | `risk_exchange`, `response_exchange`, `period_risk`, `human_control_room_study`가 owner 계약과 engine을 호출한다. `human_collection_exchange`는 0.5.0의 `CollectionBundle` owner 계산을 재사용해 typed risk input으로 전달한다. HEP를 독립 구현하지 않는다. | 기존 실행 pin은 `6617d366…`이며 2026-10-29 public `main` `d2754bbe…`보다 7 commits 뒤다. collection adapter는 MIT upstream `d2754bbe…`를 선택 extra에서 고정하지만 새 인터페이스 회귀는 미실행이다. `docs/HUMAN_COLLECTION_EXCHANGE_KO.md`와 `tests/test_human_collection_exchange.py`를 따른다. |
| [process-monitoring-toolkit](https://github.com/lyullee/process-monitoring-toolkit) | 품질 mask·기준선·탐지기·알람/진단 | `monitoring_exchange`, source data reader와 모듈 adapter가 `SignalBatch`/monitor provider를 호출한다. 감지기를 복사하지 않는다. | `research/integration_results.json`, `research/monitoring_exchange_results.json`, `docs/INTEGRATION_KO.md`, `docs/EXTENSION_REQUESTS_KO.md`. 현재 public HEAD는 기존 source pin `79b4cf52…`와 일치한다. 최신 탐지 성능이 모든 배관 센서에 적합하다고 일반화하지 않는다. |
| [lh2dt_models](https://github.com/lyullee/lh2dt_models) | 수소/극저온 장치·P/h 상태·네트워크·유한 구동·재고방출 | `equipment_exchange`, `valve_motion`, `release_exchange`, `gas_network`가 owner 모델과 provider를 지연 호출한다. 범용 배관·시계·수지 계약만 이쪽에서 둔다. | `research/external_source_versions.json`, `research/physics_integration_results.json`, `research/release_exchange_results.json`, [PI-X01 요청](EXTENSION_REQUESTS_KO.md). `b2f27633…` pin은 현재 HEAD와 일치한다. 22종 장치 팩토리 존재가 22종 물리 검증을 뜻하지 않는다. |
| [hydrogen-station-sim](https://github.com/lyullee/hydrogen-station-sim) | 충전소 장치·제어·센서/HAZOP·훈련 시나리오 | `control_exchange`, `alarm_exchange`, `sensor_placement`, source adapters를 통해 경계/monitoring/HRA에 연결한다. 충전소 내부 제어기를 복제하지 않는다. | `research/control_exchange_results.json`, `docs/CONTROL_EXCHANGE_KO.md`, [PI-X01 요청](EXTENSION_REQUESTS_KO.md). public HEAD `9f8fa941…`은 공개 저장소 metadata에서 확인했으며 현재 독립 owner execution을 이 manifest에 넣지는 않았다. |
| [prism](https://github.com/lyullee/prism) | 사고사건·방출·조건부 위험·연간/공간 결과 | `risk_exchange`, `release_exchange`, `spatial_risk`, `consequence_exchange`가 PRISM 계약으로 risk/consequence를 전달한다. 사건 빈도나 인적 오류를 현장 빈도로 임의 변환하지 않는다. | `research/risk_exchange_results.json`, `research/release_exchange_results.json`, `research/consequence_results.json`, [PI-X02 요청](EXTENSION_REQUESTS_KO.md). public source `884b0553…`와 기존 실행 pin이 일치한다. |
| [process-risk-consequence-toolkit](https://github.com/lyullee/process-risk-consequence-toolkit) | 방출·분산·화재·폭발·노출 영향 | `release_exchange`/`consequence_exchange`가 패킷 경계를 유지한다. 결과 engine을 이 toolkit 안에 중복 구현하지 않는다. | public API metadata에서 license Apache-2.0, current HEAD `4106da00…`을 확인했다. 이 specific HEAD의 실제 round-trip은 별도 소스/source-map에서 고정하기 전까지 완료로 간주하지 않는다. |
| [api581-risk-analysis](https://github.com/lyullee/api581-risk-analysis) | API-RP-581 기반 검사계획 위험연구 코드 | 공통 검사 계획·자료/수명 경계만 이쪽에 제공하며 소유 계산과 standards source를 복제하지 않는다. | public metadata는 SPDX `NOASSERTION`을 반환한다. 표준 원문·표·기준을 이 toolkit에 복사하지 않으며 소스 재사용 전에 별도 권리 검토를 한다. 현재 연결·허용 기준은 `inspection_planning`, `inspection_profile_bridge`, `docs/INSPECTION_PLANNING_KO.md`를 따른다. |

## 전체 계정 범위

계정에는 위 7개 핵심 Python 엔진 외에도 아래 프로젝트가 있다. 이 목록은 기능 중복을 피하고 caller 선택지를 찾는 지도이며 모든 저장소를 이 toolkit의 dependency로 추가한다는 뜻은 아니다.

- 배관/공정·위험 후보: `ammonia-supply-risk`, `pifira`, `DynamicLH2PoolX`, `lh2poolx`, `lh2-psv-tno-risk`, `prism-dynasense`.
- 방출/확산·열·물성: `slabx`, `slabx-lh2`, `degadisx`, `degali`.
- 자료·분리·경제: `psa-dyna`, `adm1-tea-lca`.
- 안전 지식 UI: `saga-py`, 과거 별도 구현 `saga-sts`.
- 양자/재료·방법 연구: `qsafety-qae`, `psa-qae-resource-audit`, `hydrogen-local-energetics`, `nbcp-magnetocaloric-quantum-spectral-flow`.
- 공개 계정 포트폴리오: `lyullee`, `lyullee.github.io`.

repository license metadata는 각각의 저장소/API에서 읽어야 한다. 이 inventory는 선택 라이선스 의무나 법률 검토를 대체하지 않는다. 예를 들어 [SLABx](https://github.com/lyullee/slabx), [DEGADISx](https://github.com/lyullee/degadisx), [DEGALI](https://github.com/lyullee/degali), `api581-risk-analysis`의 repository API는 SPDX `NOASSERTION`을 표시한다. 이 프로젝트에는 해당 저장소 코드를 복사하지 않고 기존 owner adapter 계약만 보존한다.

## 변경 및 검증 절차

owner source에 변동이 있으면 `external_source_versions.json`의 main HEAD만 바꾸지 말고 release/version, source-tree SHA, adapter call inputs, raw responses, failure behavior와 기존 reference cases를 함께 갱신한다. 새로운 interface는 synthetic unit fixture뿐 아니라 frozen source snapshot round-trip 및 실패/unsupported boundary를 확인한다. 물리·수치·field claims는 owner release evidence와 독립 확인이 확보될 때까지 해당 범위를 넘어 확장하지 않는다.

현재 HRA 0.5.0의 새 data collection, missingness bounds, cluster validation 등은 유용한 upstream 후보지만 이 toolkit의 caller-reviewed `HumanControlRoomOutcomeMap`을 대체하지 않는다. 우선 upstream schema와 기존 `InformationResponseStudy`/FTA adapter 사이의 실제 data contract를 검토하고, 필요하면 [확장요청](EXTENSION_REQUESTS_KO.md)에 HRA owner 수정 요청을 별도 추가한다. HRA raw workbook의 표본 구성, outcome semantics, event clock이 검토되기 전에는 새 HEP dataset을 만들지 않는다.

### HRA 0.5 수집 API와 공개 실험자료의 경계

2026-10-29 upstream `main`의 `docs/API.md`, `CHANGELOG.md`, `collection.py`를 확인했다. v0.5의 `CollectionProtocol`, `CoverageWindow`, `RegisteredDemand`, `CodedOutcome`, `CollectionBundle` 및 `validate_collection`은 등록 기회, 관측 창, 완전성 대조, coding/adjudication, 시간 기준, 집단 ID와 미관측 결과를 요구한다. `CollectionBundle.hep()`도 reconciled denominator와 명시적 prior·dependence/missingness 검토를 전제로 한다. 공개 API와 변경 요약은 [HRA API 안내](https://github.com/lyullee/human-reliability-toolkit/blob/main/docs/API.md), [v0.5 변경 기록](https://github.com/lyullee/human-reliability-toolkit/blob/main/CHANGELOG.md)에서 확인한다.

현재 `human_control_room_study`는 승인된 결과 필드의 행 수를 scenario별로 익명 집계하고 participant 행을 반환하지 않는다. 따라서 HRA 0.5 `CollectionBundle`에 필요한 기회별 ID·window·시작/종료 시각·group·누락 demand register·독립 outcome adjudication을 재구성할 수 없다. 원 workbook의 participant 총수 reconciliation도 미해결이며 이 표본은 simulator 기반이다. 그러므로 현 자료를 `CollectionBundle.hep()`에 직접 넣거나 HEP·산업 현장 분모·FTA 사건률로 변환하는 어댑터는 두지 않는다. 새 bridge는 적격한 기회 수준 자료와 clock/coverage/adjudication 근거가 확보되고, simulator population으로 제한한 별도 protocol이 검토된 후 설계한다. 현재의 유일한 대응은 집계 adapter를 유지하면서 이 경계와 provenance를 보존하는 것이다.
