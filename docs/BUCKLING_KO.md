# 탄성 프레임의 비례하중 고유좌굴

`buckling.py`는 기존 구조 소유 계산기의 탄성 강성 K와 기준 축력에 따른 기하강성 Kg를 실제 assembly하여 일반화 고유값 문제를 푼다. 선형 정적 처짐을 좌굴 결과로 이름만 바꾸지 않는다. 결과는 완전 형상·선형 탄성 프레임과 공급된 보존적 비례하중 패턴의 분기 하중계수이다. `structural.buckling` 전체 범위에서는 부분 구현이다. 쉘 외압 붕괴, 관 단면 타원화, 초기 결함, 소성, 국부 좌굴과 붕괴 후 거동은 해결하지 않는다.

## 실제 소유 소스 조사와 재사용

현재 설치된 PyNiteFEA 3.2.0 소스에는 linear/P-Delta/modal/pushover 진입점이 있으나 고유좌굴 전용 진입점은 확인되지 않았다. modal은 K/M 진동 문제여서 좌굴로 전용하지 않았다. `FEModel3D.Ke`와 `FEModel3D.Kg`는 존재한다. 기존 `structural.solve_frame`으로 독립 힘/모멘트 및 절점 수지가 검토된 기준 상태를 먼저 계산한다. 그다음 native 모델을 동일 요청으로 구성하여 해당 소유자의 Ke/Kg를 반환받는다. 이 어댑터 안에 beam 요소 강성 행렬식이나 FEM assembly 알고리즘을 작성하지 않았다.

기하강성 부호는 단일 docstring에서 추정하지 않았다. 실제 `FEModel3D.Kg`는 E·A/L과 축 연장량으로 인장 양의 축력을 만들고, PyNite `Analysis.py`는 Ke+Kg를 사용한다. 이 경로를 따라 압축 기준하중은 연화하는 Kg를 만든다. `Member3D.kg`의 compression-positive 설명과 실제 호출 경로의 부호가 달라 후자의 실행 소스를 근거로 고정했다. `Kg(first_step=True)`는 축력을 0으로 두므로 사용하지 않고 반드시 `first_step=False`로 호출한다. 기준 축력은 기존 프레임 결과의 인장 양의 실제 축력과 표본별 대조한다.

확인한 matintegrity 소스의 `structural.external_pressure`는 planned 상태이며, 쉘 붕괴/타원화 원 solver를 발견하지 못했다. 이를 완료된 재료/붕괴 엔진으로 간주하지 않는다.

## 요청 API와 지원 경계

`BucklingRequest`는 기존 `FrameRequest`, 기준 조합 ID, 공급 하중 정의, 좌표계/포트/시계와 수치 gauge를 받는다. 부재 물성·단면·형상과 nodal reference loads는 기존 프레임 계약을 사용한다. 하나의 선택 조합에 담긴 모든 하중을 동일 무차원 λ로 비례 확대한다. λ는 특정 N 또는 Pa 값 자체가 아니므로 단일 기준 힘으로 환산하려면 그 하중 패턴의 정의를 함께 읽어야 한다.

현재 native provider는 rigid-joint 부재, nodal 하중 및 homogeneous supports에 한정한다. 분포 하중, 부재 회전 release, initial strain, 0이 아닌 부과 지지변위는 요청 단계에서 거부한다. 양방향 지지 spring은 native Ke에 포함한다. follower pressure, 접촉 분리, 지반 항복, 비보존력 및 하중 경로가 변하는 문제는 이 API로 승인하지 않는다. 기존 프레임의 작은 변위·회전/축변형 적용성 검토도 유지한다.

`conservative_proportional_reference_load_reviewed`, `perfect_linear_elastic_frame_model_reviewed`, `stiffness_provider_and_dof_basis_reviewed`는 기본 거짓이다. 예제 `make_case()`도 기본 상태에서 native 호출 전 `needs_data`이다. 기준 nodal load나 기존 프레임 검토가 미상이면 기존 소유 상태를 유지한다. 숫자·단위·근거를 채웠다는 사실 자체가 물리적 적용성 검증은 아니다.

## 실제 행렬 provider 계약

기본 `PyNiteBucklingProvider`는 공개 `structural.build_native_frame`와 `assemble_frame_stiffness`를 사용한다. native 모델 구성을 이 파일에서 중복 수행하지 않는다. 소유 공개 행렬 API가 기준 프레임 변위를 다시 계산하고 공유 프레임 영수증과 translation m/rotation rad를 각각 대조한다. 각 명시 부재의 segmentation과 E·A/L 축 연장력도 확인한 후 native Ke/Kg를 반환한다. `BucklingMatrices`는 전체 DOF 순서, 고정 DOF, 행렬, 기준 프레임/기준 결과 digest, 실제 provider 소스 지문, versioned `public_structural_stiffness_receipt` 및 prestress 확인을 보존한다.

다른 원 엔진은 `BucklingMatrixProvider.assemble(request, context, reference_record)`를 구현할 수 있다. 외부 provider ID, 실제 callback 및 명시 `expected_provider_sha256`가 필요하다. callback 클래스의 실제 소스 파일 SHA를 공급 receipt와 대조하므로 외부 callback이 기본 native provider라고 가장할 수 없다. 외부 엔진의 부호·단위·재료/기하 강성 구성과 reference-state 검토 책임은 해당 provider에 있다. 출처 없는 임의 행렬을 자동으로 검증된 구조물로 바꾸지 않는다. 재생 시에도 실제 외부 callback을 다시 공급해야 한다.

## 지지·gauge·행렬 감사

