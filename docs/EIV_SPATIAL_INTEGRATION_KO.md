# 교정·적산·공간 위험의 통합 기록

2026년 10월 27일 단계다. 전체 기술 목록201개 중 구현35·인터페이스 확인99,
합계134/201(66.7%)이다. 부분19·계획19·현장 절차29는 분자에서 제외한다.
등록 계산24개와 별도로 공통 계약을 사용하는 서비스를 확장했다. 전체2197검사,
실패/생략0, JUnit155.606초를 확인했다. 이 비율은 동일 가중치의 범위 집계이며
공수·기간·현장 자격 완료율의 추정치가 아니다.

## 교체 가능한 API와 기존 계산의 재사용

| 경계 | 공개 진입점 | 공유하는 계산과 문맥 |
|---|---|---|
| 교정 | `fit_calibration`, `predict_calibration`, `verify_calibration_record` | 사용자가 제공하는 함수/미분·단위·영역, 기존 SciPy 최소제곱 경계, 전체 공분산 |
| 자료 판독 | `calibration_data`의 판독/요청 팩토리 | 체크섬으로 고정한 원자료, 실제 행 범위, 실험/변환 추정값 구분 |
| 적산 | `totalize_measurements`, `verify_totalization_record` | 공통 측정 기반·공분산 전파·선형 구간 적분, 시계/포트/원관측 의미 |
| 실제 계수 구간 | `vsl_counter_period_cases` | 기존 계량 자료 판독과 source replay, 원 회차별 q와 counter 기간 |
| 공간 위험 | `evaluate_spatial_risk`, `verify_spatial_risk_record`, `to_signal_frames` | 실제 PRISM 위해/연간율과 HRA 사건·정수 사망자 분포, 원 시설/자산/시계 |
| JSON 경계 | `Contract`, `json_value` | 유한 수치와 미상/invalid 구분, 원 소유 Pydantic 객체 불변 |

설치·입력 예제·정확한 필드와 한계는 [교정](EIV_CALIBRATION_KO.md),
[교정 자료](CALIBRATION_DATA_KO.md), [적산](TOTALIZATION_KO.md),
[공간 위험](SPATIAL_RISK_KO.md), [JSON](JSON_BOUNDARY_KO.md)를 따른다.
입력과 문맥을 고정한 receipt는 현재 엔진으로 재실행해 검사한다. 출력 해시를
다시 만드는 것만으로 오염을 통과시킬 수 없으며 전자서명·과거 실행 인증을 대신하지 않는다.

## 실제 자료와 수치 확인

공개 자료는46후보·34자료군·226파일·3,563,387,112바이트다. 이번에 EMUE 소닉 노즐
ZIP1,406,894바이트를 직접 확보해 공급자 MD5와 로컬 SHA-256을 확인했다.
CC-BY-4.0의 저자·DOI·버전·권리 근거는 [자료 집계](../research/calibration_source_results.json)와
[제3자 고지](../THIRD_PARTY_NOTICES.md)에 남겼다. 원 프로그램은 실행하지 않았다.

EMUE의 실험 Type-A 30점과 공급자 Monte Carlo 변환값/전체60×60공분산을 분리했다.
점과 축 사이의 상관을 모두 보존한 적합은 공급자가 반올림해 제시한 계수·표준
불확실도·공분산5개를 재현했다. offset0.9966305635, slope−3.5267395836,
계수 표준불확실도 약0.0009607332와0.0482652337이다. 한 캠페인의 적합 재현이며
보지 않은 별도 실험이나 현장 정확도의 검증으로 세지 않는다.

VSL 실제274회차 중224개에는 지시 유량과 counter 기간이 함께 있어 명목 구간량을
계산했다. 질량160·지역 액체 체적64회차이며 counter 기간 없는50회차는 채우지
않았다. 기간은 각 회차의 지역0..τ로 정의했으며 달력시각이나 기준계의 관측창으로
바꾸지 않았다. 읽음값/기간의 공동 공분산은 없으므로274개 모두 최종 상태는
needs_data다. 명목값224개를 불확실도까지 확인한 완성 계량으로 표시하지 않는다.

