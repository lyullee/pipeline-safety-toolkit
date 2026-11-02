# 공개 수질망 이상점수 데이터 기술 분석

## 목적과 적용 범위

이 예제는 공개된 익명 수질망 1개 구역의 시간별 **게시자 산출 이상점수**와
누수 근접 라벨을 출처 고정 상태로 읽고, 호출자가 분석 전에 정한 임계값의
집계 혼동행렬을 산출한다. 압력·유량 원시 센서 판독, 점수 생성기 재현,
임계값 최적화, 독립 성능평가, 인과 분석 또는 현장 성능 주장을 제공하지 않는다.
특히 이 데이터셋의 점수는 이미 표준화/결합된 지표이므로 실제 계측 물리량의
단위나 의미를 부여하면 안 된다.

## 출처와 권리

원본은 [Zenodo 레코드](https://zenodo.org/records/15096167)와 이에 연결된
[Scientific Data 논문](https://www.nature.com/articles/s41597-026-07203-5)에
기록되어 있다. 레코드가 표시하는 CC BY 4.0 조건에 따라 재배포·변형 시 출처,
라이선스 링크, 변경 여부를 표시한다. 저장소에는 원시 CSV를 넣지 않고, 취득
목록과 기술 설명만 둔다. 별도 가공본을 배포할 경우 해당 레코드의 귀속·표시
조건을 다시 확인한다.

검토한 파일은 `input_model_potenc_predXfault7_A.csv` (818,525 bytes,
공급자 MD5 `ae08cf142a6128f9ddc205a2ccbf80a9`, 취득 SHA-256
`5ed3fea7735eb51bdc191c4fc50d60c6e14507e88f43ead799e3016264d6e0f9`)와
`README.txt` (1,441 bytes, 공급자 MD5 `850d7486a5ad81d87021b6e162e17a19`,
취득 SHA-256 `b70c95af86623e8f06aca43087cfc5e24206bcf8cd0d3020677fb5a6fa1f17b3`)다.
실제 파일과 원본 체크섬은 `data/raw/water_pilot/` 및
`artifacts/acquisition/water_pilot.json`에 보관한다. 공개 저장소 배포물에서는
원자료를 제외한다.

## 자료 구조 및 한계

로컬 구조 점검에서 CSV는 8,737개 시간 행과 timestamp, `fault_d7`, 18개 점수
열로 구성된다. 시간 문자열은 `Europe/Bratislava` 현지 민간시각이며, 독자는
이를 UTC로 추정 변환하지 않고 원래 행 순서를 보존한다. 반복 시각은 오류로
합치지 않고 개수만 기록한다. 비어 있는 점수/라벨은 결측으로 유지한다.

`fault_d7`은 기록된 누수와 시점의 ±7일 근접 관계를 나타내며 실제 사건 발생
시각을 제공하지 않는다. 행 단위 무작위 분할은 인접한 시간 라벨이 겹칠 수 있어
독립 평가로 취급할 수 없다. 후속 시계열 연구는 시간 블록 단위로 나누고 라벨
근접 기간을 고려해 경계에 purge 구간을 둬야 한다. 원 논문에서 점수 채널 선택에
누수 라벨과의 연관 정보가 사용되었으므로 같은 표본에서 계산한 지표는 독립
검증이 아니다. 단일 익명 구역의 결과는 다른 자산·연도·운영에 일반화할 수 없다.

## API와 실행

`pipeline_integrity.derived_score_study`는 `DerivedScoreMetadata`로 출처·라이선스·
파일 SHA-256·열·점수 범위·시간대·라벨 의미를 고정한다. `read_derived_score_csv`는
파일 이름과 SHA-256, 헤더, 행 길이, 증가하지 않는 시각, 알려지지 않은 라벨,
비유한 수치, 점수 범위 및 최대 행 수를 확인한다. 원본 순서를 바꾸거나 시계 변환,
결측치 대체를 하지 않는다. `evaluate_score_thresholds`는 사전 검토 근거 ID가
기록된 호출자 임계값만 사용하며, 각 채널의 점수 또는 라벨이 결측인 행을 그
채널 계산에서 제외해 수와 함께 기록한다. `verify_score_threshold_result`는
입력과 설정을 고정한 같은 계산을 재생한다.

### 시간 순서 비교와 purge 구간

`ChronologicalThresholdRequest`와 `evaluate_chronological_score_thresholds`는
호출자가 근거와 함께 현지시각 cutoff를 지정하면, cutoff 전 학습 구간과 이후 평가
구간에 동일하게 고정된 임계값을 적용한다. 사건 근접 라벨이 ±7일이므로 cutoff
양쪽에 각각 최소 7일을 제외하며, 원본에는 시간대가 있지만 UTC 변환·DST 중복시각
해석은 하지 않는다. 결과는 두 구간의 집계와 제외 행 수만 담고 행별 값이나 분할 ID는
내보내지 않는다. `verify_chronological_score_thresholds`는 전체 출처·분할 근거·고정
임계값을 사용해 두 결과를 재생한다.

이 purge는 공개 라벨 기간에 근거한 분할 규칙이며 같은 누수 사건의 분리, 표본의 통계적
독립성, upstream 점수 선택 편향의 제거를 보증하지 않는다. 따라서 이 기능의 결과는
시간 순서 비교이지 독립 holdout 검증이나 모델 성능 승인으로 부르면 안 된다.

```python
from datetime import datetime

from pipeline_integrity.contracts import EvidenceRef
from pipeline_integrity.derived_score_study import (
    ChronologicalThresholdRequest,
    ScoreThresholdRequest,
    evaluate_chronological_score_thresholds,
    verify_chronological_score_thresholds,
)

# `study` is the source-pinned object returned by read_derived_score_csv.
# `context` carries the source and preselected-threshold evidence.
split_evidence = EvidenceRef(
    id="reviewed_time_split",
    citation="Study protocol reviewed before partitioning",
    origin="user_declared",
)
context = context.model_copy(update={"evidence": (*context.evidence, split_evidence)})
split = ChronologicalThresholdRequest(
    cutoff_local=datetime(2022, 10, 1),
    split_evidence_id=split_evidence.id,
)
fixed_thresholds = ScoreThresholdRequest(
    thresholds={"temp_site1_anomaly_score": 50.0},
    selection_evidence_id="reviewed_threshold_note_001",
)
record = evaluate_chronological_score_thresholds(study, split, fixed_thresholds, context)
verify_chronological_score_thresholds(record, study, context)
```

원본 취득 뒤 저장소 루트에서 다음처럼 실행한다. 임계값은 분석 전에 담당자가
선정하고 그 근거를 기록해야 한다. 아래 숫자는 실행 예시일 뿐 최적값 권고가 아니다.

```powershell
$env:PYTHONPATH = (Resolve-Path src).Path
.\.venv-core\Scripts\python.exe scripts\check_water_pilot_study.py `
  --threshold temp_site1_anomaly_score=50 `
  --threshold sfc_temp_site1_anomaly_score=50 `
  --threshold-evidence-id reviewed_threshold_note_001
Remove-Item Env:PYTHONPATH
```

결과 기본 경로는 `research/water_pilot_study_results.json`이다. 결과에는 행별 값이
없고 출처 해시, 결측 집계, 고정 임계값 및 집계 지표만 들어간다. 파생 점수의
작성 과정·독립성·물리적 의미를 추정하지 않는다.

## 향후 연구 경로

1. 라벨 생성 규칙과 운영 누수 기록 시각을 원 데이터 소유자 자료로 확인한다.
2. 원시 센서/운영변수와 점수 생성 파이프라인의 출처·버전을 별도 근거로 확보한다.
3. 시간 블록 및 ±7일 purge가 적용된 평가 설계를 고정하고 검증용 기간을 분리한다.
4. 구역·자산 단위 외부 평가 자료를 확보한 뒤 전이 성능을 별도 보고한다.
5. 필요하면 위험성/FTA 어댑터에 임계값 경보를 입력하되, 경보 결과를 사건 확률이나
   인적오류 확률로 바꾸려면 별도의 근거와 보정 모델을 요구한다.

회귀 사례는 `tests/test_derived_score_study.py`에 작성되어 있으며, 현재 정책에 따라
실행은 누리온 검증 대기다. 문서의 숫자는 원본 파일의 구조·집계 점검 기록이며
알고리즘 성능 시험 결과가 아니다.
