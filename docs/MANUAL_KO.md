> 최신 사용자 안내는 [상세 사용 설명서](USER_GUIDE_KO.md), 현재223개 범위는 [카탈로그](MODEL_CATALOG_KO.md)를 따른다. 아래는 개발 중 작성된 기존 매뉴얼이다.

# 사용 매뉴얼

## 1. 환경 선택

저장소 루트에서 Python 3.12 이상으로 가상 환경을 만든다. README의 전체 설치
명령은 개발·자료 판독·CoolProp·물 관망에 필요한 선택 의존성을 함께 설치한다.
자동 설치되는 외부 응용 프로젝트는 없다. 핵심만 쓰려면 `pip install .`,
수력 상관식에는 `pip install ".[engineering]"`, 물성에는 `pip install ".[thermo]"`,
자료에는 `[data]`, 관망에는 `[water]`를 추가한다.

`requirements-tested.txt`는 실행 확인한 직접 의존성의 제약 파일이다. 전이
의존성 전체를 고정한 환경 잠금 파일은 아니다. `research/tested_environment.json`
에 기록한 Python·패키지 버전과 변경 환경의 버전을 함께 보관한다.

## 2. 계산 찾기와 입력 준비

`pipeline-integrity models`는 현재 등록된 24개 계산 모델의 단위·가정·의존성을
보여 준다. `pipeline-integrity capabilities`는 계획·외부 소유 기능을 포함한
전체 기술 목록이다. 두 목록의 크기는 서로 다른 의미다.

`pipeline-integrity describe MODEL_ID`로 입력 JSON Schema를 확인한다. 모든
계산 입력은 SI를 기본으로 하며 압력은 필드 이름에 절대압 여부를 명시한다.
숫자를 문자열로 입력하거나 미지의 값을 0으로 채우지 않는다. Python API의
튜플 필드는 튜플로 공급하고, JSON에서는 배열을 사용한다.

`RunContext`에는 실제 설비 ID, 시나리오, 시계와 자료 생성 방식을 지정한다.
사용자가 가정한 값은 `user_declared`, 예제 값은 `synthetic`으로 표시한다.
실험과 실제 현장 측정, 시뮬레이션을 구분해 근거를 추가한다.

## 3. 실행과 결과 판단

```powershell
pipeline-integrity evaluate examples/liquid_loss.json --output artifacts/my_result.json
pipeline-integrity workflow examples/pump_energy_workflow.json --output artifacts/my_workflow.json
```

`computed`는 가정 안에서 계산이 완료됐다는 뜻이다. `out_of_scope`는 현재
모델이 지원하지 않는 상태이며, `needs_data`는 필요한 근거·입력이 부족한
경우다. 미완료 부모 계산을 참조하는 작업 단계는 `withheld`로 남는다.
입력 오류는 예외 또는 CLI 종료코드 2로 전달한다.

계산 값은 `values`, 단위는 `output_units`, 가정과 유의점은 `assumptions`와
`notes`에 있다. 원본 입력·실행 버전·근거·해시와 함께 JSON으로 저장한다.
반환 객체는 최상위 필드 변경을 막지만 내부 dict의 완전한 불변성을 보장하지
않는다. 저장한 결과를 변경할 때는 새 실행·검토 기록으로 관리한다.

현장 수치 기준은 `DeclaredRequirement`로 따로 선언한다. 모델의 상태와
요구조건 만족 여부를 섞지 않는다. 하나의 기준을 만족해도 전체 설계 규격
준수로 확대하지 않는다.

## 4. 자료 취득과 실제 적용

```powershell
pipeline-integrity datasets
python scripts/acquire_data.py --only hydraulic battledim ili
python scripts/apply_data.py --raw-root data/raw
python scripts/acquire_data.py --only elvhys_bleve h2safe --workers 1
python scripts/review_hydrogen_data.py
```

이미 확보한 파일은 체크섬·크기를 검사해 재사용한다. 서버가 원본을 바꾸면
기존 파일을 조용히 덮어쓰지 않고 실패한다. 각 요청에 최대 실행시간과
파일 크기 한도가 있다. 원본·부분 다운로드·취득 중 원장은 Git에서 제외된다.
캐시를 재사용하려면 명시한 `--cache-root` 아래의 카탈로그 경로와 해시가
일치해야 한다. 검토를 마치지 않은 자료는 취득하지 않는다.

`scripts/apply_data.py`는 BattLeDIM의 첫 864개 표본과 관망 1일, 유압설비
한 실험 주기, ILI 엑셀 파일 4개의 구조를 확인한다. 이는 전체 자료 학습이나
독립 검증이 아니다. 대용량 파일을 전체 처리하려면 [데이터 문서](DATA_KO.md)의
실험·시계·자료 분할 원칙을 적용한다.

## 5. 커스터마이징

물 성분 수송은 `pipeline-integrity water-quality examples/water_quality.json
--output artifacts/quality.json`으로 실행한다. `mode`에 따라 농도·평균 물 나이·
지정 노드 출처 비율을 계산한다. 주입과 초기 상태·반응·단위·시계·예산은
[성분 수송 문서](WATER_QUALITY_KO.md)에 있다. 물성 피드백이 있는 제품 혼합으로
확대 해석하지 않으며 수력 경고가 남은 결과는 모니터링 전달을 보류한다.

반복 ILI는 `python scripts/apply_ili.py --materials PATH_TO_MAT --joints 14 --reuse-scans`로
전체 읽기와 선택 용접부의 후보 정합을 재현한다. 전체 데이터 행 수와 정합
성공 수는 다르다. 관측에 없는 시각·계측 오차·동일 결함 검토를 가정해 성장률을
계산하지 않는다. 자체 자료는 [반복 검사 계약](INSPECTION_KO.md)으로 공급한다.

추가 수소 자료는 판독 성공과 물리 입력 적합성을 구분한다. 시각 반복·압력
기준 불일치·신호 단위 누락은 검토 기록에 남긴다. 실험별 시계를 임의로
합치거나 좌표를 ENU로 간주하지 않는다. 독립 검증 전에 단위·기준 상태·
실험 조건·상/구동기 모델의 적용 범위를 확인한다.

단순 조건 변경은 입력 JSON만 바꾼다. 손실계수, 효율, 거칠기, 제어밸브 계수,
검토 기준과 비용 계수는 입력 또는 사용자 공급자로 전달한다. 제조사 곡선이나
규격 표를 원 패키지에 넣지 않는다.

새 계산은 `examples/custom_model.py`처럼 입력 계약과 계산 함수를 등록한다.
`python examples/custom_model.py`로 실행한다. 기존 계산 교체는 명시적인
`engine.register(spec, replace=True)`로 한다. 모델 ID·버전·근거·검증 기록을
함께 갱신한다. 설정 파일로 임의 Python 코드를 불러오거나 실행하지 않는다.

물성 교체는 `create_registry(property_provider=provider, provider_id="site_eos",
provider_version="1")`로 한다. 공급자는 `StateRequest`를 받아
`ThermodynamicState`를 반환해야 한다. 사용자 공급자로 실행한 계산은
`user_model`로 기록한다. 기본 공급자는 CoolProp이다.