DOF 순서는 요청 절점 순서의 DX/DY/DZ/RX/RY/RZ이다. 변위는 m, 회전은 rad, 해당 generalized force는 N/N·m이다. 고정 좌표를 제거하되 숨은 구속을 더하거나 작은 대각 강성으로 regularization하지 않는다. 탄성 mechanism이나 gauge 해상도 부족은 결과를 보류한다.

`translation_gauge_m`, `rotation_gauge_rad`는 서로 단위가 다른 좌표를 명시적으로 정규화하는 양의 크기이다. 대각 S에 대해 A=SᵀKffS, B=−SᵀKgffS로 congruence 변환한다. 같은 S를 양쪽에 적용하므로 이론적 하중계수는 gauge에 따라 변하지 않는다. 그러나 조건수가 달라질 수 있어 gauge 변화 검토는 후속 수치 시험에 포함한다. mode의 최대 gauge 좌표를 양의 1로 정규화하며 실제 변형 진폭으로 해석하지 않는다.

K/Kg의 유한성·크기·대칭성과 DOF/support 일치를 검사한다. gauge K의 고유값으로 양정성과 최소/최대 강성 비율을 검사한다. 이때 `eigvalsh`를 K에만 사용한다. B=−Kg는 singular 또는 indefinite일 수 있으므로 B의 양정성을 요구하는 generalized `eigh`를 사용하지 않는다. 허용오차와 강성 해상도 기준은 요청의 수치 정책이며 설계 규격 계수가 아니다.

## 고유값·힘/모멘트 잔차

[SciPy 공식 eig API](https://docs.scipy.org/doc/scipy/reference/generated/scipy.linalg.eig.html)의 일반화 문제를 사용하여 Kφ=λ(−Kg)φ를 푼다. `homogeneous_eigvals=True`로 α/β를 받고 β=0인 무한 고유값, 유한 비양수 값, 유한 양수 값을 구분한다. α=β=0인 singular pencil, 표현 불가능한 비율 및 유의한 복소수는 `out_of_scope`이다. 미상이나 실패한 고유쌍을 조용히 삭제하여 낮은 모드를 놓치지 않는다.

양의 각 고유쌍은 gauge 공간의 상대 잔차와 원래 free DOF의 `(Kff+λKgff)u` 잔차를 모두 검토한다. 원 DOF 행별 잔차는 translation에서 N, rotation에서 N·m이며 기존 프레임의 절대 힘/모멘트 허용오차와 선언한 상대 잔차 오차를 적용한다. 고정 DOF의 perturbation 반력은 0일 필요가 없어 free 평형과 구분한다. 기준 상태의 전역 힘/모멘트 및 절점 수지는 기존 프레임 영수증에 남긴다.

전체 양의 모드 중 가장 작은 λ를 보고하고 요청 개수만큼 mode를 반환한다. 양의 모드가 없으면 `no_positive_eigenvalue_in_supplied_model`이며 critical factor는 null이다. 이를 무한 좌굴 강도, 안정 보장 또는 인장 파괴 안전으로 해석하지 않는다. `computed_diagnostic`도 mesh 수렴, 결함 민감도, 규격 허용치 및 현장 검증을 뜻하지 않는다.

## 재현·현재 검증 상태

요청/문맥, 기준 구조 영수증, 실제 전체 K/Kg 및 digest, native provider 소스/패키지 지문, gauge/support 감사, 고유벡터/잔차, SciPy solver 소스와 런타임 버전을 보존한다. `verify_buckling_record(record, request=..., context=..., provider=...)`는 실제 provider를 재호출하여 행렬을 재구성하고 고유문제를 다시 푼다. 반복/중복 고유값의 mode basis는 라이브러리/BLAS 환경에 따라 달라질 수 있어 엄격한 재생이 실패할 수 있다. 무서명 해시와 현재 재계산은 과거 실행 신원이나 과거 실행 사실을 인증하지 않는다.

코드·예제·시험 정의를 작성했고 Ruff 및 Python 컴파일을 통과했다. 사용자 지시에 따라 pytest, PyNite 기준 상태, 행렬 assembly, SciPy 고유값 계산 및 예제 수치 실행을 하지 않았다. native provider 실제 재현, Euler 기준 해 재현 및 mesh/gauge 수렴은 모두 검증 대기이다. 후속 누리온 환경에서 `python -m pytest tests/test_buckling.py -q`를 실행한다. 시험에는 pinned column의 독립 Euler 해, mesh refinement, 기준 하중 배율/gauge 불변성, 인장 기준 패턴, source/DOF/support/asymmetry/zero-K 거부 및 재봉인 변조가 포함된다.

## 소유 확장 요청: PI-X05/02

구조 소유 모듈의 공통 native model factory와 K/Kg/reference-state 공개 assembly receipt는 현재 연결했다. 독립 native 재현과 수렴 검증은 대기 중이다. 분포 축력·회전 release·초기 변형 및 비선형 접촉이 포함된 기하강성의 적용성/수렴 검토는 여전히 소유자가 확장해야 한다. 재료/쉘 소유 모듈에는 결함 형상·타원화·두께 변화·탄소성·잔류응력·외압/내압과 경계조건을 포함하는 실제 shell collapse solver 및 독립 검증을 요청한다. provider 확보 전에는 현재 완전 탄성 frame eigenproblem을 관 붕괴/외압 허용압력 계산으로 재명명하지 않는다.

native API 구성은 [PyNite FEModel3D 공식 문서](https://pynite.readthedocs.io/en/latest/FEModel3D.html)를 참조하되, 이 구현의 지원 버전과 행렬 부호는 실제 설치 소스 지문을 기준으로 한다.
