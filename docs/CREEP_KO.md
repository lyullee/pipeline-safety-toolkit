# 크리프·열화와 생존 조건부 수명 연결

`creep_exchange`는 기존 `matintegrity`의 일곱 모델을 공통 자산·위치·시계·
재료 상태·근거 계약으로 호출한다. 구성식, 수명 적합기, 원자료 판독기는 원
프로젝트가 소유한다. 이 패키지는 적용 범위와 노출 구간, 결과 의미 및 위험
평가로 전달하는 조건을 점검한다. 현재 결과의 자격은 연구 수준이다.

## 설치와 실행

README의 개발/자료 의존성을 설치하고 원 재료 프로젝트가 Python에서
import 가능한지 확인한다. `matintegrity`는 별도 소스 프로젝트이며 이 배포본에
포함하지 않았다. HRA는 기간확률의 원 Boolean 호출을 재현할 때 필요하다.

```powershell
python scripts/acquire_data.py --only cmsx6_creep --workers 1 --maximum-seconds 90
python scripts/check_creep.py --materials PATH_TO_MAT --hra PATH_TO_HRA
python -m pytest -q tests/test_creep_exchange.py
```

검증 스크립트는 지정한 프로젝트의 `src`를 사용하며 소유 코드를 수정하지
않는다. 실행 전후 소스 트리 해시를 비교한다. 전체 결과와 변환 관측은 로컬
`artifacts/creep`에, 공개 가능한 집계와 실행 해시는
`research/creep_results.json`에 저장한다.

## 방법과 입력

| method | 원 입력의 핵심 필드 | 출력과 적용 범위 |
|---|---|---|
| `creep.norton` | stress_Pa, reference_stress_Pa, rate_at_reference_stress_per_s, stress_exponent, exposure_s | 고정 온도의 정상 크리프 속도와 그 노출의 변형률 |
| `creep.larson_miller` | temperature_K, constant, rupture_time_s | 선언한 파단시간의 Larson–Miller 좌표; 수명 곡선 적합은 포함하지 않음 |
| `creep.time_fraction` | exposure_s, rupture_life_s | 동일 길이 벡터의 노출/기준 수명 비 누적 |
| `creep.lognormal_life_fit` | 시편·응력·온도·종료시간·right_censored 벡터, family, 재료/제어 기준, evidence | 파단과 우측 검열을 함께 사용하는 원 수명분포 적합 |
| `creep.conditional_life` | model, material_condition_id, 응력/온도 기준, current_age_s, horizon_s, 적용 검토 | 시작까지 생존했다는 조건의 첫 파단 기간확률 |
| `kinetics.arrhenius` | reference_rate, reference_temperature_K, temperature_K, activation_energy_J_mol | 동일 기구를 가정한 기준 속도와 온도 배율 |
| `degradation.parabolic_oxidation` | initial_oxide_thickness_m, parabolic_constant_m2_s, exposure_s | 포물선 성장 가정의 산화막 두께 |

필드 이름과 숫자 단위는 원 `MaterialJob` 스키마를 그대로 따른다. 기본 시간은
초, 응력은 Pa, 온도는 K다. Larson–Miller 내부 로그의 시간 관례만 시간(h)이며
입력 초를 3600으로 나눈 뒤 로그를 구한다. 36,000초·923.15K·상수20 사례의
좌표는 19,386.15K다. 이 변환만으로 미지 파단시간을 예측하지 않는다.

정상 크리프는 초기/가속 크리프나 파단을 모델링하지 않는다. 시간비 누적
손상은 1을 넘을 수 있는 결정론적 지표다. 산화막 성장량은 관벽 금속 손실과
다른 물리량이다. 온도 배율은 지정한 기구의 속도 변화이며 수명확률이 아니다.

## Python 사용과 사용자 입력

```python
import runpy
from pipeline_integrity.creep_exchange import (
    evaluate_creep,
    evaluate_creep_sequence,
    CreepRiskMapping,
    creep_risk_probability,
)

example = runpy.run_path("examples/creep_methods.py")
cases, sequence, context = example["make_cases"]()
strain = evaluate_creep(cases["creep.norton"], context)
damage = evaluate_creep_sequence(sequence, context)
fit = evaluate_creep(cases["creep.lognormal_life_fit"], context)
request = example["prediction_request"](fit, context, current_age_s=500000.0, horizon_s=500000.0)
prediction = evaluate_creep(request, context, fit_record=fit)
mapping = CreepRiskMapping(
    expected_prediction_digest=prediction["record_digest"],
    event_id="span_creep_rupture",
    physical_scope_id="span_period",
    condition_id="constant_conditions",
    period_start_s=500000.0,
    period_finish_s=1000000.0,
    fresh_material_origin_s=0.0,
    conditioning="Survival to period start; unchanged declared stress and temperature",
    failure_definition="First creep rupture of the declared material population",
    age_origin_evidence_id="reference",
    evidence_ids=("reference",),
    physical_event_and_age_mapping_reviewed=True,
)
binding = creep_risk_probability(prediction, mapping)
print(strain["status"], damage["damage"], binding["status"])
```