## 6. 다른 프로젝트와 사용

공개된 HRA와 모니터링 프로젝트를 각자의 설치 지침에 따라 설치한다.
`matintegrity`는 검증할 소스 체크아웃 또는 배포 패키지를 명시적으로 설치한다.
경로를 넘기는 연동 확인 예시는 [연동 문서](INTEGRATION_KO.md)에 있다.

외부 모듈의 오류·보류 상태를 이 패키지에서 정상 상태로 승격하지 않는다.
감지 점수는 HEP가 아니며 HEP는 연간 사고 빈도가 아니다. 감지·사람·밸브·방출·
결과를 연결할 때 시계, 확률의 조건과 이미 반영한 방호를 반드시 확인한다.

수소 동역학 엔진의 `dynamic_hem_pipe`를 만들었다면 `PipeAccidentRequest`로 사고
ID·시계·초기 상태·실패 개구·주변 상태·위치·방향·기간을 공급한다.
`client.export_accident_history(built, request)`의 결과가 exchange_checked일 때만
`prism_release(history)`로 기존 PRISM 패킷을 만든다. 코드와 전체 입력 예제는
`scripts/check_release_exchange.py`, 입력 필드는 [API](API_KO.md)에 있다.
withheld인 응답을 고쳐 전달하지 말고 issues와 PI-X01의 원본 문제를 확인한다.
반환 패킷과 함께 `handoff.record()` 및 원 이력을 보관해 시계·기준·근거를 유지한다.

## 7. 재현·배포

물리 방출에서 결과 계산으로 이어가려면 기존 `prism_release(history)`의
handoff와 `ConsequenceCase`를 `PRISMConsequenceClient.evaluate`에 넘긴다.
필요한 압축성 평면은 명시적으로 요청하고 원 source·물성·질량·시간 기준을
유지한다. 연간 환산은 동일 사건과 조건의 LOC 빈도를 별도로 공급한다.
보류된 기상 조건도 입력에 남겨 null과 미계산 범위를 보존한다. 실제 소스
준비·실행·설명은 [사고 결과](CONSEQUENCES_KO.md)에 있다.

기존 HRA 추정값은 `HRARiskClient.bind_human_estimate`로 조건·기회·방호를
연결한 뒤 FTA·ETA·LOPA 또는 요구 노출에 따른 연간 기대 횟수로 전달한다.
`python examples/risk_exchange.py --write-job artifacts/risk_job.json`은 자체
작성 FTA 입력을 만든다. HRA 별도 설치 후 `pipeline-integrity risk
artifacts/risk_job.json --output artifacts/risk_result.json`으로 실행한다.
PRISM까지의 실제 호출과 연구 한계는 [위험 연결](RISK_EXCHANGE_KO.md)을 따른다.
입력의 조건 ID와 방호 ID를 서로 다른 분석에서도 일관되게 공급해야 한다.

변경 후 `pytest`, `ruff`, 모든 계산 예제를 실행하고 배포 아카이브를 검사한다.
원본 자료가 없는 환경에서는 단위 검사와 예제 실행을 먼저 수행한다. 상세
순서는 [인수인계](CONTINUATION_KO.md)에 있다. 이 저장소를 공개할 때도
외부 데이터의 출처·라이선스 고지를 유지한다.

## 물리 파라미터 보정

`pipeline-integrity water-fit examples/water_calibration.json --output artifacts/fit.json`은
자체 작성 관의 관측과 알려진 파라미터를 사용한다. 사용자 자료는 Observations에
단위·valid·시계·출처 해시·잔차 척도를 명시한다. `WaterSensor`와 `WaterParameter`로
센서 위치와 수정할 속성을 지정한다. 다른 물리 엔진은 Prediction 공급자를
구현해 같은 추정 API를 사용할 수 있다. 자세한 설정은 [보정 문서](CALIBRATION_KO.md)에 있다.

공개 자료 적용의 fitted 상태를 현장 검증·모델 교체로 해석하지 않는다. 학습과
이후 시점의 오차를 따로 확인한다. 현재 고정 두 배율은 다음 날 오차를 키웠고
기본 모델에 반영하지 않았다.

## 두 센서 시간차

`python examples/acoustic_delay.py --write-job artifacts/acoustic_job.json`으로
합성 작업 파일을 만들고 `pipeline-integrity acoustic-delay artifacts/acoustic_job.json
--output artifacts/acoustic_result.json`으로 실행한다. 사용자 파형은 SampledTrace의
단위·시계·표본·valid·출처에 맞춘다. span을 null로 주면 지연만 계산한다.
정정 시간차나 실제 위치에 필요한 근거는 [음향 문서](ACOUSTICS_KO.md)를 따른다.
보류 결과도 파일에 저장되므로 명령의 종료만으로 물리 결과를 승인하지 않는다.

## 밸브 명령과 압력파

`pipeline-integrity liquid-transient examples/valve_transient.json --output
artifacts/valve_result.json`으로 저장된 유한 구동 액체 사례를 실행한다.
이 경로에는 engineering 의존성이 필요하며 기존 장치 엔진은 필요하지 않다.
원 구동기를 다시 실행해 명령·곡선을 바꾸려면 [밸브 문서](VALVE_TRANSIENT_KO.md)의
Python 예제와 외부 소스 경로를 사용한다. 저장된 원 궤적을 수정하고 해시만
유지한 입력은 거부한다. 목표 도달은 개도 관측이며 공정의 완전 차단 판정과
구분한다. 결과의 접점·밸브 잔차와 관별 수지, 적용 범위 상태를 함께 확인한다.

## 압력 이력으로 피로 평가

별도 재료 엔진을 설치하고 `pipeline-integrity pressure-fatigue
examples/pressure_fatigue.json --output artifacts/fatigue/result.json`으로
합성 기준을 실행한다. 사용자 입력은 압력의 절대/게이지 기준, 외부 절대압,
같은 시계의 벽 온도·valid, 평균 반경·두께·곡선의 적용 범위와 근거를 채운다.
결측 창을 임의 연결하지 않는다. 상세 재현과 원 엔진 경로는
[피로 문서](PRESSURE_FATIGUE_KO.md)에 있다. 손상은 실패확률이나 승인 판정이 아니다.

## 정상 기체 관망과 GasLib

기존 동역학 엔진과 그 의존성을 설치하고 `pipeline-integrity gas-network
examples/gas_network.json --output artifacts/gas_network/result.json`으로
선언한 메탄 사례를 실행한다. 유체·P/T 경계·인출·형상·관 구간·근거를 바꿀
수 있다. 입력 근거가 측정이어도 결과 기원은 simulation이다. 원 결과의
정적 엔탈피 장부를 전체 에너지 검증으로 읽지 않는다. 고도차·정체·영역
초과·미지원 물성의 보류와 [재현 설명](GAS_NETWORK_KO.md)을 함께 확인한다.

