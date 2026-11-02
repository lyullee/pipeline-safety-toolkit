# 전기화학·부식·도장·전기방식 교환

`pipeline_integrity.electrochemical_exchange`는 기존 matintegrity의 일곱 계산을
재사용한다. 이쪽은 입력 의미·근거·검토·시계·원 응답의 연결을 담당한다.
원 계산식·설계 표·전기방식 기준을 복제하지 않는다. 계산 결과는 연구용이며
보호 전위·규격 적합·실제 부식속도·잔존수명·파손확률의 승인이 아니다.

## 연결한 원 모델과 검토

| 원 model_id | 입력 의미와 필수 검토 |
|---|---|
| corrosion.coupon_loss | 세척 보정 후 순 질량손실, 노출 면적·밀도·기간 |
| corrosion.faraday | 양극 용출 부분전류 또는 명시한 조건부 icorr, 원자가·밀도·전류 효율 |
| corrosion.charge_to_loss | 용출 전하, 전자당 당량·밀도·효율 |
| corrosion.stern_geary | 활성화/선형 분극 적용성, Tafel 기울기와 면적 정규화 |
| corrosion.galvanic_linear | 공통 기준전극·호환 표면/환경·허용 선형 과전압 |
| protection.coating_coverage | 건조막/체적 고형분 기준·도포 전달 효율 |
| protection.cathodic_budget | bare fraction/요구 전류 기준·양극 용량/이용률·최소 전류/설치/간섭 |

필수 검토 키는 `METHOD_REVIEWS[method]`로 조회한다. 빈 dict가 기본이다.
사실이 확인된 검토와 조건부 시나리오의 가정을 evidence에서 구분한다.
키가 해당 모델에 속하지 않으면 입력 오류다. 원 모델의 스키마·버전·단위·
확률 의미를 MaterialJob으로 고정한 뒤 strict JSON으로 실제 원 API를 호출한다.
원 응답·보류 상태·범위 이탈 결과를 바꾸지 않고 전체 receipt와 해시를 남긴다.

일곱 항목은 interface_verified로 집계한다. 선언한 경계의 연동과 기준 계산을
확인했으며 독립 현장 시험이나 원 모델 전체의 자격 검증을 뜻하지 않는다.

## Python API

`ElectrochemicalRequest`에 method, asset/location/clock/condition, material_id,
environment_id, current_meaning, parameters, applicability_reviews, evidence_ids를
전달한다. 원 입력은 SI와 원 스키마를 따른다. 미상 재료/환경·검토·None 입력은
needs_data다. 양극/음극 자유전위를 쓰는 갈바닉 계산은 공통 potential_reference_id가
필요하다. 단위가 없는 전극 기준을 임의로 표준 수소전극 등으로 바꾸지 않는다.

```python
import runpy
from pipeline_integrity.electrochemical_exchange import evaluate_electrochemical

example = runpy.run_path("examples/electrochemical_methods.py")
cases, context = example["make_cases"]()
request = cases["corrosion.faraday"]
result = evaluate_electrochemical(request, context)
print(result["status"], result["values"], result["output_units"])
```

검토를 마친 계산에는 별도 원 matintegrity 설치/소스 경로가 필요하다. 공개
예제의 재료 수치·전류·용량·기간·도장 효율은 독립 작성한 가정이다. 원 설계
기준이나 공급자 자격 값을 제공하지 않는다. 원 엔진 내부 오류를 표본 삭제나
정상 결과로 바꾸지 않는다. output_origin은 simulation이며 원 입력/근거의
origin과 구분한다. native_receipt는 원 MaterialReceipt 그대로 보존한다.

## 전류의 의미

current_meaning은 anodic_dissolution, conditional_corrosion_current_estimate,
protective_demand, net_external_electrode_current, not_applicable, unresolved를
구분한다. 보호 요구 전류를 용출량 계산에 넣거나 순 외부 전극 전류를 전체
금속 용출 전류로 읽으면 out_of_scope다. 부호를 없애는 절댓값 변환으로 이
구분을 해결하지 않는다. Stern–Geary의 icorr도 조건부 추정값이다.

Faraday와 전하 변환의 전류 효율·전자당 당량은 사용자 근거다. 갈바닉 결합
전류는 자기 부식과 별개이며 선형 분극 범위 밖의 값은 보류한다. 현재 원 엔진의
곡선 기반 혼합전위 모델은 후속 후보지만, 이 자료의 면적·기준전극·분기·조건
공백을 해소한 뒤 별도 실제 연동을 검증해야 한다. 단순 선형 결과를 비선형
혼합전위나 과도 안정성 해석으로 확장하지 않는다.

## SignalFrame 전류 이력

`reduce_surface_current(frames, SurfaceCurrentReduction(...), context)`는 원 프레임의
stream·시계·채널 순서·단위·origin을 유지한다. A/m2로 명시적 단위 환산을 하며
시간순 정렬·재표본화·gap/마지막 꼬리 채움은 없다. point-linear 전류밀도를
적분하고 선언 노출 면적으로 전하를 계산한다. 최대 간격과 시간 허용오차는
각각 maximum_interval_s와 time_tolerance_s(기본1e-9초)로 지정한다.

노출 면적을 추정하지 않는다. 결측/무효/긴 gap은 미해결 구간을 남기고 알려진
구간의 전하만 표시한다. 이때 전체 계산용 전하로 쓰지 않는다. 음의 용출/보호
요구 전류도 원값을 보존해 out_of_scope로 남긴다. 순 외부 전극 전류는 부호를
유지한 수학적 적분이며 총 용출이 식별된 것으로 표시하지 않는다.

