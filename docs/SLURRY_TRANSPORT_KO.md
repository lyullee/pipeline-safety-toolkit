# 균질 슬러리 수송의 제한적 유변학·관손실 API

상태: 코드와 시험 시나리오 작성 완료. 수치 검증은 누리온 실행 대기이며 로컬 pytest·수치 smoke·벤치마크는 실행하지 않았다. 정적 검사 결과를 물리 검증 합격으로 해석하지 않는다.

## 모델 선택과 재사용

설치된 Fluids 및 조사한 기존 hydraulic/equipment 소유 소스에는 Bingham/Herschel–Bulkley/power-law 원관 수송 엔진을 찾지 못했다. Newtonian 등가 모드는 기존 `models.hydraulics.liquid_pressure_loss`를 그대로 호출한다. HB 계열에는 정상·완전발달·층류·no-slip·이상적 원관의 응력/유량 관계만 최소 closure로 제공한다. 적분과 역산은 SciPy QUADPACK `quad`, `brentq`에 맡기며 별도의 수치 적분기·근 찾기 엔진을 만들지 않았다.

지원 모델은 Newtonian, Bingham(n=1), power-law(항복응력=0), Herschel–Bulkley다. 일관성 계수 K의 단위는 Pa·sⁿ이고 n이 1일 때만 일반 동점도가 아닌 **동적 점도 Pa·s**와 같은 차원이 된다. 항복응력은 Pa, 전단율은 1/s다. 항복 이하 영역의 전단율은 0이며 플러그를 허용한다. 항복 부근의 점도를 유한하게 만드는 임의 regularization은 넣지 않았다.

## 최소 closure 유도

정상 원관 축방향 힘 평형에서 반경 r의 전단응력은 벽면 응력 τw와 r/R의 곱이다. HB 구성식의 yielded 영역 전단율은 ((τ−τy)/K)^(1/n)이다. no-slip 속도 분포를 단면 적분하고 적분 순서를 바꾸면 유량은 다음과 같이 정리된다.

`|Q| = π R³ ∫[τy/τw, 1] ((τw s − τy)/K)^(1/n) s² ds`