## 자원 일정 실행

scheduling 의존성을 설치한 뒤 `pipeline-integrity schedule
examples/operations_schedule.json --output artifacts/scheduling/result.json`으로
선언 예제를 실행한다. 작업·대체 방식·시간·모든 자원/달력·hold·소모품·비용과
근거를 입력한다. 모든 Operation은 필수이며 자원 구간은 [start, finish)다.
원 솔버의 OPTIMAL만 보지 않고 감사·정수 하한·proven_optimal을 함께 확인한다.
CLI 종료 0은 처리 성공이며 작업 승인이나 실행 가능성을 보장하지 않는다.
예제·HRA 교환·변형·그림과 한계는 [일정 문서](SCHEDULING_KO.md)에 있다.

## 인적 반응 평가

HUMAN_RESPONSE_KO와 examples/human_response.py의 전체 예제에서 시작한다. 기존
HRA 패키지를 별도 설치한 환경에서 `python examples/human_response.py`로 실행한다.
모집단과 근거를 준비한 후 인지 단계 또는 초기 오류 Estimate, 최종 HFE, 시계와
공정 한계, 시간 분포 또는 복구 조건을 입력한다. needs_data의 확률은 내보낼 수 없다.
실제 원 소스 재현은 `python scripts/check_human_response.py --hra PATH_TO_HRA`다.

## 검사 선택과 자원 계획

INSPECTION_PLANNING_KO와 examples/inspection_campaign.py를 읽고, 별도 MatIntegrity
및 scheduling 의존성을 설치한다. 위치별 가설·검출 자료·이번 계획의 메뉴·자원·
예산·점수 단위를 입력한다. 대상의 required가 true이면 예산이나 시간 때문에
불가능한 계획을 미선택으로 바꾸지 않는다. needs_data는 전체 비교가 불확정이라는
뜻이다. 실제 원 계산은 check_inspection_planning.py, 원 데이터 적용 조건과 가상
부분 문제의 제외 후보·범위는 research/inspection_planning_results.json을 확인한다.

## 공개 NDE 자료 사용

NDE_DATA의 취득·검토 명령으로 TUBERS 두 ZIP을 확보한다. 파형의 원 signed 시각과
결측을 유지한 뒤 같은 native_clock_id·원본 근거로 검토한 에코 구간을 지정한다.
음속·보정·각도와 가설의 근거를 넣어 원 Mat API에 전달한다. 데이터 파일명만으로
두께나 시편 정답을 만들지 않는다. 영상 대응 318쌍과 7개 고립 마스크를 함께
확인하고 전체 영상/독립 검출시험의 needs_data를 유지한다.

## 탱크 재고 사용

TANK_INVENTORY의 설치 명령과 examples/tank_inventory.py로 액위→체적·질량을
실행한다. 내부 치수·바닥 영점·액상/기상 상태를 각 근거와 공급한다. 체적 계산의
computed만으로 전체 질량을 승인하지 않고 mass_status도 확인한다. 실제 상태를
모르면 밀도를 생략해 체적만 얻을 수 있다. 사용자 보정표는 엄격한 단조성과
적용 범위를 지켜 공급하며 외삽하지 않는다. 공개 결합 탱크의 원 JSON·단위·
형상 공백은 자료 검토로 남기고 예제의 가정을 실제 교정값으로 사용하지 않는다.

## 제어·계측의 연구 실행

CONTROL_EXCHANGE와 examples/control_loop.py로 원 PID를 연결한다. 관측 axis의
압력 기준/영점과 단위를 맞추고 세 이득의 단위·최대 관측 나이·scan 간격·출력
정책을 명시한다. needs_data에서 반환된 이전/고정 명령도 장치의 안전 승인이나
실제 동작 완료가 아니다. 센서 지연·drift·시계 오류는 선언한 시나리오로 실행한다.
원 필터 입력에 미래 표본을 쓰지 않으며 미지 입력 구간은 보간하지 않는다.
원 탱크/밸브 폐루프 예제의 온도 오차와 공개 관측 shadow 명령의 한계를 함께 읽는다.

## 압력 단면과 고체 열 사용

MATERIAL_SECTIONS의 재현 명령과 examples/material_sections.py를 따른다. 원 재료
모델의 schema/version을 검토한 job으로 저장하고 입력 단위와 근거를 공급한다.
내압·외압은 각각 기준을 가진 Pressure로 받으며 미상은 needs_data다. 열전도
computed만 유효 simulation 프레임으로 내보낸다. 셀 평균·점·표면과 원통/평면을
구분하고 부적합 원 상태를 바꾸지 않는다. 원 봉재는 절대 온도/열유속 자료가
아니므로 excess와 counts를 유지하고, 가정한21점 경계의 계산을 현장 검증으로 쓰지 않는다.

## 균열 평가 사용

FRACTURE_EXCHANGE와 examples/fracture_screen.py로 Mode-I·성장·사용자 경계를 실행한다.
실제 K/형상·개구 하중·인성 정의/재료/온도·C 단위/보정·cycle 근거를 제공한다.
미상·미검토·arrest 인성은 개시 평가에 사용하지 않는다. 압력 총응력을 개구
응력으로 자동 취급하지 않고 검토한 scalar mapping과 crack-face 조건을 넣는다.
computed와 경계 만족을 따로 확인하고 calibration 범위 밖의 partial 궤적을
전 요청 수명으로 해석하지 않는다. 원 cycle index·filename 온도·원 force/displacement
파일은 자격 있는 물성/실제 운전 회수/기간 파손확률을 자동 생성하지 않는다.


## 지지·분기 프레임을 계산할 때

structural 선택 설치 후 examples/pipe_frame.py에서 시작한다. 지리 좌표를
그대로 사용하지 말고 검토한 Cartesian 좌표와 단면·물성을 입력한다. 분기마다
노드를 두고 부재를 나눈다. 지지·스프링·하중 경우/조합과 네 적용 검토를
명시한다. 중량·압력 추력·열팽창은 자동 발생하지 않는다.

STRUCTURAL_KO의 노드/로컬 축과 내력 부호, 지점 이동이 모든 조합에 적용되는
의미, 수지·소변위 한계를 확인한다. 결과가 보류돼도 원 계산값과 이유는 남는다.
frame_signal_frame은 지정한 한 조합을 simulation 신호로 내보내며 HRA와 모니터링
어댑터에 전달할 수 있다. 서로 다른 조합을 관측 시간 이력으로 바꾸지 않는다.


## 균일 열팽창과 직선 관 압력을 함께 적용할 때

FRAME_LOADING_KO와 examples/frame_loading.py를 따른다. 원본 프레임에 thermal과
pressure 등의 미사용 하중 경우를 만들고 같은 원본에서 각 기여를 준비한다.
열의 무응력 절대 온도·현재 온도·상수 계수와 범위, 압력의 면별 기준·끝 조건·
원 단면 해시와 정확한 시각을 명시한다. 준비 후 프레임을 바꾸면 다시 준비한다.

