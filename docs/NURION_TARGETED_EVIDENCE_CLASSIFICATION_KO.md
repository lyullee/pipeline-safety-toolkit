# 누리온 집중 검증 증거 분류

기준 시각: 2026-10-30 KST. 이 문서는 이미 끝난 Nurion campaign의 JUnit/receipt 집계와 현재 checkout의 대응 테스트 코드를 나눠 해석한다. 각 module의 통과 수는 실행 증거이며, 그 자체가 관련 API 전체나 174개 capability의 qualification은 아니다.

## 증거 수준

| 분류 | 포함되는 증거 | qualification에 필요한 추가 연결 |
|---|---|---|
| 독립 기준/해석해 | 별도 닫힌식, 별도 계산 절차, 명시된 기준값과 비교 | capability/API, 입력영역, 단위, 오차 허용치를 카탈로그 단위로 연결 |
| 보존/수지 | 질량·에너지·힘·모멘트 또는 수두 잔차를 기준에 대조 | 기준식의 독립성과 영역별 한계, 누적 오차 기준 연결 |
| 수렴/격자 | timestep, grid, partition 변화에 따른 안정성과 수렴 | 수렴 차수/허용기준, 비정상 구간과 보간 한계 연결 |
| 사건/상태 전이 | 시작·정지·역전·도달·임계 조건의 순서 및 사건 브래킷 | 사건 발생/미발생 경계와 미관측 구간의 의미 연결 |
| 계약/경계 | 입력검증, 증거 결합, owner 위임, receipt 재생성·변조 거부 | 물리 계산값을 검증하지 않으므로 독립 물리 증거와 별도로 집계 |

한 테스트가 둘 이상의 분류를 가질 수 있다. 모델/API 전체가 물리적으로 검증됐다고 간주하지 않고, assertion이 실제로 대조하는 항목만 기록한다.

## 완료된 집중 실행

| PBS job | 모듈 / JUnit 통과 | 코드에서 확인되는 직접 증거 | 해석 제한 |
|---|---|---|---|
| 24236850 (wave49) | cyclic fatigue 60, corrosion/wear 40, fracture arrest 25; 총 125/125 | 입력·근거·단위·population·source pin·재생 검증 및 미지 값 보존. fracture arrest에는 샘플 시점별 기준 비교와 큰 간격에서 윈도우를 끊는 동작이 있음 | 대부분 owner contract와 적용경계. 외부 fatigue/corrosion 계산의 물리 적합성을 증명하지 않음 |
| 24236854 (wave50) | buried thermal 42, heated transport 13, pump transient 8; 총 63/63 | 원통 열저항·접촉저항의 닫힌식, 일정 cp 열수송 기준값, 열/압력일의 분리 및 전역 에너지 장부 검사 | pump transient는 외부 owner 응답 장부와 boundary 보존 위주. 실 pump curve validation 아님 |
| 24236855 (wave51) | pipe-soil transient 40, pressure-wave pair 50, valve transient 10; 총 100/100 | pressure-wave에서 거리/파속의 독립 시간 기준, valve transient에서 부호 있는 오리피스·음향 점프·격자 refinement 비교 | pipe-soil 대부분은 위임 경계/receipt. 압력파의 사건 원인 또는 현장 누출의 ground truth를 증명하지 않음 |
| 24236858 (wave52) | gas network 23, lifting 47, pig motion 18; 총 88/88 | lifting 독립 힘·모멘트와 좌표변환 불변성, pig motion의 piecewise analytic 전이·일-에너지 관계, gas network closure/수지 경계 | gas network의 일부 경로는 native solver 또는 입력 evidence에 의존. lifting 정적 평형은 동적 rigging/승인 검토 아님 |

세 wave50–52는 합계 251건이다. 이 251건은 **focused regression 통과 건수**로 저장하고 capability qualification 분자에 더하지 않는다. wave49 125건은 별도로 계약/경계 회귀로 유지한다.