이 예제의 계수와 검토 값은 작성한 가상 기준에만 해당한다. 실제 적용에서는
`CreepRequest`의 자산·위치·시계·재료 상태, 원 parameters와 `evidence_ids`를
교체한다. `RunContext.evidence`에는 해당 근거를 등록한다. 원 입력과 출력
스키마·버전·단위·확률 의미는 기존 `prepare_material_job` 경로로 고정한다.

`applicability_reviews`의 방법별 키는 `REVIEWS[method]`에서 조회한다. 누락과
미검토는 `needs_data`이며 검토 키의 임의 추가는 오류다. 검토를 True로 설정하는
것은 사용자의 적용 선언이다. 자동 현장 승인이나 타 기관 인증을 뜻하지 않는다.

Norton·Arrhenius·산화막 모델에는 `CreepDomain`의 재료 상태, 온도/응력 구간,
최대 노출시간, 보정 근거와 `calibration_reviewed`를 제공한다. 범위 이탈은
`out_of_scope`, 범위나 온도 미상은 `needs_data`다. Arrhenius의 기준 온도도
지원 범위에 있어야 하며 `reference_rate_unit`를 SI 기준으로 제공한다.
예를 들어 mm/year 수치를 그대로 넘기면 먼저 단위 변환하도록 보류한다.

## 노출 이력과 손상

`CreepSequence`는 선언한 시작/종료와 순서 있는 `CreepBlock`을 받는다. 각
구간은 응력·온도·양의 기준 파단수명·근거와 `life_basis`를 갖는다. 실제 파단
관측, 검토한 기준값, 모델 분위수, 미해결을 구별한다. 모델 분위수에는
0과 1 사이의 `life_quantile`가 필수다. 이를 파손확률로 해석하지 않는다.

구간 겹침·역순·선언 창 밖 입력은 오류다. 관측 공백이나 기준 수명 누락은
0으로 채우거나 제외하지 않고 보류한다. 지정한 시간 허용오차는 구간 경계의
수치 비교에만 사용한다. 재료 상태/응력/온도/노출 지원과 선형 누적 검토를
확인한 뒤 원 시간비 모델에 모든 구간을 전달한다. 두 노출 각각100,000초와
기준 수명1,000,000/2,000,000초의 작성 기준은 누적 지표0.15다.
하중 순서 효과, 회복, 크리프–피로 상호작용은 현재 포함하지 않는다.

## 검열 수명과 확률 의미

`specimen_id` 하나는 물리 시험편 하나다. 각 시편의 종료시간과
`right_censored`를 유지한다. 파단하지 않은 채 시험을 끝낸 시편의 종료시간을
실제 파단시간으로 바꾸지 않는다. 로그 수명 산포는 모집단의 시편 간 산포다.
모수 추정 불확실성이나 재료/기구 선택 불확실성은 현재 포함하지 않는다.

`stress_only`는 동일 온도 범위에서 응력 관계를 추정한다. 여러 온도의 충분한
지원·식별성이 없으면 `arrhenius_stress` 활성화에너지 적합은 보류된다.
`median_life_s`는 중앙수명이다. 평균수명·남은 평균수명과 같지 않다. 지원 밖
중앙수명이 null인 경우에도 원 상태·지원 판정과 기간확률을 함께 확인한다.
원 longest failure와 명시한 외삽 배율의 시간 지원을 자동 확대하지 않는다.

조건부 확률의 의미는 `P(a < T <= a+h | T > a)`다. 현재 나이 a와 이후 기간 h,
새 재료의 기준시각 및 그동안 조건이 유지됐다는 근거가 필요하다. `fit_record`를
넘기면 전체 적합 결과와 모델 일치를 확인한다. 외부에서 받은 모델은 그
내용의 `model_reference_digest`와 근거를 제공한다. 해시는 변경 탐지용이며
출처의 진위나 모델의 정확성을 인증하지 않는다.