실제 축력은 등가 초기변형률 힘을 제거한 필드다. axial_n은 원 보정 전 값이다.
원 닫힌 원통의 axial stress를 복원 응력에 다시 더하지 않는다. 비균일 온도·
상태 의존 E/α·분기 압력·엘보/국소 응력·좌굴은 후속 범위다. 공개 온도차의
주변/무응력 기준은 원자료에서 확인되지 않으면 별도 가정으로 기록한다.


## 정비·교체의 비용과 배출 비교

LIFECYCLE_KO와 examples/lifecycle_study.py에서 시작한다. 같은 서비스·기간·
비용/배출 경계와 통화/기준연도·실질/명목 가격을 선언한다. 지급 시각·반복
횟수·잔존가치를 직접 입력한다. 비용 또는 할인율 None을0으로 만들지 않는다.
배출 재고는 할인하지 않으며 비용과 합친 임의 점수를 만들지 않는다.

전력의 관측 구간·sign 의미와 gap을 확인하고, 연간 운전 횟수/요금/배출계수는
별도 입력한다. 일정 score를 통화로 해석하거나 계획을 실제 작업으로 바꾸지
않는다. 위험 빈도는 조건부 평균 손실의 같은 endpoint/기간/경계와 연결한다.
예제의 외부 항목은 기본 견적과 분리한 공유 추가 항목이며 대안별 실제 절감
근거가 아니다. 원 자료/공급자의 적용성을 별도로 검토한다.

## 물리·비용·위험의 불확실성

UNCERTAINTY_KO와 examples/coupled_uncertainty.py를 사용한다. 파라미터 분포를
선언하거나 원 짝이 있는 데이터 행을 JointCase로 제공하고 definition_reviewed를
검토한다. 출력은 known_weight와 unresolved_weight를 먼저 확인한다.
conditional_on_valid_mean은 실패 표본을 제외한 조건부 수치이며 전체 평균이
아니다. 한 설계의 표본 비중을 현장 오류율·관측 고장 빈도로 보고하지 않는다.

## 관측 분포와 별도 평가 구간

UNCERTAINTY_FIT_KO와 examples/fit_uncertainty.py를 사용한다. 변수의 가족·단위별
점수 척도·학습/평가 ID·원 그룹·순서 gap·최소 행 수를 자료 적용 전에 선언한다.
fit status와 평가 status, declared_gate, field_qualified를 함께 확인한다.
점수가 낮아도 구간 포함률이 실패할 수 있다. 자료가 적거나 조건이 다르면 보류한다.

## 부식·전기방식 연결

ELECTROCHEMICAL_KO와 examples/electrochemical_methods.py를 사용한다. 보호 요구·
순 외부 전극·금속 용출 전류의 의미와 면적/시간/전극 기준·재료/환경을 명시한다.
원 데이터의 Ω을 Ωm2로 바꾸거나 도장 필요량을 실제 결함 면적비로 바꾸지 않는다.
전하/순간 전류 예산이 통과해도 보호 전위·전체 규격·현장 자격은 별도다.

## 고온 노출과 조건부 수명

CREEP_KO와 examples/creep_methods.py의 원7모델을 사용한다. 실제 적용에는
재료/열처리·온도/응력·노출 지원과 시편 단위의 파단/검열 근거를 입력한다.
처리 이력 행을 독립 시편으로 세지 않는다. 구간 노출은 누락 없이 제공하며
시간비 손상과 기간 파단확률을 분리한다. 기간확률을 위험 사건으로 전달할 때
시작까지 생존, 새 재료 나이 원점과 동일 운전조건을 검토한다. 현재 요구 시
FTA/연간 환산 어댑터는 기간 사건을 보류한다. 공통 기간의 별도 연결은
PERIOD_RISK_KO를 따른다. 의존성은 독립 근거 또는 명시적 공동 상태로 제공한다.

## 압력 시험 하중과 기체 재고

PRESSURE_TESTS_KO와 examples/pressure_tests.py를 사용한다. 시험 배율/허용치의
근거와 게이지 하중·국소 고도차를 구분한다. 가스 재고에는 절대 상태·체적·대표
온도·조성과 명시한 기준 상태가 필요하다. 원 파일에 없는 체적·대기압을 채워
측정 누설률로 보고하지 않는다. 가정 시나리오와 원 관측을 별도 기록한다.
음의 순 유출/배경 보정은 남기고, 팽창 일을 폭발 피해량으로 사용하지 않는다.

## 용접·제작 지표와 원자료

WELDING_KO의8원모델과 examples/welding_methods.py를 사용한다. CE는 mass percent,
용융 희석은 mass fraction이며 원소 미상을0으로 만들지 않는다. 전기 에너지는
같은 시각의 U×I에서 계산하며 평균 U×평균 I로 대체하지 않는다. 기록 공백/
invalid를 남기고 실제 거리와 열효율이 없으면 입열을 보류한다. 경도는 원
방법/힘/유지/불확실성/측정 위치와 사용자 기준을 받으며 미측정영역은 unknown이다.

Tandem과EBW는 서로 다른 실험/재료다. 같은 물리 용접의 전기·열 이력으로
합치지 않는다. 냉각 가지 선택·동시 센서 band 점유와 절차 합격을 구분한다.
원자료는 자동 취득하며 수백MB 규모만 필요 시 사용자와 분담한다. 취득 실패
파일은 완료로 세지 않고 이어받은 뒤에도 전체 supplier digest를 확인한다.

## 경보 이후 대응·공정과 물리 사건

RESPONSE_TRACE_KO의 예제로 경보·단서 지연·명령·목표 도달 bracket·공정 표본
조건을 연결한다. 원 명령과 simulation motion을 같은 관측 종류로 바꾸지 않는다.
작업자 시간 모델은 명령 발행을 endpoint로 하며 공정 완료에는 별도 근거가
필요하다. trace의 exchange_checked는 공정 성공을 뜻하지 않는다. 하류 재고가
남은 예제에서 명령/목표는 기한을 충족했어도 흐름 조건은 충족하지 못했다.

ALARM_SOURCE_KO의 공개 공정864행 재생은 독립 선언한 임계값을 적용한 자료
연결 예시다. 경보 횟수로 오경보율·누설 횟수·HEP를 만들지 않는다. 기록 전이와
물리 사건 onset, UNKNOWN와 NORMAL을 구분한다.

PHYSICAL_RELIABILITY_KO는 완전 포착한 사건 수/노출·명시 prior 또는 생존
나이/수명 입력을 받아 원 Mat를 호출한다. 평균 물리 강도는 초/년 기준을 검토한
뒤 변환하고, 조건부 수명 확률은 같은 기간·원점을 유지한다. 현장 관측이 없으면
원 예제의 가정값을 실제 설비 보정값으로 사용하지 않는다.

## 제작·체결·경도와 시편 물성

FABRICATION_KO의 파라미터별 근거와 사용자 요구로 굽힘·체결·벽 예산을 실행한다.
nut factor·인장 면적·성형/부식 손실이나 설계 요구 두께를 기본 표에서 채우지
않는다. 계산 완료와 요구 충족은 별도이며0토크와 미상 토크도 구분한다.