history_record를 evaluate_electrochemical에 붙이면 전하 변환 또는 전기방식
예산에만 사용할 수 있다. 원 context·자산·위치·조건·시계와 전하 또는 시간/
전류/면적의 일치를 검사한다. full_exposed_surface 기준의 요구 밀도에 bare
fraction을 다시 곱해 전류를 줄이는 입력은 거부한다. bare_surface 밀도이면
별도로 선언한 bare fraction을 원 엔진이 보간한다.

## 전기방식 예산과 도장량의 한계

원 cathodic_budget는 선형으로 변하는 bare fraction과 전류밀도의 곱을 정확히
적분하고 구간 내부의 최대 전류도 확인한다. 시간별 요구 전하·연속 필요 질량·
전하/순간 전류별 최소 양극 수와 설치 여유를 반환한다. 일정한 양극 전류를
공유한다는 설치/간섭 가정과 용량·이용률·최소 공급 전류는 검토한 외부 입력이다.
보호 전위·전해질 저항망·차폐·과보호·고갈에 따른 전위 변화는 별도다.

도장 계산은 건조 체적·습윤 두께·필요 도료량이다. 그 값을 실제 휴일/결함
면적비 또는 시간에 따른 피복 열화로 바꾸지 않는다. 시편 질량손실은 평균
손실이며 피팅 최대 깊이·미래 성장률·두께 잔량의 정답이 아니다.

## 공개 자료 적용

Vontorová·Novák·Váňová의
[Zenodo v3, DOI 10.5281/zenodo.13886478](https://zenodo.org/records/13886478) 자료에서
워크북4개와 README1개, 총315521바이트를 확보해 공급자 MD5와 로컬 SHA-256을
검증했다. CC BY 4.0과 원 저자·버전·출처를 기록한다. 기존 원본 캐시를 재사용했으며
원 자료·그대로 읽은 문장·변환 행은 코드 릴리스에 포함하지 않는다. 더 새로운
판이 표시되어도 이 검증의 원본을 자동 교체하지 않는다.

load_galvanized_tafel_summary는 고정한 원 Tafel 표의24개 요약을 읽는다.
전해질은 원 표의0.1M HCl·0.1M H2SO4·5% NaCl이다. 염수 농도를 추정해 다른
단위로 바꾸지 않는다. mA/cm2→A/m2의 배율10, 음의 cathodic 기울기와 별도
양의 크기를 보존한다. ± 통계의 종류와 독립 반복 수는 미상으로 남긴다.

원 분극저항은 Ω이고 원 전극 면적은 이 표에서 확인되지 않았다. Ω·m²로
임의 변환하거나 icorr에서 면적을 역산해 검증값으로 사용하지 않는다.24개
Stern–Geary 입력은 None을 유지해 needs_data로 보류했다. 비도금12개는 보고된
icorr 추정치와 가정한 Fe²⁺·밀도7850kg/m3·효율1·100000초를 기존 Faraday에
전달했다. 이는 조건부 계산이며 실제 용출 전류·실험 부식속도/기간·현장 검증이 아니다.
도금 시편의 전류를 철의 단일 용출 반응으로 자동 변환하지 않는다.

Michael Trask의
[포화 potash brine 탄소강 배관 보호 전류 V2](https://data.mendeley.com/datasets/z6m27978vj/2)는 CC BY 4.0 원본을 취득하고 공급 SHA-256/길이와 대조했다. 별도 `potash_pipe_cp` reader는 반복·보고 평균·95% 구간 및 단위 환산을 보존한다. `to_signal_frames`는 사용자가 고른 보고 평균 또는 단일 반복을 기존 `SignalFrame`으로 연결하고, 원 출처·해시·라이선스·실험실 한계를 `EvidenceRef`에 남긴다. 결과 프레임의 origin은 laboratory다.

이 연결은 데이터 형식 통합이며 현장 전류 설계 적용을 뜻하지 않는다. 전극 전위, 노출 면적, 온도, 기하 및 보호 기준이 완결되지 않아 양극 예산 입력을 자동 생성하지 않는다. 기존 `reduce_surface_current`를 통한 적분도 선언 시간축의 전하 요약일 뿐 보호 적합성이나 현장 설계를 확정하지 않는다. 정체 배관 자료와 유속 potash elbow 자료는 별개의 실험이며 합쳐서 반복수를 늘리지 않는다. 자료의 취득·구조·범위는 [POTASH_PIPE_CP_DATA_KO](POTASH_PIPE_CP_DATA_KO.md)에 기록한다.

## 재현과 검증

```powershell
python scripts/acquire_data.py --only galvanized_polarization --workers 1
python scripts/check_electrochemical.py --materials PATH_TO_MAT
python -m pytest tests/test_electrochemical_exchange.py -q
```

실제7모델·독립 수식10기준·전류 이력의 전하/보호 예산·미검토/범위 이탈을 확인했다.
특히 bare fraction0→1과 밀도1→0의 작성 사례는10m2·4초에서 전하40/6C,
중간2초의 최대 전류2.5A다. 끝점 전류만으로 양극 수를 정하지 않는다.
원 재료 소스의 전후 해시가 같음을 확인했다. 원 격리·스키마·입출력·근거와
22새검사의 세부 결과는 research/electrochemical_results.json과 VERIFICATION을 따른다.

다른 저장소의 후속 요구는 PI-X04에 원 기준전극·전해질·전극 면적·부분전류/
효율·분극 곡선 분기·보호 전위/저항망·양극 출력 지원 범위로 요청했다.
계산식 연동을 현장 자격이나 전체 부식·전기방식 완성으로 집계하지 않는다.