공간 위험은 기존 PRISM/HRA 코드를 실제 호출했다. 고정 위치 연간율, 재실자 연간율,
기대 사망자/년을 별도로 보존하고 재실률은 한 번 적용한다. 정수 사망자 F-N에는
각 셀의 주변 평균과 일치하는 명시적 결합 법칙이 필요하다. 같은 주변 평균이어도
의존성에 따라 F-N이 달라지는 반례를 유지한다. PRISM의 기대 사망자 문턱 곡선을
정수 사망자 exceedance로 바꾸지 않는다. 실제 원 결과의 미계산 풍향은 partial과
None으로 남기며 위험0으로 보정하지 않는다.

## 발견한 결함과 수정

병렬 구현에서 기존 물 관망/SCADA driver와 새 교정 driver의 이름이 충돌했다.
기존 `scripts/check_calibration.py`를 이전 검증 source 배포와 바이트 동일하게
복구하고 새 실행을 `scripts/check_eiv_calibration.py`로 분리했다. 기존 가이드와
연구 기록도 확인했다. 공유 inverse hook 변경 후 SCADA51회 전방 호출을 다시
실행했고, 전력/적산이 공유하는 quadrature 변경 후 생애주기 실제24000표본도 다시
실행해 각각의 현재 소스 핀을 갱신했다. 외부 소유 엔진은 수정하지 않았다.

독립 검토는 양 끝이 같은 극소 수인 표현 가능한 적분이 먼저 반으로 나누는 과정에서
소실되는 반례, 큰 시계 offset 때문에 서로 다른 시간이 같아지는 반례, 외부 JSON
전용 변환기가 NaN을 null로 숨기는 반례를 재현했다. 지수 분해 기반 구간 적분,
시간 간격 변형 허용오차, 원 필드와 변환 결과의 유한 검사로 수정했다. 참0과
수치 underflow, 원래 None과 잘못된 NaN을 구분한다. 외부 소유 모델의 스키마나
serializer를 전역 변경하지 않는다.

공간 경계의 초기 독립 공격은 원 요청은 그대로 두고 ignition/harm을 일관되게
변경하거나, 시설 높이/유체/포트를 바꾸거나, 실패·영역 절단 플래그를 성공으로
승격한 기록을 재해시했을 때 통과하는 결함을 찾았다. 현재는 원 typed 요청을
재구성해 전체 비교하고 시설/원 payload digest·지원 영역·실행/자격 상태를 확인한다.
원 열/압력 원시량에서 소유 라이브러리의 위해 함수를 다시 실행해 장부와 최종
위해를 비교한다. legacy flat import는 격리된 작은 프로세스에 두었다.
이 재검사는 원시량→위해 단계이며 전체 geometry/dispersion/fire 해석 재생은 아니다.

## 검증의 종류와 재현 순서

| 항목 | pytest 추가 | 가이드/별도 실행 증거 |
|---|---:|---|
| 교정 | 53 | 분류된77기준, 독립 재검토27기준, 재해시7오염 거부 |
| 교정 자료 | 35 | 판독26기준, 출처 오염12거부, 실제 적합의 수치/정책11기준 |
| 공통 JSON | 33 | 외부 JSON-only 변환·원 객체 불변·순환/공유값·정상 스키마/날짜 |
| 공간 위험 | 59 | 분류된78기준, 재해시27거부, 최종 동일 소스 독립22기준과 추가9기준 |
| 적산 | 40 | 작성/실제 자료13기준, 독립21기준, 재해시5거부 |
| 합계 | 220 | 기존1977개와 함께 전체2197개 통과 |

여기에는 수치 기준·계약·출처·상태·재실행 확인이 섞인다. 서로 겹치는 기준을
합산해 독립 물리 관측 수로 해석하지 않는다. 기존 upstream FiPy 경고2건은
전체 원 로그에 보존했다. 버전은 [환경 기록](../research/tested_environment.json),
소스 핀은 각 research 집계를 따른다.