HARDNESS_KO는 실제 힘N·압흔 길이m와 시험 조건을 받는다. conventional hardness를
강도·취성·용접 합격/파손확률로 자동 변환하지 않는다. 새 공개 압흔의 미상 표면은
COPPER_HARDNESS_KO처럼 원 조건 보류와 가정한 기하 진단으로 구분한다.

CHARACTERIZATION_KO는 원 면적/표점거리·게이지 변형률·같은 조건의 연신율과
명시한 cyclic amplitude 입력을 쓴다. COPPER_TENSILE_KO의 source time/strain
반전이나 음수는 정렬·절댓값·machine travel 대체로 복원하지 않는다. 단조 시편의
보고 기울기나 같은 export label을 독립 검증된 cyclic 물성으로 쓰지 않는다.

FASTENER_DATA_KO의 힘/토크는 서로 다른 angle grid다. nominal 마찰계수는
nut factor가 아니며 원 속도 단위의 불일치가 해결되기 전 시간을 합성하지 않는다.
실험 자료 확보와 실제 적용 가능한 모델 입력 확보를 따로 확인한다.

## 검사 통계와 반복 하중의 원 모델 사용

[검사 통계 가이드](INSPECTION_STATISTICS_KO.md)에서 실제 정답·독립 시편·검사
절차와 단위를 먼저 준비한다. 이항 기회의 관측 행과 횟수를 맞추고 검출 크기
또는 검증된 blank를 구분한다. hit/miss POD에는 실제 알려진 결함 크기와
검출 여부가 필요하다. TOFD에는 같은 불변 frame의 기준·회절 pick과 교정된
probe 간격·음속·벽두께를 제공한다. 두께 격자는 원 행·열·동일 셀 면적·좌표계와
미상 셀을 보존한다. 현장 자료가 없으면 예제의 합성 truth·독립성·교정을 옮겨
붙이지 않는다. 생성 기록은 `check_inspection_statistics_record`로 확인한다.

[반복 하중 가이드](CYCLIC_FATIGUE_KO.md)의 사용자 파라미터 예제를 따라
Goodman·경로 일·변형률 수명을 호출한다. 재료 상태·모집단·교정 지원과
원 stress/strain 정의를 입력하고 MPa·% 같은 원 단위는 근거 있는 별도 SI
변환 기록으로 남긴다. 진폭과 범위, cycle과 reversal, 기록된 일과 닫힌 cycle의
일을 구분한다. 미상은 0으로 채우지 않고 음의 경로 일을 절대값으로 수리하지
않는다. 단조 인장 계수를 cyclic 계수로 사용하거나 평균응력 효과를 두 번
적용하지 않는다.

원 Mat는 사용자가 별도로 관리한다. 동시 개발 중인 원 폴더 대신 라이선스·
고지·필수 package 자료를 유지한 검토된 고정 소스를 선택할 수 있다. 아래의
`PATH_TO_REVIEWED_MAT_SNAPSHOT`은 사용자가 선택한 로컬 source root다.
공개 결과에 이 경로를 저장하지 않으며 실제 로드된 package/evaluator/registry와
tree SHA의 일치를 공통 `material_source_guard`로 확인한다.

```powershell
python scripts/check_inspection_statistics.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT --source-kind private_pinned_snapshot
python scripts/check_cyclic_fatigue.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT
python -m pytest -q tests/test_inspection_statistics.py tests/test_cyclic_fatigue.py tests/test_material_source.py
```

검사 네 모델의 15사례·24개 독립 수치 기준과 피로 세 모델의 원 응답 16개·
10개 독립 산술/해 기준을 확인했다. 이 결과는 소프트웨어 교환과 계산 검증이다.
검사 합격·POD 현장 교정·피로 수명 전이·HEP·기간 고장확률의 자격을 뜻하지
않는다. 공개 집계는
[검사 결과](../research/inspection_statistics_results.json)와
[피로 결과](../research/cyclic_fatigue_results.json)에 있고 상세 요청·관측·receipt는
각 `artifacts` 하위에 둔다. 전체 진행률과 전체 회귀 결과는 STATUS를 따른다.

부식 power-law·Archard·Gumbel 세 모델도 native 연동을 확인했다. 작성 조건의
원 응답 12개·독립 스칼라 기준 11개·거부 경계 5개는
[부식·마모 가이드](CORROSION_WEAR_KO.md)와
[실행 집계](../research/corrosion_wear_results.json)를 따른다.
`python scripts/check_corrosion_wear.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT`으로
재현한다. 이 결과는 적합·재표집·물리 꼬리의 독립 실험 검증이 아니다.
`CorrosionWearRequest`/`LossDomain`을 공개 구리·AFM 자료에 맞출 때 미상
단위·물리 노출 원점·접촉 경도·분포 면적을 예제 값으로 채우지 않는다.
[공개 자료 가이드](CORROSION_SOURCE_KO.md)의 열네 원조건 요청은 모두
`needs_data`이며 원 수치 모델 호출은 0회다. 이 자료 확인과 작성 조건의
native 소프트웨어 검증을 실제 자료 교정으로 합치지 않는다.

## 재료 조성·충격·수소·축 방향 감육의 원 모델 사용

다음 아홉 원 기능을 사용할 수 있다. 등록 계산 목록과 외부 소유 경계 목록은
계속 구분하며, 전체 진행률과 최종 회귀 결과는 STATUS를 따른다.

| 작업 | 선택 경계와 가이드 |
|---|---|
| 증명서/검증 조성 정합·조성 지수·충격 천이 3종 | `MaterialQualityRequest`, [재료 품질](MATERIAL_QUALITY_KO.md) |
| 반무한 확산·국부 trap 평형·유한 슬랩 속도 수송 3종 | `HydrogenTransportRequest`, [수소 수송](HYDROGEN_TRANSPORT_KO.md) |
| 축 방향 감육 프로파일의 선언된 압력차 용량 | `ProfileCapacityRequest`, [감육 용량](PROFILE_CAPACITY_KO.md) |
| 독립 게이트·배타 경로의 원시 기능 2종 수치 대조 | 기존 `MaterialJob`와 `HRARiskClient`, [위험 대조](MATERIAL_RISK_REFERENCE_KO.md) |

실제 사용할 때는 원자료의 단위와 분석 대상부터 정한다. 조성 구간의 질량
분율과 pitting 원소의 질량 백분율을 혼용하지 않는다. 증명서·확인 분석은 같은
물리 범위에 연결하고, pitting의 Cr·Mo·N은 같은 assay의 측정값으로 제공한다.
미측정 원소를 0으로 만들거나 다른 batch의 제조사 상한을 측정값으로 옮기지
않는다. 재료 지수의 계수와 요구 조성 구간은 검토한 사용자 근거로 교체한다.

