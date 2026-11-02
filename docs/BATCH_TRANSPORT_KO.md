# 다품종 액체 배치 수송 공급자 경계

`batch_transport`는 다품종 액체 수송 방정식을 새로 구현하지 않는다. 외부 소유
해석기가 계산한 시간–위치 조성·물성장과 질량 원장을 검증해 공통 연구 기록으로
바꾸는 typed adapter다. 기존 `water_quality`는 일정 밀도 물의 희석 성분/출처/나이
모델이며 석유 제품 배치 수송의 대체물이 아니다.

## 요청

`BatchTransportRequest`는 asset/location/clock, 경로 길이와 단면적, 기간, 2개 이상
component ID, 초기 조성, 0초에서 시작하는 inlet step, 각 step의 체적유량·조성·온도·
절대압, property basis와 dispersion model ID를 받는다. geometry/boundary/property
evidence ID는 실제 `RunContext.evidence`에 있어야 한다. 시간/위치 점 수와 절대/상대
질량수지 허용오차도 호출자가 지정한다.
총량과 각 성분의 `numerical_adjustment` 절대 상한도 요청에서 따로 지정한다. 잔차
허용오차와 보정항 상한은 서로 다른 계약이며, 큰 보정항으로 손실을 숨긴 뒤 잔차만 0으로
만드는 결과는 통과하지 않는다.

열 처리는 `prescribed_temperature`와 `energy_coupled`를 구분한다. 전자는 온도장을
공급 경계/해석 결과로 보존하지만 에너지 보존을 주장하지 않는다. 후자는 thermal model/
evidence ID와 절대·상대 에너지수지 허용오차를 반드시 지정하며 해당 evidence가 실제
`RunContext`에 있어야 한다. 에너지 `numerical_adjustment` 상한도 별도로 지정한다. 쓰지
않는 열 설정을 전자 모드에 넣어 모호한 주장을 만드는
것도 거부한다.

모든 조성은 같은 component set을 정확히 포함하고 합이 절대오차 1e-12 안에서 1이어야
한다. inlet 시각은 elapsed 0에서 엄격 증가하고 마지막 변화는 분석 종료보다 앞선다.
이는 step-held 경계 정의이며 공급자가 어떤 보간이나 수치법을 쓰는지는 provider
source/version 계약에 속한다.

## 공급자 출력

provider는 id/version/source digest와 정확한 request digest를 반환한다. 결과에는
0과 종료를 포함한 증가 time grid, 0과 route length를 포함한 증가 position grid,
`time_major_position_minor` 순서의 성분별 fraction, density, dynamic viscosity,
temperature, absolute pressure 및 signed mass ledger가 필요하다. 각 격자점의 fraction
합을 검사하며 물성장은 양의 유한값이어야 한다.
provider `source_digest`는 임의 문자열이 아니라 소문자 64자리 SHA-256이어야 한다.

총량과 각 성분 질량 원장의 부호는
`initial + inlet - outlet - final + numerical_adjustment = residual`이다. residual이
요청한 절대+상대 예산을 넘으면 결과는 `withheld`이며 값을 숨기지 않는다. 이 경계는
각 성분 원장의 합이 총량 원장의 다섯 항목과 각각 일치하는지도 검사한다. 따라서 한
성분의 손실을 다른 성분의 인위적 생성으로 상쇄해 총량 수지만 닫는 결과도 성공으로
통과하지 않는다. 이 경계는
밀도/점도식, axial dispersion, interface tracking, 열전달, 상변화 또는 피그 모델을
복제하지 않는다. `constant_density_or_water_quality_assumed=False`를 기록한다.

`energy_coupled` 결과는 별도의 signed energy ledger를 요구한다. 부호는
`initial + inlet - outlet - final + heat_transfer + shaft_work + numerical_adjustment = residual`이며
잔차가 호출자 예산을 넘으면 질량수지가 닫혀도 결과를 `withheld`한다. 질량수지와
에너지수지 상태는 각각 기록하고 최종 `conservation_within_tolerance`에서 결합한다.
잔차가 닫혀도 질량·성분·에너지 수치보정 중 하나가 호출자 상한을 넘으면 보류한다.
`prescribed_temperature` 결과가 검사하지 않은 energy ledger를 끼워 보존을 암시하는 것도
거부한다.

`verify_batch_transport_record`는 같은 provider를 다시 호출해 요청·문맥·source와 결과를
재생한다. 무서명 digest는 공급자 신원이나 현장 정확도를 보증하지 않는다.

## 검증 상태와 후속

작성한 stub 회귀는 조성 closure, grid, 물성, 총량·성분별 질량수지 보류, 성분 합과
총량 원장 일치, 열모드와 에너지수지 보류, 과도한 수치보정 차단, evidence와 provider
replay를 다룬다. 사용자
지시에 따라 pytest와 수치
해석은 누리온 대기다. 실제 제품 조성/물성,
경계·채취 자료, 독립 interface 도달/혼합 오염 검증과 owner solver 확장은 남는다.
상태는 `research/batch_transport_adapter_status.json`을 따른다.