τw≤τy이면 Q=0이다. 비영 유량에서는 위 적분과 지정 |Q|의 차이를 SciPy로 역산하고 마찰 압력강하 크기를 `4 L τw / D`로 구한다. 유량의 부호로 마찰 압력강하 방향을 정한다. 이는 난류 마찰계수식이나 침적속도식이 아니다. [공개 원논문의 층류 Newtonian/power-law/Bingham/HB 처리](https://www.cambridge.org/core/journals/journal-of-fluid-mechanics/article/optimising-branched-fluidic-networks-a-unifying-approach-including-laminar-and-turbulent-flows-rough-walls-and-nonnewtonian-fluids/02A336A447A689A1D9F288B0363071D2)를 참고할 수 있다. 설명·코드는 새로 작성했으며 원문의 표·문장을 복제하지 않았다.

Newtonian 모드에서는 이 closure를 중복 실행하지 않고 기존 hydraulic owner를 사용한다. 그 결과의 fully-developed 벽면응력은 공급된 국부손실과 구분한다. 비Newtonian 모드에 Newtonian의 경험 국부손실 계수나 거칠기 난류식을 적용하지 않으며, 현재 roughness=0 및 local K=0만 지원한다.

## 공급자 데이터와 근거

`SlurryPropertySnapshot`은 provider_id/version, source_sha256, 물성 reference_basis, 구성식 계수, 혼합 밀도, 온도, 고체 체적분율, 입경, 액체/입자 밀도와 각각의 적용 범위를 명시한다. 실험 유변학 분석기나 다른 프로젝트 모듈이 이 snapshot을 생성하면 된다. 온도·농도에 따른 K/n/항복응력의 임의 보간식은 제공하지 않는다. source_sha256는 실제 선택한 property EvidenceRef.sha256와 일치해야 하며, 원 데이터/공급자 버전과 공개 조건을 그 근거에 보존한다.

공급자는 다음 조건을 별도 근거로 검토해야 한다.

- `homogeneous_condition='supported'`: 단면·길이 방향의 균질 연속체 근사가 유효함.
- `settling_condition='supported_homogeneous_operating_window'`: 지정 운전 조건에서 침강·분리 없이 해당 근사가 지지됨.
- `supported_absolute_velocity_m_s`: 그 판단과 유동 영역에 적용할 실제 속도 범위. 이 하한을 프로그램이 계산한 임계 침적속도로 부르지 않는다.
- 고체 농도·온도·최대 전단율·최대 전단응력·입자/관경 비의 공급자 지원 범위.
- 비Newtonian에는 `flow_regime='laminar_supplied'`, Newtonian 등가에는 `flow_regime='newtonian_owner'`.

이 판단은 모델이 자동 확인한 실험 결과가 아니다. 미상·실패 상태를 supported로 변환하지 않는다. 임의의 일반화 Reynolds 임계값이나 입경 비 한계값은 내장하지 않는다. 입도분포, 입자 형상, 응집·thixotropy·벽면 slip·shear banding이 관련되면 공급자 근거와 모델의 유효성이 별도로 필요하다.

고체 농도가 0보다 크면 입경, 검토된 입자/관경 비, 액체 밀도, 입자 밀도가 필요하다. 체적분율 혼합 밀도 `(1-c)ρcarrier+cρsolid`와 공급한 혼합 밀도를 지정 허용오차로 교차 확인한다. 이는 부피 가산의 검토된 snapshot 장부이며, 농도 변화나 slip mass balance를 푼 결과는 아니다.

## 요청·실행·재실행

`SlurryTransportRequest`에 유량 m³/s(부호 포함), 관경/길이 m, 두 포트, 절대 입구압 Pa, 출구-입구 높이차 m, 중력 및 수치 예산을 지정한다. 시간은 주어진 clock의 snapshot 시각이다. 유량은 inlet→outlet 방향 양수이며 역방향도 계산할 수 있다. 역방향일 때도 높이차와 두 포트 압력 기준을 바꾸지 않는다.

1. 실제 context EvidenceRef에 geometry/property/review 근거를 넣고 네 `REVIEWS`를 실제 검토 결과로 채운다.
2. 검토 시점의 `native_backend_metadata()`를 owner_pins에 저장한다. SciPy/Fluids 버전·라이선스, quad/Brent Python 소스, QUADPACK/root 바이너리, 기존 hydraulic owner와 adapter 해시를 포함한다.
3. 최종 `boundary_metadata(request)`를 context.metadata['slurry_transport_boundary']에 넣는다. 자산·이벤트·시계·시각·압력 포트 및 provider snapshot 전체를 바인딩한다.
4. `evaluate_slurry_transport`를 호출한다. 누락·미검토는 needs_data로 반환하고 native 엔진을 호출하지 않는다. context/source pin 불일치는 오류다.
5. `verify_slurry_transport_record`는 digest 검사 이후 실제 closure와 numerical owner로 재실행한다. 결과·근거·자격 판단까지 같아야 valid다.

예제는 독립 작성한 가상 Bingham snapshot이다. 예제의 supported 플래그와 범위는 시험 가정으로 현장 측정이 아니다. 기본 실행은 미검토 상태를 보여 준다. `reviewed=True`는 시험용 객체 작성 편의이며 실제 검토 근거를 생성하지 않는다.

## 수치 범위와 결과

근 찾기의 상한은 사용자 stress bracket, 공급자 최대 전단응력, 공급자 최대 전단율을 구성식으로 바꾼 응력 중 가장 작은 값이다. 따라서 trial 상태도 선언된 전단 영역을 넘기지 않는다. 상한에서 목표 유량을 만들 수 없으면 범위 밖으로 반환하며 bracket을 임의 확장하지 않는다. QUADPACK 경고·비유한 값·근 찾기 실패는 out_of_scope이고, 작은 유량이 수치 오차 때문에 항복 이하로 역산되면 성공으로 받아들이지 않는다.

최종 유량 잔차, QUADPACK 오차 추정, 사용자가 지정한 절대/상대 유량 예산, root 반복 수를 기록한다. 수치 오차 추정은 엄밀한 오차 증명이나 독립 실험 검증이 아니다. 전단율은 구성식에 따라 벽에서 최대이므로 벽 상태의 지원 범위를 확인한다. Newtonian 경로는 기존 owner의 전이영역 보류 정책을 그대로 유지한다.

Q=0일 때 항복 유체의 마찰 압력강하는 유일하지 않다. 항복 이하의 양·음 압력 구간을 별도로 반환하고, 기본 출구압 계산에는 마찰강하 0인 최소 가지를 선택했음을 `minimal_zero_flow_pressure_branch`에 표시한다. 이 값을 실제 정지 관로의 유일 압력으로 해석하지 않는다. 정지 snapshot도 공급자가 허용한 균질/침강 속도 범위 안이어야 한다.

## 압력·기계 일·질량 장부

높이차의 정수압 항과 부호 있는 마찰 압력강하를 분리한다. 압력 일 rate는 `Q(Pin−Pout)`, 위치에너지 rate는 `QρgΔz`, 소산 rate는 `|Q| |ΔPf|`다. 세 항의 잔차를 절대/상대 W 예산으로 검사한다. 역방향에서는 중력 및 압력 일의 부호가 달라질 수 있으나 소산은 음수가 되지 않는다.

혼합 질량유량 및 액체/고체 체적유량을 반환한다. 농도·밀도는 외부 snapshot으로 고정되며 상간 slip이나 입자 이동 방정식을 푼 것은 아니다. 기계 소산의 열전달 처리, 실제 온도 변화, 침적속도, 마모율은 계산하지 않으므로 None이다. 물성 온도를 유지하기 위한 열경계는 이 모듈 밖에서 다뤄야 한다. snapshot power를 운영 시간 없이 누적 에너지로 변환하지 않는다.

## 누리온 검증과 미지원 확장

시험에는 Buckingham–Reiner Bingham 해, power-law 해, Newtonian Hagen–Poiseuille 한계, 역방향·중력의 기계 일, 정지 항복압 구간, 입자/밀도/전단 범위 거부, unknown 침강조건 거부, roughness/local K 거부, context·비유한 복사 입력·source pin·기록 변조가 포함된다. 작성만 했고 아직 실행하지 않았다. 누리온 실행 시 통과·실패와 실제 환경/source pin을 보존해야 한다.

후속 qualification에는 항복 부근의 유량 해상도·적분 오차 수렴, n과 K의 극단 영역, 응력 bracket 끝점, 독립 rheometry와 관손실 측정, 농도·입도별 균질성 관측이 필요하다. 현재 결과를 슬러리 전반의 완성 기능이나 독립 검증 모델로 표시하지 않는다.

기존 receiving owner에는 이질 슬러리의 농도·상간 slip/질량·운동량 장부, 침적/재부유 사건, 검증된 침적속도, 비Newtonian 난류/거칠기/국부손실, 입자 마모, 시간 의존 유변학 및 열/점도 결합이 필요하다. 모델별 출처·라이선스·지원 domain과 실제 benchmark를 함께 제공하는 교체형 supplier API로 확장해야 하며, 계수를 추정해서 현재 closure에 추가하지 않는다.

공식 수치 API: [SciPy quad](https://docs.scipy.org/doc/scipy/reference/generated/scipy.integrate.quad.html), [SciPy brentq](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.brentq.html). 의존성의 실제 배포 라이선스·고지는 보존하고 공급자 데이터의 재사용 조건을 근거에 유지한다.

정지 항복 유체의 API 구별: `pressure_prediction_unique=False`이면 `outlet_pressure_pa_abs`와 `required_inlet_minus_outlet_Pa`는 None이다. 마찰 0의 참고 가지는 `minimal_branch_reference_outlet_pressure_pa_abs`와 `minimal_branch_reference_inlet_minus_outlet_Pa`에만 남긴다. 이 경우 pressure-work rate 0은 어떤 정지 항복압을 골라도 유량이 0이기 때문이며 압력이 식별되었다는 뜻이 아니다.

정적 교차검토 보강: 정지 항복 재료에서는 마찰 0의 참고 가지가 음수라도 항복 지지압 구간 안에 양의 절대압 가지가 존재할 수 있다. 참고 가지 하나로 전체 구간을 거부하지 않는다. `zero_flow_formal_outlet_pressure_interval_Pa`는 signed 수학적 구간이고 실제 절대압으로 허용되는 부분은 P>0과의 교집합이다. `minimal_branch_formal_outlet_pressure_Pa`는 음수가 될 수 있는 형식상 참고값이며 물리 절대압을 뜻하지 않는다. 물리 참고값 필드는 양수일 때만 유지한다. `zero_flow_positive_absolute_pressure_branch_exists`는 양의 가지 존재만 나타내며 압력이 식별됐다는 뜻이 아니다. 구간 전체가 비양수면 적용 범위를 벗어난다. 관련 회귀 시험은 작성만 했으며 수치 실행하지 않았다.

## 공통 불확실성 엔진 연결

`slurry_uncertainty.slurry_transport_node`는 새 샘플러나 슬러리 solver를 만들지 않고 기존 `uncertainty.evaluate_ensemble`의 `EnsembleNode`로 기존 `evaluate_slurry_transport`를 호출한다. 변수별 요청 경로와 단위를 명시하고, 각 draw에서 요청 계약·물성 지원영역·기존 solver pin·계산 영수증을 다시 확인한다. 대표 허용 경로는 유량, 관 형상, 압력/높이 경계, 중력, 혼합 밀도, snapshot 온도, 항복응력 및 지원 전단 한계다. SI 표기와 단위 문자열은 exact match를 요구한다.

일관성 계수 K는 유동 지수 n에 따라 단위가 Pa·sⁿ로 바뀌므로 자동 분포 바인딩을 허용하지 않는다. rheology 종류·유동 지수·항복응력 지지 범위의 변경은 별도 검토된 공동 시나리오 계약이 필요하다. 모든 미해결 draw는 가중치를 버리지 않고 `needs_data`로 남긴다. 개별 결과가 owner 게이트를 통과하지 못하면 그 draw의 출력은 `out_of_scope`가 된다. 기존 ensemble 요약의 설계 가중 평균·분위수·임계값 가중 범위는 그 설계에 대한 기술 통계이며 독립 trial 신뢰구간이나 운전 실패확률이 아니다. 이 접합부의 회귀 세 건은 작성했으나 누리온 검증 전이다. 재현 가능한 호출 예제는 `examples/slurry_uncertainty.py`이며, 이 파일은 검토된 synthetic snapshot을 쓰므로 실제 공급자·공정 데이터의 검토를 대신하지 않는다.