충격 천이에는 같은 형상·방향·재료 상태의 완전 관측 시리즈가 필요하다.
K·J와 시편 ID를 연결하고 검열/미파단 결과를 완전한 흡수 에너지로 단정하지
않는다. 원 shelf/식별 조건의 보류와 목표 crossing의 `None`을 유지한다.
곡선이 맞았다는 이유로 파괴인성이나 허용 사용 온도를 만들지 않는다.

수소에는 원자 몰 기준 농도·사이트 밀도·에너지 기준·초기 상태와 경계를
제공한다. H₂ 몰수·ppm·기체 상태·총 전류에서 격자 농도를 자동 생성하지
않는다. 점 농도·국부 total 분배·셀 평균을 구분하고, 물리 시계 원점·원 깊이/
시간/셀 ID를 유지한다. kinetic 오른쪽 표면도 양의 유속은 안쪽이며 독립 속도
이력은 그대로 공급한다. 사용자가 모수나 온도 이력을 바꾸면 같은 재료·격자·
온도 지원·근거를 함께 재검토한다. 부분 solver 궤적은 완료 결과가 아니다.

감육 용량에는 전체 축 범위의 좌표·기준 벽·잔여 두께와 동일 형상/재료의
기준 압력차·팽창계수 공급자를 제공한다. 검사 2D 격자를 근거 없이 1D로 줄이지
않는다. 영 잔여 두께·지원 밖 손실·공급자 길이 범위 이탈은 원 상태를 유지한다.
선택 요구 압력은 내압−외압의 비음수 차압이며, 계산 성공과 요구 압력 만족을
따로 읽는다. 용량은 MAWP·잔여 수명·기간 파손확률이 아니다.

기존 위험 API에는 별도 사건 ID·조건·기회·기간·생존·의존 근거를 공급한다.
같은 사건의 반복 참조를 두 독립 사건으로 늘리지 않는다. 범위 없는 Mat 확률을
HEP나 요구당 실패확률로 바꾸거나 1/s 빈도를 기간확률로 읽지 않는다.
기존 HRA·FTA·ETA와 기간 위험 계산을 유지하며 새 위험 함수를 선택할 필요는 없다.

```powershell
python scripts/check_material_quality.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT
python scripts/check_hydrogen_transport.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT
python scripts/check_profile_capacity.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT
python scripts/check_material_risk_reference.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT --hra PATH_TO_HRA
```

각 가이드의 `make_cases` 예제는 독립 작성 조건이다. 자신의 데이터·모수로
교체할 때 검토 키·근거 ID·지원 범위도 갱신한다. 미상 입력이나 미검토 요청은
원 Mat 없이도 `needs_data`를 확인할 수 있다. 실제 계산은 별도 Mat 설치 또는
검토한 소스가 필요하며, 버전·schema·단위·pin 변경은 새 검증 대상으로 다룬다.

## 두 새 실험 자료의 취득과 보류 확인

[원조건 판독 가이드](MATERIAL_PROCESS_DATA_KO.md)의 BAM S355 Charpy 4자산과
MS-AHSS1300 수소 투과 1자산은 합계 1,620,960바이트다. CC BY 4.0 근거와
공급자 MD5/SHA-256을 보존하고 공통 취득기를 사용한다.

```powershell
python scripts/acquire_data.py --only bam_s355_charpy ahss_hydrogen_permeation --workers 2 --maximum-seconds 45
python scripts/review_material_process_data.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT
```

BAM의 6시편은 한 온도의 미파단 시험이며 보조 조성 선언은 시험 lot와 대응이
확인되지 않았다. AHSS의 1,859행은 두 연속 전류 이력이며 독립 시편 ID·유효
투과 면적·농도 경계·D/포획/초기 상태가 미상이다. 원조건의 재료 3요청과 수소
6요청은 모두 `needs_data`이고 원 수치 모델 호출은 0회다. 파일 판독 성공을
모수 보정이나 현장 검증으로 바꾸지 않는다. 원본·캐시·관측/변환 행·상세 receipt는
로컬 `data/raw`와 `artifacts`에 두고 공개 집계만 `research`에 남긴다.

원 소스 pin 보고서를 다시 읽을 때는 공통 `verify_material_source_pin`이
기대 SHA·전후 불변·파일 수와 실행 여부의 일치를 검사한다. 미실행 요청은
실행 pin을 주장하지 않는다. 이 검사는 전자서명이나 공급자·현장 진위 인증이
아니다. 실제 loaded source 검사와 런타임/의존성·라이선스 기록을 함께 보존한다.
확정되지 않은 원 엔진 배포 URL이나 버전을 추정하지 않으며, 별도 배포가
준비되면 [PI-X04 배포 인수 조건](EXTENSION_REQUESTS_KO.md)을 따라 재현한다.

## 감시 자료의 학습·보정·평가와 실행 환경 선택

[모니터링 교환](MONITORING_EXCHANGE_KO.md)의 `make_case()`는 기본으로 source/version/spec
미선택 상태를 만든다. 자신의 PMT source를 fingerprint하고 `client.describe`의 원 spec을
확인해 요청에 고정한다. 현재 Python이 원 의존성과 맞으면 `NativeMonitoringClient`,
다르면 `PythonMonitoringClient`를 쓴다. 원 검출기 extra를 이 패키지 핵심 의존성으로
추가할 필요는 없다. 별도 환경의 설치 버전과 source pin은 함께 기록한다.

원 관측을 training/calibration/evaluation으로 먼저 나눈 뒤 각 역할에 물리 run,
source group, 관측 ID, 원 시계·채널·단위·품질을 제공한다. 한 run의 연속 시간
분할과 서로 다른 run의 분할을 혼합하지 않는다. 정상성은 별도의 검토 근거다.
평가 자료로 scaler·모수·threshold를 다시 fit하지 않는다. 창 초기·결측·gap의
미계산 행은 그대로 두며 경보 수를 탐지 정확도로 읽지 않는다.

다음은 source 선택 전의 보류 예제다. optional PMT·SAGA를 불러오지 않는다.

```python
import runpy
from pipeline_integrity.monitoring_exchange import evaluate_monitoring, verify_monitoring_record
from pipeline_integrity.knowledge_exchange import evaluate_knowledge, verify_knowledge_record

request, splits, context = runpy.run_path("examples/monitoring_exchange.py")["make_case"]()
monitor = evaluate_monitoring(request, splits, context)
assert monitor["status"] == "needs_data" and monitor["native_receipt"] is None
verify_monitoring_record(monitor)

requests, context = runpy.run_path("examples/knowledge_exchange.py")["make_cases"]()
knowledge = evaluate_knowledge(requests["retrieve"], context)
assert knowledge["status"] == "needs_data" and knowledge["native_receipt"] is None
assert verify_knowledge_record(knowledge)
assert knowledge["authority"]["approved_rule"] is False
```

BattLeDIM 적용은 한 simulation run의 첫 3일을 하루 288행씩 나눈 조건이다.
첫 두 날의 reference 정상성은 caller 가정이며 원 관측 라벨과 구분했다.
pressure head m를 Pa_abs로 바꾸지 않았다. 방법별 점수·경보·유효 행 수는
[실행 집계](../research/monitoring_exchange_results.json)에서 확인한다. 단일 채널
lag_ridge shape 오류나 CBLOF 지원 실패 때 가짜 채널·표본 삭제로 성공을 만들지 않는다.

