# 부식 조건 배관 타격 압력 신호

## 자료와 권리

[Mendeley Data: Pipe percussion dataset](https://data.mendeley.com/datasets/csyshmrpvg/1)은 CC BY 4.0으로 공개된 실험 자료다. publisher metadata에서 고정된 원본은 5,426,985,868 bytes, SHA-256 `f22a5d01347a2dce8830763954a6cb99ba3914ec97070277096370f52e0529c6`이다. 사용자가 제공한 로컬 파일을 이 크기와 SHA-256으로 확인하고 `data/raw/percussion/`에 복사했다. 원본 archive는 소스 코드 배포와 Nurion source bundle에서 제외한다.

Zenodo/Mendeley 설명은 동일 규격 두 시험관, 7개 전기화학 부식 조건, 각 조건에서 다섯 위치를 타격한 반복 신호라고 기술한다. 원 기록은 횟수를 조건/위치마다 110으로 설명한다. 이 metadata는 실제 CSV 내용의 계측 타당성이나 수명/부식량의 calendar relationship을 증명하지 않는다.

## archive inventory

RAR를 추출하지 않고 Windows `tar -tvf`로 목록을 읽었다. 3,892개 member(파일 3,849개, directory 43개)가 있으며 CSV가 3,849개, 비압축 파일 크기는 19,935,877,131 bytes다. 예상 7×5×110 조합 3,850개 가운데 `B/1/017.csv`가 없어 실제 파일 수와 게시 설명의 반복 횟수가 한 건 차이 난다. 이 tar 목록은 member encryption metadata를 노출하지 않으므로 암호화 여부는 미확인이다. 누락 member 전체 fingerprint와 집계는 `research/percussion_archive_inventory.json`, 파일별 목록은 로컬 취득 영수증 `artifacts/acquisition/percussion.json`을 참조한다.

`A/1/001.csv`의 첫 줄은 `时间 - cDAQ9185-1D34FAFMod1/ai0`, `Pascals - cDAQ9185-1D34FAFMod1/ai0` 두 열을 선언한다. reader는 `时间`을 초(s), `Pascals`를 Pa로 해석하며, 이는 파일명/값 및 장비 열 이름을 기반으로 한 데이터 매핑이다. 처음 표본의 간격은 약 19.5 μs다. 헤더 확인은 각 표본의 교정, 채널 identity의 외부 확인, clock synchronization을 입증하지 않는다. 전체 CSV 분석이나 모든 신호의 재생은 아직 수행하지 않았다.

## Python API

```python
from pipeline_integrity.percussion_data import (
    read_percussion_recording,
    summarize_percussion_recording,
    percussion_recording_to_sampled_trace,
)

record = read_percussion_recording(
    "data/raw/percussion/Pipe percussion dataset.rar",
    "Pipe percussion dataset/A/1/001.csv",
)
summary = summarize_percussion_recording(record)

# 기존 음향 모듈에 연결하려면 시험관/센서/clock ID를 연구자가 명시합니다.
trace = percussion_recording_to_sampled_trace(
    record, asset_id="pipe-A", sensor_id="mic-1", clock_id="experiment-clock",
    maximum_relative_jitter=0.05,
)
```

`read_percussion_recording`은 호출마다 supplier archive size/SHA-256을 재확인하고 `tar` 또는 `PIPELINE_RAR_LIST_TOOL`로 member 목록을 확인한 뒤 선택한 파일만 stdout stream으로 읽는다. 대량 순차 작업에서는 `PercussionArchiveReader`를 한 번 생성해 같은 archive의 검증된 목록을 재사용하고 `reader.read(member_path)`로 각 파일을 읽을 수 있다. 이 reader는 생성 시 전체 SHA-256과 목록을 확인하고 매 읽기 전후 파일 크기·수정시각·파일 식별자를 확인하므로, 사용 중 archive가 변하지 않도록 유지해야 한다. 중간 압축 해제 파일을 만들지 않고 archive/member/sample 상한, 안전한 canonical path, UTF-8 CSV header, 숫자·유한값·단조 증가 시계를 검사한다. 허용 가능한 path는 `A-G`, tap position `1-5`, stroke `001-110`이며 condition D는 원 파일명의 `a-e` prefix도 검증한다.

측정 열은 **압력 Pa, 시각 s**로 고정하지만 센서 ID, clock ID와 시험관 ID는 자동으로 알아낼 수 없다. acoustic API bridge는 이 식별자를 caller가 넣고 시간 간격이 명시한 허용오차 내에 있을 때 중앙 시간간격 기반 `acoustics.SampledTrace`를 만든다. 관측 timestamp는 소수점 반올림으로 간격 편차가 최대 약 5%다. 기본 1% 허용오차에서는 이 파일을 거부하므로, acoustic 계산에 넣으려면 자료 해상도와 목표 계산의 시간오차를 검토한 뒤 최대 5%를 명시해야 한다. 생성 trace의 source digest는 중앙 간격 가정 및 관측/허용 지터를 봉인한다. 이 절차는 원 신호를 재표본화하지 않지만 불균일한 timestamp를 균일 timebase로 해석하는 caller 가정을 둔다. 서로 다른 기록을 동기화하지 않는다.

## 분석 및 한계

가장 자연스러운 재사용 지점은 기존 acoustic waveform delay/correlation API와 신호 특징 추출이다. 다만 타격 위치는 다섯 곳이고 단일 tap response만으로 pipe damage location이나 corrosion severity를 정답으로 분류하지 않는다. 파일 path에 포함된 A-G 부호는 source condition indexing이지 독립적인 corrosion depth/잔여두께 측정값이 아니다. 두 관, 반복 stroke, 위치를 독립 샘플로 무작위 분할하면 같은 관·조건·취득 반복이 train/evaluation에 누출된다. 평가 split은 pipe 또는 condition/tap/stroke 종속 구조를 검토한 뒤 caller가 선언해야 한다.

새 reader는 시계열 배열을 메모리 객체로 반환하므로 그 객체나 raw traces를 JSON/공개 receipt로 덤프하면 안 된다. `summarize_percussion_recording`은 선택한 한 기록의 수량 요약만 만든다. 공개 산출물에는 집계, 근거, source hashes와 실행 정의만 포함하고 원 신호/파형을 넣지 않는다. 실험 모형, 센서 교정, 부식 상태 독립확인, 재현/외부 검증과 pipeline field transfer는 미완료다.

회귀 모듈 `tests/test_percussion_data.py`는 synthetic path/clock/aggregate 경계를 다루며 Nurion 실행 대기 상태다. 이번 session에서 CSV 전체 해석, damage model fitting, regression suite 또는 PBS job은 실행하지 않았다.