## 실행 상태와 비완료 항목

- wave46은 169/169 모듈, 3,427 JUnit 노드를 끝냈다. 3,408 통과, 실패·오류 0, skip 19라 strict no-skip 게이트는 실패했다. potash skip 1건은 별도 wave47에서 1/1 통과했다. 남은 18건은 선택적 풍하중 consequence 저장물이 wave46 workspace에 없어서 생긴 skip이다.
- wave53 (24236879.pbs)은 최종 PBS `F`, Exit_status=0으로 끝났다. controller 출력 기준 tank 30, slurry 30, water quality 2로 총 62건이 통과했고 실패·오류·skip은 0이다. source 및 wheel 체크섬 검사는 통과했지만 scratch의 wave53 폴더에 raw JUnit/receipt가 보존되지 않아 rank별 증거 해시 재검산은 불가능했다.
- wave54는 74 pass, 9 skip, 실패·오류 0이다. skip 9건은 `lh2dt` owner map 누락 때문에 native gas thermal 8건과 tank breathing 1건이 실행되지 않은 것이다. 따라서 strict no-skip gate 실패로 기록했다.
- wave55는 3개 rank manifest에 worker `--size 1`을 전달해 worker receipt가 만들어지지 않은 pretest 설정 오류다. 테스트 0건이며 모델 결과가 아니다.
- wave56 (24236970.pbs)은 교정 owner map과 manifest에 맞는 rank size 3을 사용해 PBS `F`, Exit_status=0으로 끝났다. controller 출력 기준 83건 통과, 실패·오류·skip 0이며 source와 wheel 체크섬 검사도 통과했다. scratch의 wave56 폴더에는 raw JUnit/receipt가 보존되지 않아 하위 증거 해시 재검산은 불가능했다.
- 24235721.pbs는 `prepared_not_executed` 요약과 PBS Exit_status=1을 남겼으며, 테스트 0건인 설정/인프라 실패로 분류한다. wave55 (24236967.pbs)도 rank-size mismatch로 테스트 0건이었다. 둘 다 물리 모델 실패로 세지 않는다.
- 2026-10-30 10:49 KST 기존 Nurion 공유 wrapper의 read-only 조회로 위 결과를 확인했다. 새 인증이나 새 Nurion 제출은 하지 않았다. 기존 Nurion 계산은 끝났으며, 다음 신규 검증은 Hangang CPU로 전환한다. Hangang 연결과 Slurm은 확인했지만 현재 project bundle이 없으므로 새 제출 전 source bundle 전송 및 체크섬 확인이 선행되어야 한다.

## 진행률 의미

현재 독립 물리 capability qualification은 **0/174 (0%)**로 유지한다. 집중 회귀와 전체 suite가 여러 모델의 기준 비교·보존·수렴·사건 동작을 확인했어도 아직 결과 노드를 카탈로그 capability/API, 적용영역, 독립 기준, 허용오차와 완전하게 연결한 acceptance evidence ledger가 아니다. 반대로 0/174는 실행한 테스트가 하나도 없다는 뜻도 아니다. 각각의 관측값을 혼동하지 않도록 전체 suite, focused regression, capability qualification을 따로 추적한다.

## 다음 재개 순서

1. Hangang CPU에 현재 source bundle을 전송하고 SHA-256으로 출처/무결성을 확인한다.
2. 기존 PBS 결과에서 raw JUnit/receipt가 보존되지 않은 제한을 기록하고, Hangang 신규 campaign부터는 각 rank JUnit, receipt, provenance, 집계 summary를 공유 scratch로 복사하도록 controller를 구성한다.
3. Hangang Slurm에서 해당 campaign을 다시 실행해 보존된 개별 증거와 통계를 확인한다.
4. 통과한 test node를 capability evidence crosswalk에 연결하고, 독립 기준/보존/수렴/사건 검증이 없는 API는 별도 누락으로 남긴다.