현재는 일곱 family의 선택 59 ID를 실제 실행했다. 동적 PCA와 neural 6종,
temporal_neural 14종에는 원 그래프·diffusion 연구 구현도 포함된다.
classification·incremental·subsequence·engineering은 선택하지 않았다.
새 신경망을 선택할 때는 hidden/epochs/batch_size/seed와 sequence window를 명시하고,
예산과 검토 근거를 갱신한다. 작은 smoke에 쓴 모수나 epoch 수는 현장 추천값이 아니다.

신경망은 별도 모니터링 환경의 torch 2.14.1+cpu에서 실행했다. 주 패키지에
torch를 필수 설치하지 않는다. 같은 seed 재현·평가에서 학습 상태 불변·CPU thread
복구를 확인하지만, 학습 수렴·다른 장치의 동일 결과·실제 탐지 성능은 별도다.
실행 방법과 원 배포 고지는 [감시 가이드](MONITORING_EXCHANGE_KO.md)를 따른다.

## 공통 시계로 충전소 궤적을 넘기는 순서

[충전소 가이드](STATION_EXCHANGE_KO.md)의 `make_case()`는 검토되지 않은 요청이다.
자신의 source snapshot/호환 Python과 tank/network 또는 제한된 full-reference
조건을 지정한다. `reference_boundary(asset_id, clock_id)`의 event ID·clock offset과
조건을 검토해 `RunContext.metadata["station_boundary"]`에 결합하고, context에 있는
EvidenceRef ID를 선택한다. source manifest pin과 세 검토가 있어야 실행한다.

원 native clock은 episode elapsed 0부터이며 공통 clock에 명시 offset만 더한다.
검증한 결과는 `to_signal_frames(result, context, source_root=...)`로 내보낸다.
부분 prefix는 기본 거부하므로 검토 후 명시 `allow_partial=True`로 구분한다.
연결 유량의 양수는 명목 source→target이고 역류는 음수다. native 원 실제 포트와
미상 엔탈피는 receipt/invalid에 남긴다. full-reference에서 원 API가 주지 않은
전체 재고·에너지나 고장확률을 만들어내지 않는다. 같은 clock 이름만 붙였다는
이유로 별도 관측과 자동 정합하지 않는다.

```powershell
python scripts/check_station_exchange.py --source PATH_TO_REVIEWED_STATION_SNAPSHOT --upstream-source PATH_TO_ORIGINAL_STATION --python PATH_TO_STATION_PYTHON
python examples/station_exchange.py
```

## 검토한 pool boundary를 연결하는 순서

1. [pool 가이드](POOL_EXCHANGE_KO.md)의 `reference_config`를 복사해 자신의 검토한
   지면 도달 액체 유입·표면·초기 재고·output clock·numerics로 교체한다.
2. ENU origin, source asset/event/port, clock offset, ZOH final hold와 유입 의미를
   `RunContext.metadata["pool_boundary"]`에도 같은 값으로 결합한다. 근거·origin을 기록한다.
3. 선택 SDK source manifest digest와 세 검토를 새 요청에 제공하고 필요하면 별도
   Python·wall timeout·직렬화 예산을 지정한다. 미확정 검토는 needs_data로 유지한다.
4. 전체 원 ledger·경고·수지·escape·numerical adjustment를 확인하고 저장 receipt를
   재검사한다. computed 상태만 `to_signal_frames`로 감시 API에 전달한다.

입력 유입은 이미 지면에 도달한 액체이며 jet/deposition 비율을 임의 가정하지 않는다.
pool 증발 interval mean은 누적 증발량 차분이고 첫행은 미상이다. 원 순간 증발률을
감시 간격 평균으로 이름만 바꾸지 않는다. SDK에 없는 기상 상태·전체 에너지 원장을
재구성하거나 withheld 방출을 계산 완료로 바꾸지 않는다. 위험 엔진으로 옮길 때는
별도 검토한 물리 경계와 사건/기간 계약이 필요하다.

## 지식 후보를 검토하는 방법

[지식 교환](KNOWLEDGE_EXCHANGE_KO.md)은 원 검색·인용·HAZOP 준비도를 사용한다.
자료별 판·권리·허용 방법·적용 설비/분야·유효기간·locator/content pin을 먼저
확정하고 허용 source ID를 좁힌다. 외부 자료의 사용 조건이 미상이면 검색 가능 여부와
관계없이 보류한다. 요청 시각 `as_of_utc`와 근거는 명시한다.

현재 재현은 독립 작성 corpus를 임시 DB에 넣은 offline 실행이다. 검색된 문헌 후보,
pending 규칙, 기계 준비도와 사람 승인을 구분한다. 수치·단위·인용 위치를 확인해도
현장 적용이나 법적 적합성이 자동 승인되지는 않는다. 추론 서비스가 없는 요청은
needs_data이며 생성 답변을 대신 채우지 않는다. 원 SAGA DB나 운영 경보를 수정하지 않는다.
SAGA 별도 환경은 PyMuPDF를 실제 import하므로 그 AGPL/상업 조건도 독립적으로
확인한다. 원 SAGA의 MIT만으로 모든 의존성을 MIT라고 보지 않는다.

## 정상 매설 열 경계를 적용하는 방법

[매설 열 가이드](BURIED_THERMAL_KO.md)의 `make_cases()`는 실제 토양 추천값이 아닌
독립 작성 조건이다. 자신이 검토한 관·층·접촉·film·토양·평면 온도와 각 물성 지원,
최소 길이/직경비를 새 요청에 넣는다. 접촉 입력 단위는 K·m/W이고 구간 저항 K/W와
다르다. 유동 모드는 일정 cp·단상·정상 조건과 최대 NTU의 근거를 추가로 요구한다.

총 열률은 유체에서 토양으로 양수다. 온도차 0, 열 유입의 음수와 미상은 구분한다.
유동 평균 온도가 지원 범위 안이어도 입구/출구 국소 온도가 벗어나면 out_of_scope다.
정체·transient soil·수분·복수관·계절 지표 조건을 이 정상 함수로 계산하지 않는다.
물성 공급자를 바꾸면 값뿐 아니라 지원 범위·조건 ID·근거·검토와 버전도 갱신한다.

```powershell
python scripts/check_monitoring_exchange.py --monitoring PATH_TO_REVIEWED_PMT --worker-python PATH_TO_MONITORING_PYTHON
python scripts/check_pool_exchange.py --source PATH_TO_REVIEWED_POOL_SDK
python scripts/check_knowledge_exchange.py --saga PATH_TO_REVIEWED_SAGA --python PATH_TO_KNOWLEDGE_PYTHON
python scripts/check_buried_thermal.py
```

