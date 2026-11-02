# 다단 압축기 맵과 열 예산

`compressor_train.evaluate_compressor_train(request, context)`는 검토된 사용자 맵을
보간하고 기존 LH2DT 압축기의 실제 점 계산을 직렬 연결한다. 지원 유체는
Hydrogen/ParaHydrogen, 물성은 CoolProp HEOS다. 다단 열역학·명시 냉각 경계와
정적 운전영역을 계산하며 실제 서지 진동·로터·재순환/안티서지 제어는 풀지 않는다.
따라서 전체 압축기 맵 기술은 부분 구현으로 유지한다.

## 입력과 교체 방법

`CompressorTrainRequest`에 자산·사건·시계/offset·실제 평가시각, 총상태 입구 P/T,
질량유량, 유체/엔탈피 기준, 단계별 포트/축/속도와 근거를 선언한다. 각
`CompressorStage.performance_map`은 속도 행×유량 열의 압력비와 총상태 간
등엔트로피 효율, 속도별 서지/초크 경계, 같은 유체의 입구 P/T 지원 범위를 갖는다.
축은 정렬된 원 순서를 사용하며 결측 knot·메타데이터를0이나 기본 맵으로 채우지 않는다.

`coordinate_convention=actual`은 실제 rad/s·kg/s다. `reference_corrected`는
명시 참조 절대압력·온도로 θ=T/Tref, δ=P/Pref를 정하고 속도/√θ와 유량√θ/δ를
사용한다. 같은 유체·기하와 해당 보정 정의의 적용성은 검토 근거가 필요하다.
공기 맵을 수소로 바꾸거나 실제 기체 일을 이상기체 상사식으로 대체하지 않는다.
P/T·맵·효율은 총상태 기준이다. 별도 Mach/면적/정압 검토 없이 정압 포트로 내보내지 않는다.

보간은 기존 SciPy `RegularGridInterpolator`의 선형 모드이며 영역 밖 외삽을
거부한다. 정적 서지/초크 유량 여유는 caller가 선언한 최소값과 비교한다.
이 여유는 고장 확률·시간 여유나 실제 서지 발생의 검증값이 아니다.

각 단계는 `none`, `outlet_temperature`, `heat_removal` 중 하나의 독립 냉각 경계를
받는다. 별도 압력 손실·냉각원 온도·최소 접근온도·가용 열 제거 용량을 선언하며
온도 목표와 열 제거량을 동시에 강제하지 않는다. 실제 상태를 P/T 또는 P/H로
해결하고 액체/습증기·순가열·음의 절대압력·용량/접근온도 위반을 거부한다.

원 압축기의 `shaft_power_W`는 이 연결에서 ṁΔh인 기체 엔탈피 증가로 확인한다.
외부 기계 손실과 구동 손실은 별도로 선언한 효율로 계산한다. 같은 손실이 이미
다른 성능 정의에 포함됐는지 검토해야 하며 엔진이 이중 계수를 추정해 보정하지 않는다.
단계/전체 기체·냉각·주위 손실·전기 입력 예산과 압력 장부를 함께 남긴다.

## 근거·기록·다른 모듈 연결

검토5항목, 선택 근거 ID와 `boundary_metadata(request)`를 문맥에 결속한다.
`native_backend_metadata()`는 실제 원 Python tree/라이선스, CoolProp 바이너리/
revision, SciPy 보간 소스, 공통 경계와 환경 버전을 고정한다. 공유
`thermo.coolprop_reference_anchors()`로100kPa/300K의 H/S 기준도 읽어 전역 기준
변경을 검출한다. 이 점은 물성 기준 식별 표식이며 실험 교정점이나 운전 입구가 아니다.

```python
from examples.compressor_train import make_case
from pipeline_integrity.compressor_train import evaluate_compressor_train

request, context = make_case()  # 검토 전에는 선택 물성/원 엔진 import 없이 needs_data
record = evaluate_compressor_train(request, context)
assert record["status"] == "needs_data"
```

검토된 작성 예제는 원 엔진이 설치된 환경에서 `make_case(reviewed=True)`로 실행한다.
`verify_compressor_train_record(..., request=..., context=...)`는 맵·원 압축점·열 예산·
소스/기준을 재실행하며 재해시한 변조를 거부한다. 해시는 서명/작업 승인이 아니다.
`to_signal_frame`은 평가시각과 총상태 채널 이름·단위를 유지한 계산 결과를 내보낸다.
origin은 synthetic 또는 simulation이며 실측으로 표시하지 않는다.

## 재현과 발견한 한계

```powershell
python -m pytest tests/test_compressor_train.py tests/test_property_reference.py -q
python scripts/check_thermal_equipment.py --owner PATH_TO_LH2DT_MODELS --only train
```

실제 두 유체·여러 단계·맵 좌표 보정·별도 affine 면 기준·원 압축점/엔탈피/효율
결속·냉각 온도/열량·영역/시계/근거·변조를 검사한다. 결과와 소스 핀은
[집계](../research/compressor_train_results.json), 전체 입력/결과는 배포 밖
`artifacts/compressor_train`에 남긴다. 작성한 맵은 특정 제품의 성능 자료가 아니다.

동일 냉각 경계를 P/T와 P/H로 정한 경우 첫 냉각기 출구 H는 같았지만 다음 단계
네이티브 P/S 역산의 온도가 약3.4×10⁻⁷K 달랐다. 임의의 더 엄격한 온도 일치를
약속하지 않고 물리적 상태/입력 열량과 기존 에너지 예산으로 동등성을 확인했다.
관측 운전자료를 맵/서지 정답으로 삼지 않는다. 조성·유량·총상태·속도 참조·효율·
동적 제어·회전/열 관성과 독립 시험의 부족은 확장 요청서에 남긴다.

참조 API는 [SciPy 보간](https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.RegularGridInterpolator.html),
[CoolProp 기준 상태](https://coolprop.org/coolprop/HighLevelAPI.html#reference-states)다.
원 소유 모델의 MIT 고지와 버전·체크섬을 유지하며 해당 소스를 이 패키지에 복제하지 않는다.
