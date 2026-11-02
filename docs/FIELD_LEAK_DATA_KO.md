# 현장 누설 캠페인 수두·유량 자료

[Zenodo23622401](https://zenodo.org/records/23622401)의 CC-BY-4.0 고정 자료를
`andean_controlled_leaks`로 확보했다. 출처/저자/라이선스/공급자 체크섬은 catalog와
reader provenance에 보존한다. 원 Excel·변환 기록은 data/raw와 artifacts에 유지하고
소스 배포에 포함하지 않는다. 공급자가 제공한 R 프로그램을 실행하거나 복제하지 않는다.

## 실제 형식과 현재 실행 범위

Q_P_80_Leak1.xlsx는 실제80관측, Q_P_46_Leak2.xlsx는46관측이며 각파일뒤에8개
비관측 행이 있다. 실제 리더로126개 원 관측을 읽었고 원 행 번호·빈 행·열을 보존했다.
이 실행은 원자료 형식 읽기이며 수리 계산·탐지기 학습·성능평가·회귀시험이 아니다.
새 프레임 변환/monitoring 연결과 회귀는 누리온 실행 대기다.

압력 열은 m 단위로 기록되어 있다. Pa·절대압·수두 datum·밀도나 중력을 추정하지 않는다.
유량은 m³/s이며 원 부호와 결측을 유지한다. 원 날짜와 별도 TIME 셀을 결합한 naive
calendar와 각 캠페인 첫 관측에서의 상대 elapsed만 보존한다. 시간대/UTC나 두
캠페인의 공통 동기화 시계를 만들어내지 않는다. 실제 간격30초는 센서의 평균 노출
구간30초를 뜻하지 않으므로 평균폭을 자동 설정하지 않는다.

controlled leak indicator는 별도 source annotation이다. 0을 장치전체 verified_normal,
1을 검출기 출력·자발누설 원인·독립 정답/POD로 변환하지 않는다. 두 캠페인과126행을
독립126실험으로 세지 않는다.

## 리더와 재현

`field_leak_data.read_field_leak_workbook(path)`는 기존 bounded XLSX/원본 pin helper를
재사용한다. 원본 크기/체크섬·고정 시트/헤더/관측 수·catalog digest·reader/dependency
소스·openpyxl 버전을 보존한다. raw식/비수치 결측을 0이나 계산된 셀 값으로 채우지 않는다.
source_spec을 명시 제공하는 시험/별도검토 사례에서도 declared origin을 보존하며
simulation을 measured로 내보내지 않는다.

`verify_field_leak_record(record, source_root=...)`는 안전한 profile basename을
확인하고 현재 원본을 다시 읽어 비교한다. 같은 root 밖의 symlink/경로를 읽기 전에
거부한다. unsigned 해시는 원 시스템의 서명/신원 인증을 대신하지 않는다.

## 명시적 관측 매핑

`FieldLeakMapping`에 source_record_digest, asset/stream/clock/site, head/flow sensor와
port ID, source/mapping evidence를 선언한다. RunContext는 원 source origin과 같은
asset/clock을 가지며 metadata의 field_leak_boundary에 `field_leak_boundary(mapping)`을
넣는다. source EvidenceRef의 SHA가 있으면 실제 workbook SHA와 같아야 한다.

수두/유량 definition, source relative clock, temporal_support와 report-to-available
offset 및 clock_error_bound를 검토하기 전에는 frames를 반환하지 않는다.
기본 상태는 unresolved다. 실제 source가 point report인지 trailing interval mean인지,
평균폭/가용시각 지연이 얼마인지 원 계측 근거를 확인한 뒤 명시해야 한다.
값을 넣었다는 사실을 자동 현장 승인으로 취급하지 않는다.

`bind_field_leak_source(record, mapping, context, source_root=...)`는 원본을 재확인하고
SignalFrame과 권위있는 sidecar를 만든다. frame은 보고시각+검토된 지연+clock error의
최신 가용 상한에 위치하며 작은 양의 오차가 float 덧셈에서 사라지면 필요한 최소
representable 시각으로 보수 상향한다. 평균폭·원 calendar·상대 노출·sensor/port·
controlled annotation은 sidecar에 보존한다. source-relative exposure가 첫 관측 전이면
음수 상대시간을 보존하며 관측을 새로 생성하지 않는다.

frame 채널은 pressure_head(m), volume_flow(m³/s)뿐이다. 결측은 None/valid=False이고
indicator는 특징값에 넣지 않는다. `verify_field_binding`은 원 자료와 매핑을 재생한다.

## 기존 모니터링에 연결

`to_field_monitoring_split(binding, source_root=..., role=..., split_id=...,
physical_run_id=..., source_group_id=..., observation_ids=...)`로 원 관측 ID를 순서대로
선택한다. 자동 정렬·임의 sample split·reference 정상 판정을 하지 않는다.
기본 reference_kind는 unresolved다. source campaign/group과 실제 event/site를
검토해 기존 `monitoring_exchange.evaluate_monitoring(request, splits, context)`에 전달한다.
학습·교정·평가의 겹침·chronology·heldout 근거와 검토는 원 monitoring 계약을 따른다.
같은 clock/채널이라도 센서 위치·수두 datum·시간응답이 다르면 같은 population으로
자동 묶지 않는다. 필요한 원 모듈 확장은 PIPE-EXT-005와006을 따른다.

## 취득 및 스크립트

```bash
python scripts/acquire_data.py --only andean_controlled_leaks
python scripts/review_field_leak_source.py --source-root data/raw/andean_controlled_leaks --output artifacts/field_leak/source_review.json
```

기본은 원자료 읽기만 한다. 단일 workbook과 명시 mapping/context JSON을 함께
제공한 경우에만 프레임 바인딩을 호출한다. 탐지기나 위험 계산을 자동 실행하지 않는다.
source output에는 원자료가 포함되므로 artifacts 같은 비배포 경로에 보관한다.

원 workbook/매핑/명시 원행 선택·결측·시계/기원·변조/경로 이탈 회귀를 작성했지만
실행하지 않았다. 실제 source-data record와 모델/현장 검증을 구분한다.


## 세 단계 owner 모니터링 호출

`FieldMonitoringSelection`에 binding_digest, 원 observation_ids, stage role,
physical_run_id/source_group_id/reference_kind를 명시한다.
`field_monitoring_boundary(request, bindings, selections)`를 RunContext.metadata의
field_monitoring_boundary로 바인딩한 뒤
`evaluate_field_monitoring(bindings, selections, request, context, source_root=...,
client=... 또는 owner_source_root=...)`가 기존 PMT gateway를 실제 호출한다.
수치/학습 엔진을 새로 구현하지 않으며 필수 근거가 미확인일 때 원 gateway가 보류한다.

세 단계의 센서/site/port·수두/유량 정의·시간응답이 다르면 같은특징으로 묶지 않는다.
chronological_within_run은 하나의 실제 원 campaign을 유지해야 하고, 서로 다른 이름만
붙여 같은 workbook을 separate_physical_runs 세개로 만들 수 없다. 현재 원파일은 두
캠페인이므로 별도3-run 평가에 필요한 source를 이 wrapper가 만들어내지 않는다.
다른 원자료의 독립 reference를 쓰려면 각 source 계약을 보존한 native MonitoringSplit
통합 경계를 사용한다. 어떤 분할도 자동 생성하지 않는다.

`verify_field_monitoring_record`는 원자료/매핑/선택을 재확인하고 PMT의 원 semantic
verifier를 호출한다. detector를 다시 fit하는 함수가 아니며 과거 실행 신원을 보증하지 않는다.
새 세 단계 호출·보류·재선택·실제 campaign 중복 거부 회귀도 작성만 했고 실행하지 않았다.


chronological 한-source의 세 단계는 같은 analysis_start/availability offset/clock error
매핑을 유지한다. stage별 재시각화로 원 순서를 바꾸지 않고 원 elapsed/report와 노출
지지영역의 순서도 확인한다. 서로 다른 observation ID라도 평균 노출이 split 경계를
가로질러 겹치면 거부한다. 이 검사는 단계 경계의 자료누수만 방지하며 원 관측의
독립 오차나 정상 reference를 증명하지 않는다.