worker 환경과 원 source·버전·고지는 별도로 확보한다. 원 소스·바이너리·임시 DB·
상세 요청/응답·관측 행은 이 코드 배포에 넣지 않는다. 공개 research는 집계와 핀을
남기며, 기존 기록의 pin을 덮어쓰지 않고 새 조건으로 새 기록을 생성한다.

## 원통형 관벽·토양 축열을 연결하는 순서

[원통 과도 열](PIPE_SOIL_TRANSIENT_KO.md)은 실제 Mat 열 solver를 호출한다.
관벽·단열·토양의 반경 edges와 길이, 각 셀의 초기 평균 K, 검토한 k/부피 열용량
곡선과 온도 지원을 제공한다. 인접 층의 공통 반경은 같은 값을 재사용한다.
원 source/model/schema pin과 material client를 검토한 뒤 실행한다.

안·밖 경계를 단열·지정 온도·지정 열유속·대류/복사 중 선택하고 전체 knot별
활성 값을 넣는다. 여기서는 linear 보간이며 ZOH history를 이름만 바꿔 넣지 않는다.
두 경계 열의 양수는 고체로 유입하는 방향이다. 바깥 열 손실은 음수로 유지한다.
물성·경계·초기 상태가 미상이면 needs_data, 유한 접촉 저항은 out_of_scope다.
원 solver 기준이나 공간/시간 해상도를 낮춰 예산을 통과시키지 않는다.

`pipe_soil_boundary(request)`로 만든 metadata를 같은 RunContext에 결합한다.
계산 뒤 원 결과·제한·최종 층별 축열과 원 substep 누적 J, 출력 시점의 경계 W와
표면/셀 K를 함께 확인한다. 전체 J가 맞아도 서로 상쇄되는 잘못된 W는 거부한다.
검증한 computed 결과만 신호로 넘기고 sample W를 구간 평균 열률로 바꾸지 않는다.
유체 온도는 제공한 경계이며 유체 재고·엔탈피와 함께 푼 되먹임 결과가 아니다.

이 모델의 바깥 경계는 동심 원통이다. 정상 매설관 shape factor에 맞춘 가상
반경이나 실측 수직 깊이를 실제 토양 체적으로 넣지 않는다. 수평 지표·복수관의
실제 단면 문제는 별도 단면 기하와 검토된 경계를 갖춘 모델을 선택해야 한다.

```powershell
python scripts/check_pipe_soil_transient.py --materials PATH_TO_REVIEWED_MATERIAL_SOURCE
python examples/pipe_soil_transient.py
python scripts/acquire_data.py --only soil_hydrothermal_covers soil_borehole_heating --workers 3 --maximum-seconds 120
python scripts/review_soil_source.py
```

## 토양 관측을 모델 입력으로 쓰기 전의 확인

[토양 자료](SOIL_DATA_KO.md)의 두 묶음은 7파일·7,822,891바이트이며 공급자 MD5와
원 권리·판을 확인했다. 시추공 작은 MAT의 온도는 처리된 실제 관측이며 원 전체
파일·개별 보정/축약 계보·달력 원점은 미상이다. 실제 processing time 5~129,481초와
121/122/123/427/843초 간격을 유지한다. 315개 수직 깊이점은 radial geometry가 아니다.

피복 workbook의 일 극값·range를 순간 온도나 연속 경계로 만들지 않는다.
T sheet의 header/수치 불일치, 수분/suction의 단위 미상과 결측, 원 두 기온 시각
열·불규칙 rainfall 시계를 보존한다. 물성 k·Cv·관벽 온도·관 형상·가열 전력을
제공하지 않은 관측에서 이를 추정하지 않는다.

선택 시추공 온도를 감시 신호로 재생하려면 원 raw SHA와 같은 EvidenceRef,
asset/clock·수직 위치·record digest·processing time/offset·측정량 의미의 명시
binding을 제공한다. °C→K 외에 시계 정합·보간·재영점화는 수행하지 않는다.
측정 신호가 생성됐다는 사실을 원통 모델의 validation/calibration 근거로
사용하지 않으며, 물리 좌표·센서·경계·처리 계보와 독립 실험 대응을 먼저 검토한다.

## 지표·여러 관 공동을 가진 토양 단면을 사용할 때

[FiPy 단면 가이드](SOIL_CROSS_SECTION_KO.md)의 예제는 실제 지표와 두 다각형
cavity를 가진 독립 작성 조건이다. 원 vertex/face/cell·경계 group·pipe asset ID를
자신의 검토한 기하로 교체한다. 현재 지원은 직교 mesh로 제한되며 허용 각도를
높여 비직교 해석을 켤 수 없다. 원형 관을 다각형으로 만들었다면 근사 오차·
둘레·해상도·far-field 거리도 별도 검증해야 한다. 내부 cell face를 pipe라고
이름만 바꾸어 공동을 대신하지 않는다.

초기값은 각 토양 cell의 평균 K다. 일정 k/Cv와 온도 지원, 수평 지표·관 공동·
far-field 각 face의 온도/열유속/대류/단열 조건과 근거를 입력한다. 관벽·유체 에너지와
함께 계산하는 경로가 아니므로 유체 inlet K를 근거 없이 토양 cavity 표면 K로 쓰지 않는다.
수분·동결·지하수·비직교/곡면의 일반 지원은 후속 범위다.

시간 보간은 linear 또는 previous-knot hold를 명시한다. 예산과 온도 지원을
검토하고 `boundary_metadata(request)`를 같은 RunContext에 결합한다. output에는
원 내부 step의 cell 평균·boundary BE 적용 W·누적 J와 전역/국부 equation 잔차가
있다. hold의 오른쪽 knot에서 끝난 구간의 경계와 새 knot 직후 경계를 혼동하지 않는다.
초기 열률 None/invalid와 실제 partial prefix를 유지한다.

기본 예제의 미검토 보류는 optional FiPy 없이도 실행한다.

```python
import runpy
from pipeline_integrity.soil_cross_section import (
    evaluate_soil_cross_section,
    verify_soil_cross_section_record,
)

soil_request, soil_context = runpy.run_path("examples/soil_cross_section.py")["make_case"]()
soil_record = evaluate_soil_cross_section(soil_request, soil_context)
assert soil_record["status"] == "needs_data" and soil_record["native_called"] is False
assert verify_soil_cross_section_record(soil_record)["valid"]
```

실제 실행은 optional soil 환경을 설치한 뒤 같은 backend와 검토 조건으로 재현한다.

```powershell
python -m pip install -c requirements-tested.txt ".[soil]"
python scripts/check_soil_cross_section.py
python examples/soil_cross_section.py
python -m pytest tests/test_soil_cross_section.py -q
```

기준 해의 실패를 허용오차 확대나 결과 보정으로 숨기지 않는다. 전역 에너지
통과는 실제 열류 정확도·mesh 자격의 충분조건이 아니다. 일반 비직교 correction의
호환/물리 정확도 문제는 미완료로 보존하고, 새 기준·격자/시간 수렴·독립 경계
열류 검증을 확보하기 전에는 전체 매설 복수관 기술을 완료로 표시하지 않는다.