`creep_risk_probability`는 원 `MaterialReceipt`와 값·조건·확률 의미의 일치를
확인한다. `period_start_s - fresh_material_origin_s`가 원 current_age_s와,
기간 길이가 원 horizon_s와 같아야 한다. 출력은 연구 수준
`RiskProbability(measure="mission_probability")`다. 같은 학습 모델의 의존 그룹을
보존하며 크리프 파단 endpoint를 봉쇄 상실·누설·인적오류로 자동 변경하지 않는다.

초기 크리프 검증에서는 원 HRA의 일반 Boolean 함수로 단일 기간 사건의 수치 전달을 확인했다.
이 패키지의 기존 `HRARiskClient.fault_tree`와 `annualize`는 요구 시 실패 계약을
사용하므로 기간 사건을 보류한다. 여러 기간 사건의 공통 시간창·생존 조건·
의존성·원 모델 계보를 받는 별도 공통 기간 어댑터는 현재 PERIOD_RISK_KO를 따른다. 확률의 이름을
요구 시 실패로 바꾸거나 1년으로 나누어 기존 계약을 통과시키지 않는다.

## 공개 실험 적용과 시계

[BAM CMSX-6 자료](https://doi.org/10.5281/zenodo.14750432)는 Luis A. Ávila Calderón,
Sina Schriever, Ying Han, Juergen Olbricht, Pedro Dolabella Portella,
Birgit Skrotzki가 제공한 단결정 니켈계 초합금 실험이다. DOI 등록 버전은1.1이며
개별 workbook 파일명은 v1.0이다. 파일명과 등록 버전을 혼동하지 않는다.
[DataCite 권리 기록](https://api.datacite.org/dois/10.5281/zenodo.14750432)의
CC BY 4.0을 확인했고 캐시 해시는 `research/creep_methods.json`에 기록했다.

workbook과12개 LIS의 총13파일5,470,578바이트를 취득해 공급자 MD5와 대조했다.
12시편 중 파단9개·시험 제한시간의 우측 검열3개, 온도1253.15K 하나와
초기 명목응력140/170/200/230MPa를 유지했다. 기존 원 stress-only 적합은
실행됐고 온도 의존 적합은 식별 자료 부족으로 보류했다. 이 자료는 강관이나
운전 배관의 크리프 파손률을 검증하는 데이터가 아니다.

원 판독기의12개 처리 이력140,032행을 파일 수치와 비교했다. 시간(h→s),
변형률(%→분율), 온도(°C→K)를 확인했고 반복 시각23구간을 보존했다.
시간 원점은 하중 부여 종료이며 설비 가동 시작·새 재료 나이0과 자동으로 같지
않다. 원 판독기의 기본 반복시각 거부와 명시적 preserve 정책을 구분한다.
시각을 흔들거나 정렬·압축해 엄격 증가 SignalFrame으로 전달하지 않는다.
이력 마지막 행을 파단으로 간주하지 않고 workbook의 종료 구분을 사용한다.

공개 코드에는 원관측·시편별 변환 행·전체 이력을 포함하지 않는다. 자료를
재취득하는 카탈로그·검사 코드·집계 및 출처 고지는 제공한다.

## 검증과 후속 연구

29개 새 자동 검사와 별도 실제7모델 호출을 확인했다.12개 독립 수치 기준은
정상 크리프·시간 관례·손상·온도 배율·산화막, 작성 로그 수명의 응력 지수3과
산포, 중앙수명, 독립 erfc 조건부 확률 및 HRA 단일 사건의 보존을 포함한다.
미검토/미상·범위 이탈·이력 공백·변조·나이/기간 불일치·결정론 손상의
확률 오인과 요구 시 연간 환산을 거부하는 경로도 확인했다.

분포와 생존함수 관계는 [NIST 로그정규 설명](https://www.itl.nist.gov/div898/handbook/apr/section1/apr164.htm),
검열을 포함한 수명 적합의 비교 관점은
[NIST 검열 적합 예제](https://www.itl.nist.gov/div898/handbook/apr/section4/apr413.htm)를
참조한다. 문장·표·예제 데이터를 가져오지 않았다. 실제 적합은 원 엔진을 호출한다.
로그정규가 항상 우월하다는 주장은 하지 않는다. 다른 분포·비모수 비교,
모수 불확실성·고장기구 전이·다온도 검증은 시편 단위의 별도 평가가 필요하다.

공통 기간 위험 어댑터의 실제 다사건 검증은 PERIOD_RISK_KO와 별도 실행 기록을 따른다.
다음 작업은 PI-X04의 모수 불확실성,
변동 조건·다온도·제어 방식·크리프–피로·현장 전이 근거다. 전체 기술 완료율과
독립 현장 검증 완료율은 구분한다.
