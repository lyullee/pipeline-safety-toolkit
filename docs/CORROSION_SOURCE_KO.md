# 작은 부식·마모 공개 관측 자료의 적용 범위

서로 다른 두 원실험 자료를 공식 저장소에서 직접 취득했다. CSV 두 개의 합계는
2,645,039바이트다. 원본 크기와 공급자 MD5를 확인하고 로컬 SHA-256을 별도로
남겼다. 이 자료가 보여 주는 관측량을 읽는 것과 물리 모델의 입력 조건을
확정하는 것은 다른 작업이다. 이번 적용은 관측 보존과 미상 조건에 대한 계산
보류를 확인하며 현장 부식률·수명·검사 탐지 성능을 자격화하지 않는다.

| 자료 | 공식 출처와 버전 | 이용 조건 | 실제 구성 |
|---|---|---|---|
| Kbely 구리 센서 | [Zenodo 10640939](https://zenodo.org/records/10640939), 0.2 | CC BY 4.0 | 2,545,534바이트 CSV, 9,387관측, 28열 |
| AISI 52100 AFM 마모 | [Zenodo의 Dryad 기록 4743140](https://zenodo.org/records/4743140), 2021-05-17 자료 | CC0 1.0 | 99,505바이트 CSV, 1,346행, 10열, 9개 그림 구획 |

첫 자료의 저자는 Michal Kuchař와 Jaromír Fišer이며 DOI는
`10.5281/zenodo.10640939`다. 두 번째는 Jack Walker, Jamal Umer, Mahdi
Mohammadpour, Stephanos Theodossiades, Stephen Richard Bewsher, Guenter Offner,
Hemant Bansal, Michael Leighton, Michael Braunstingl, Heinz-Georg Flesch가
제공했으며 원 자료 DOI는 `10.5061/dryad.s7h44j16j`다. 후자는 다른 문헌의 수치를
모은 일반 재료 표가 아니라 같은 연구진의 AFM·압흔 실험 그림 자료다.

## 구리 센서에서 확인한 것과 남긴 미상

CSV의 시각은 `2022-04-13 10:43:09`부터 `2023-05-09 11:36:29`까지 증가한다.
시간대 표시는 없으며 인접 간격 중 네 개는 3,600초가 아니다. 그 차이를
한 시간으로 교정하지 않는다. `corrosion_diff`는 앞행의 `corrosion` 차이와
부동소수점 잔차 범위에서 일치한다. 두 열의 부호와 원 문자열, 환경 열을
포함한 모든 원 셀을 유지한다. 원 수치의 누적 범위는 0에서 0.0271이며
음의 누적값·음의 공급자 차분은 이 파일에서 관측되지 않았다.

CSV 헤더에는 깊이 단위가 없다. 연관된
[Kuchař 등 연구](https://doi.org/10.1186/s40494-024-01212-6)는 센싱 구리 표면의
부식 깊이와 주변 환경을 설명하고 그림의 깊이 축을 nm로 표시한다. 그러나
그림의 약 27.1이라는 범위와 CSV의 0.0271이라는 범위 사이의 변환 배율을
CSV 자체나 자료 설명이 명시하지 않는다. 비슷한 곡선 모양과 그럴듯한 배율만으로
CSV 열을 자동으로 µm 또는 nm로 선언하지 않았다. `corrosion_m`과 SI 변환 배율은
따라서 `null`이다. 첫 관측값이 0이라는 사실도 센서 설치 전의 물리적 노출 나이,
영점 조정 이력, 누적 금속 손실의 기준을 확정하지 않는다.

`elapsed_from_first_observation_s`는 시간 문자열 차이를 기록한 관측 좌표다.
이를 재료의 `physical_exposure_age_s`로 복사하지 않는다. 원 power-law는
노출 나이에 대해 `coefficient_m * (exposure_s / reference_time_s)^exponent`를
계산하므로 단위·노출 원점·모수 지원 범위가 미상인 현재 자료를 물리적으로
적합하지 않았다. 임의의 첫 관측 차감과 양수 로그 변환을 수행해 유효한 부식
성장률을 만들어 내지 않는다. 향후 관측 시작 시점의 나이를 따로 검토하면
증분 예측은 전체 나이의 두 예측값 차이로 정의해야 한다.

한 센서의 시간별 반복은 독립 시편 9,387개가 아니다. 이 자료에는 같은 노출에서
같은 면적의 독립 블록별 최대 pit 깊이가 없다. 따라서 구리 열을 Gumbel 면적
최대값의 표본으로 바꾸거나 자연 환경의 강관으로 전이하지 않는다. 이 제한은
자료의 구리 센서 역할을 역사적 알루미늄 구조물의 실측 깊이로 바꾸는 것도 막는다.

## AFM 원단위와 조건부 거리 해석

AFM CSV는 균일한 한 표가 아니다. 그림 4A의 다섯 하중·변위 곡선, 그림 4B의
다섯 경도 요약, 그림 6A의 여섯 pass 수·평균 부피, 그림 7·8의 마찰 이력,
그림 10의 열두 하중·부피 기록, 그림 12의 마모 정도, 그림 13A·13B의 마찰
자료가 구획별로 이어진다. 빈 배치 행과 반복 하중도 원 위치에 남긴다.
그림 4A의 음의 초기 압입 변위도 잘라 내거나 원점을 재조정하지 않는다.

원 열이 선언한 µN에는 `1e-6`을 곱해 N으로, µm³에는 `1e-18`을 곱해 m³로
변환한다. 경도 구획의 하중은 mN이므로 `1e-3`이고 경도는 GPa이므로 `1e9`다.
압흔 하중과 scratch 하중의 세 자릿수 차이를 같은 조건으로 합치지 않는다.
경도 표는 scratch의 각 µN 접촉에 대응하는 경도나 scratch 식별자를 제공하지
않으므로 보간이나 기본 경도로 그 연결을 채우지 않는다.

[Walker 등 방법](https://pmc.ncbi.nlm.nih.gov/articles/PMC8336757/)의 방향성 pass
정의와 scratch 길이를 검토하면 한 pass는 5 µm 이동이다. 그 방법 근거의 해시를
명시해 reader에 전달할 때만 그림 6A의 pass 수에 그 길이를 곱한다. 그림 10은
2,000 pass, 총 10,000 µm이므로 검토된 이동거리는 0.01 m다. 방법 근거를 주지
않은 reader 호출은 이동거리를 `null`로 유지한다. 단순히 마찰 이력의 행 수를
이동거리나 물리적 시간으로 선언하지 않는다.

그림 10의 열 이름을 그대로 존중해 보고된 `Removed Volume`과 `Pile Up Volume`을
별도로 변환한다. 전자에서 후자를 다시 빼서 실제 탈락 부피로 재정의하지
않는다. 검토된 거리와 양의 하중이 있는 열두 기록에는 보고된 removed volume을
하중과 거리의 곱으로 나눈 설명량을 계산할 수 있다. 이 양의 단위는
`m³/(N·m)`이고 이번 범위는 약 `4.63e-15`에서 `4.37e-13`이다. 이는 무차원
Archard 계수가 아니다. 동일 연구의 경도 값을 임의로 곱해 무차원 계수로
전환하지 않았다.

원 Archard 모델은 무차원 계수, 하중, 이동거리, 경도, 분포 면적을 필요로 한다.
현재의 source 조건에서는 대응 경도, 계수의 보정 범위와 분포 면적이 미상이다.
따라서 열두 원 기록의 native 계산은 `needs_data`로 남는다. topography 촬영
면적을 실제 마모 분포 면적이라고 가정하지 않는다. 그림의 평균·오차나
같은 하중의 두 행을 개별 독립 시편이라고 간주하지 않는다. 나노 접촉에서
배관 부품의 거시 접촉으로 전이하는 근거도 현재 없다.

## 실행과 기록

```powershell
python scripts/acquire_data.py --only copper_wire_corrosion afm_steel_wear --workers 2 --maximum-seconds 90
python scripts/review_corrosion_source.py --materials PATH_TO_REVIEWED_MAT_SNAPSHOT
python -m pytest -q tests/test_corrosion_data.py
python -m ruff check src/pipeline_integrity/corrosion_data.py scripts/review_corrosion_source.py tests/test_corrosion_data.py
python -m ruff format --check src/pipeline_integrity/corrosion_data.py scripts/review_corrosion_source.py tests/test_corrosion_data.py
```

공통 취득 원장은 `artifacts/acquisition/copper_wire_corrosion.json`과
`artifacts/acquisition/afm_steel_wear.json`이다. 원본은 각 `data/raw` 하위에 있으며
코드 배포물에 포함하지 않는다. 공급자 메타데이터·권리·연관 방법 캐시는
`artifacts/source_metadata`에 남는다. 원관측 전체, 변환행, 상세 계산 요청과
결과는 `artifacts/corrosion_source`에만 기록한다. 공개
`research/corrosion_source_results.json`에는 출처·집계·해시·보류 조건만 기록한다.
원모델 경계에는 구리 두 요청과 AFM 열두 요청을 전달하며 모두 조건 부족으로
차단된다. 이 열네 보류 기록에서 원 소유 엔진의 수치 모델 호출은 0회다.

검사는 공급자 크기·체크섬 확인이 CSV 판독에 앞서는지, 같은 크기의 손상 파일을
거부하는지, 순서가 뒤집히거나 시각이 겹쳐도 보정하지 않는지, 비유한값·결측과
부호를 보존하는지, 부피의 세제곱 단위 계수와 pass 거리의 근거 요구가 맞는지
확인한다. 이 검사는 원실험의 정확도나 센서의 교정 성능 검증과 다르다.
실행 전후 원 소유 엔진의 Python 소스 집계 해시가 같아야 한다.

## 보류를 해제하는 후속 계약

구리 자료에는 정확한 CSV 단위와 배율의 공급자 근거, 영점·설치·노출 시작 이력,
센서 교정과 손실량의 정의가 필요하다. 물리 모수 적합을 수행한다면 같은 센서의
과거 구간만으로 보정하고 이후 구간의 입력을 별도로 보존해야 한다. 그런 시간
구간 비교도 다른 센서나 다른 현장의 독립 검증으로 부르지 않는다.

AFM 자료에는 각 scratch의 물리 식별, 보고 부피의 측정 정의, 대응하는 접촉
경도·tip 조건, 무차원 계수의 보정 근거와 분포 면적이 필요하다. pit 면적 전이에는
동일 노출·동일 표면 모집단의 같은 면적 독립 블록 최대값과 검열 정보가 필요하다.
POD에는 알려진 flaw 크기·유무, blank, inspector·trial·절차와 독립 실물 정답이
필요하다. 두 파일은 이 조건들을 제공하지 않으므로 검사 POD나 기간 실패확률을
생성하지 않는다.
