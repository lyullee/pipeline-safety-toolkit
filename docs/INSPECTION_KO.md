# 반복 검사 자료와 재료 평가 연결

이 기능은 반복 ILI 관측의 단위를 맞추고 좌표 변환에 따른 결함 대응 후보를 만든다.
검토된 경로의 두께 추세는 기존 `matintegrity`에 전달한다. 위치 정합 비용을 동일
결함일 확률로 바꾸지 않으며, 미대응 관측을 새 결함·소멸·검출 실패로 자동 분류하지 않는다.

## 자료와 재현

사용 자료는 Rioshar Yarveisy, Faisal Khan, Rouzbeh Abbassi의
[공개 반복 배관 검사 자료 V1](https://doi.org/10.17632/c2h2jf5c54.1)이며 CC BY 4.0이다.
원본과 정규화된 관측은 공개 배포에서 제외하고 취득·해시·집계·코드를 제공한다.
전체 실행 증거는 `research/ili_application.json`에 기록한다. 부분 읽기 실행은
`artifacts/ili_application/partial_application.json`에 남아 전체 실행을 덮어쓰지 않는다.

```powershell
python scripts/acquire_data.py --only ili
python scripts/apply_ili.py --materials PATH_TO_MATINTEGRITY --joints 14 --reuse-scans
```

기본 실행은 네 통합문서의 전체 행을 읽고 지정한 용접부의 특징을 보관한다.
`--maximum-source-rows 100`처럼 읽기 범위를 제한하면 부분 자료로 표시하며
미대응 특징을 계산하는 정합 단계로 넘기지 않는다. 캐시는 원본 해시·요청·
판독기 코드·관측 계약·openpyxl 버전 및 저장한 내용의 해시를 확인한 후 사용한다.
원자료에 포함된 분석 프로그램은 실행하지 않는다.

## 원본 의미와 단위

`GWNUM`은 용접부 식별자다. `RD.m`은 이음부 내 상대거리이고 `SPO.deg`는
특징 시작점의 원주각이다. 절대거리 `SPD.m`의 원점은 회차마다 다르므로 기본
정합 좌표로 쓰지 않는다. `SIPRD.m`의 열 이름과 설명에 단위 충돌이 있어 이
판독기는 해당 좌표를 정합에 쓰지 않는다. 원주각의 영점은 별도로 등록해야 하며 ENU가 아니다.

관벽·최대 깊이·특징 길이·폭은 mm에서 m로 변환한다. 원주각은 rad로 바꾸며
360°는 0°와 같은 방향이다. 결측 폭·길이는 None으로 유지한다. 잘못된 행은
행 번호와 이유를 남기고 제외 수를 기록한다. 깊이가 명목 벽 두께 이상인 관측은
정합에 쓸 수 있지만 양의 잔여 두께를 요구하는 추세 계산은 보류한다.

검사 라벨 1·3·5·7은 원 자료의 시점 구분이다. 실제 설비 나이·달력 시각·초 단위
경과시간으로 자동 변환하지 않는다. `inspection_time_s`, `clock_id`,
`time_basis_evidence_id`는 검토한 시간 기준이 있을 때 함께 공급한다.

## 계약과 정합 방법

`ILILoadRequest`와 `load_ili_run`으로 읽고 `InspectionRun`을 받는다. 사용자가
직접 만든 자료도 `InspectionFeature`와 `InspectionRun`으로 같은 API에 공급할 수 있다.
`feature_id`는 해당 관측의 식별자이며 회차 간 실제 결함 ID가 아니다.

`InspectionRegistration`에는 원/대상 run·좌표 프레임·명시적인 용접부 대응,
축방향 좌표 방식·오프셋·원주각 오프셋·변환 근거를 넣는다. 변환은 원 회차에서
다음 회차 방향이다. `joint_distance`는 거리 이동, `fraction_of_joint_length`는
원 이음부 길이로 나눈 상대 위치를 대상 이음부 길이에 곱한 후 이동한다.
이 길이 비 변환은 선언한 모델이며 실제 관 변형 또는 거리 교정의 증거가 아니다.

```python
from pipeline_integrity.inspection import match_inspections

# proposals = match_inspections(baseline, followup, registration, policy)
```

같은 등록 용접부 안에서 축 오차와 순환 원주각 오차를 각각의 gate로 제한한다.
후보 비용은 두 정규화 오차의 제곱 합이다. 깊이·잔여 두께·가정한 성장률은
목적함수에 넣지 않아 성장 관측에 유리하도록 대응을 맞추지 않는다. 후보 밖
연결은 금지하고 각 원 관측에 미대응 선택을 추가해
[SciPy의 선형 배정 해법](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.linear_sum_assignment.html)으로
일대일 비용 합을 최소화한다. 행렬 크기 예산은 용접부별로 검사한다.

`FeatureMatchPolicy`의 gate·미대응 비용·모호성 간격은 사용자가 정한다.
예제의 0.25 m·20°·비용 2.5는 실행 가정이며 계측기 정확도나 공인 기준이 아니다.
선택한 쌍과 같은 행/열의 다른 후보·미대응 선택 간 비용 차이를 국소 간격으로
반환한다. 이 값이 작거나 음수이면 모호성을 표시한다. 이는 전체 대안 배정의
확률 또는 신뢰구간이 아니다. 분할·병합·군집 모델은 별도 연구 대상이다.

결과의 `unmatched_*`는 등록 범위에서 선택되지 않은 유효 관측이다.
`unregistered_*`는 명시한 용접부 대응 밖의 관측이다. 어느 쪽도 검출 확률이나
물리적 소멸의 판정이 아니다. 원본·변환·정책 해시가 달라지면 대응을 다시 계산한다.
알고리즘 버전·구현 모듈의 SHA-256·Python/NumPy/SciPy/Pydantic 실행 버전도
결과에 보존한다. 이는 계산 재현을 위한 소스 범위이며 독립 물리 검증의 근거를 대신하지 않는다.

## 검토된 경로와 성장 불확실도

`InspectionTrack`은 회차별 관측 ID를 명시한다. 연속 회차마다 원 정합 해시를
참조하는 `CorrespondenceReview`의 승인·검토자·근거가 있어야 한다. 좌표 변환도
`review_evidence_id`가 있어야 한다. 미검토 후보를 코드에서 승인 값으로 채우지 않는다.

실제 owner trend 호출 전 `RunContext.evidence`는 각 run의 `source_id`에 대한 동일 SHA-256과
origin, 모든 `time_basis_evidence_id`, 좌표 등록의 `review_evidence_id`, 승인된 각
`CorrespondenceReview.evidence_id`를 포함해야 한다. EvidenceRef ID 중복이나 어느 근거의
누락·불일치도 `needs_data`로 보류하며 owner를 호출하지 않는다.

`evaluate_inspection_track`은 최소 세 회차, 같은 자산·관측 출처·시계, 증가하는
경과시간, 안정된 명목 관벽 기준과 양의 잔여 두께를 요구한다. 관측별
`remaining_ligament_error_bound_m`는 명목 두께와 최대 깊이의 오차를 포함한
절대 오차 한계다. 표준편차나 신뢰구간으로 해석하지 않는다. 누락된 시간·오차·
정합 검토·제외 관측 문제는 needs_data로 반환하고 재료 추세 함수를 호출하지 않는다.

전달 값은 명목 관벽에서 보고된 최대 깊이를 뺀 추정 잔여 두께다. 최대 깊이의
위치는 특징 내부에서 바뀔 수 있으므로 같은 위치에서 반복 측정한 UT 두께와
구분한다. 명목 관벽 기준이 바뀌면 기본 보류하고, 허용차를 공급할 때 근거를 검토한다.

검사를 통과한 경로는 기존 `corrosion.thickness_trend`에 전달한다. `ols` 또는
`theil_sen`을 선택할 수 있으며 원 응답·부호·가정·통계 구간을 보존한다. 관측된
깊이 감소나 음의 손실률을 0으로 잘라내지 않는다. 인접 회차의 손실 차이에 두
절대 오차 한계의 합을 더하고 빼 시간차로 나눈 최악 경계도 따로 반환한다.
이는 오차 한계가 성립한다는 선언에 의존하며 통계 신뢰구간·파손확률이 아니다.

실제 자료에는 검토된 동일 결함 경로·측정 오차·초 단위 시각을 확정하지 않았다.
후보 정합과 필요한 입력을 보고하며 현장 성장률을 승인하지 않는다. 알려진
위치 변환·선형 손실·시간·오차를 가진 합성 사례로 실제 재료 함수의 왕복 호출과
해석값을 확인한다. 이 합성 실행은 실제 자료의 독립 검증이 아니다.

## 소유 모듈 확장

PI-X04에는 이 계약과 완료 조건을 전달한다. 입력·출력 스키마 검색은 기존
재료 레지스트리의 기능을 사용한다. 정합·계측·검출성·위치가 바뀌는 최대 깊이의
성장 해석은 해당 전문 모델의 검토가 필요하다. 재료 추세·예측·강도 엔진을
이 패키지에 복제하지 않는다.