```powershell
python scripts/acquire_data.py --only emue_sonic_nozzle_calibration
python scripts/review_calibration_source.py --raw-root data/raw --fit-source
python scripts/check_eiv_calibration.py
python scripts/check_eiv_independent.py
python scripts/check_totalization.py --raw-root data/raw
python scripts/check_totalization_independent.py
python scripts/check_spatial_risk.py --prism PATH_TO_LH2_TNO --hra PATH_TO_HRA --consequence-directory PATH_TO_REGENERATED_CONSEQUENCE_RECORDS
python -m pytest -q --junitxml=artifacts/verification/tests.xml
python scripts/export_verification.py
python scripts/build_research_index.py
```

원 사고 결과가 없을 때 `--consequence-directory`를 생략한 실행과 원 결과 적용을
구분한다. 소유 엔진 설치/버전·원 결과 생성 순서는 각 가이드를 따른다.
새 EIV 실행과 기존 SCADA `check_calibration.py`의 입력/기록은 별개다.
독립 [교정](../research/eiv_independent_review.json),
[적산](../research/totalization_independent_review.json),
[공간](../research/spatial_independent_review.json) 집계를 함께 확인한다.

선택적 odrpack0.7.0은 별도 독립 비교용이다. 전체 상관을 한 벡터 관측으로 묶은
ODR 실험은 native info10010으로 실패해 생산 경로로 채택하지 않았다. 생산 경로는
기존 SciPy 최소제곱으로 전체 공동 오차를 whitening한다. 임의 독립화·행 복제·
공분산 수리를 하지 않는다. NumPy/SciPy/물성 버전은 유지했다. 참조 wheel 안의
컴파일러 런타임은 Python wrapper와 별도 권리 조건이며 바이너리는 배포하지 않는다.

## 다음 연구와 배포 경계

거래 계량은 partial로 유지한다. 일반 특이 공분산·정확한 y, 신규 관측에 교정을
적용한 전체 불확실도, 독립 캠페인·환경 drift·계수기 reset/wrap·64좌표 초과 공동
공분산·규정별 판정/승인 절차가 남는다. 교정 예측은 지원 범위 안의 명목값만
제공하며 입력 오차를0으로 가정한 불확실도를 만들지 않는다.

공간 위험의 실제 인구/좌표 변환·현장 의존성·연속 등고선·전체 사고장 재생도 남는다.
분수 people/pixel인 WorldPop 자료를 정수 ENU 인구로 반올림하지 않았다.
필요한 원 엔진 확장은 [PI-X01/05와 PI-X02/03/06](EXTENSION_REQUESTS_KO.md)에 구체화했다.
고급 수송·최적화·구조/토양 연성과 다른 전체 기술 범위도 계속 추적한다.

원관측·다운로드·외부 엔진·상세 receipt·환경·개인 경로는 공개 코드 배포에서
제외한다. 출처/이용 조건·버전/체크섬·오류/제약·재현 절차는 유지한다. 공개용
설명과 주석은 독립 작성했으며 자료 고유의 출처와 필요한 저자 표시는 제거하지 않는다.
패키지 설치와 아카이브의 실제 구성 검사는 별도 core/release 기록으로 남긴다.

## 실제 최소 설치 확인

현재 wheel544,515바이트·패키지109파일을 핵심 의존성 전용 환경에
`--no-deps --force-reinstall`로 실제 설치했다. 선택 의존성30개가 모두 없는
상태에서 적산·유한 JSON 경계와 기존 핵심 계산을 실행했다. 미검토 교정은
SciPy/odrpack 없이, 미검토 공간 위험은 PRISM/HRA 없이 needs_data를 반환한다.
자료 모듈 import가 workbook/문서 라이브러리를 불러오지 않는 것도 확인했다.
설치한 wheel SHA-256은
`452c7b36504a35912c20e0edd6346a99c68358d3e4881419e8dfda564d43bdb8`이며
[실제 설치 기록](../research/core_installation.json)에 결속했다. workspace·wheel·
설치된109파일은 바이트 단위로 같았다. source 배포는 이 기록까지 포함해 만든다.
