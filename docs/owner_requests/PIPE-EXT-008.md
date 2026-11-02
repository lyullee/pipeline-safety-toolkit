# PIPE-EXT-008 — 프레임·지반·셸의 원 구조 확장

상태: 원 소유 모듈의 변경 요청입니다. 이 문서의 생성은 구현·시험·승인 증거가 아닙니다.

대상 컴포넌트: structural owner within pipeline-integrity-toolkit, PyNite upstream, matintegrity material owner

이 세션에서는 아래 소비자 계약을 유지하며 대상 원 엔진을 확장하세요. 같은 물리/통계 엔진을 복제하지 마세요. 대상이 이 저장소 내부인 항목은 여기서 구현할 로컬 작업으로 구분하고, 외부 저장소에 잘못 넘기지 마세요.

## 소비자 계약

- `pipeline_integrity.soil_pipe`: `SoilPipeRequest` — [소스](../../src/pipeline_integrity/soil_pipe.py), SHA-256 `f2929f38f5395bb5b40409656380bab66e2436f1a3530fdb2d7de366a2429cce`.
- `pipeline_integrity.buckling`: `BucklingRequest`, `BucklingMatrices` — [소스](../../src/pipeline_integrity/buckling.py), SHA-256 `1ed70f7a2a1afde40bb3d5c2daeea646409bc0c523c21692a044986d80c1d5f5`.
- `pipeline_integrity.pulling`: `PullingRequest` — [소스](../../src/pipeline_integrity/pulling.py), SHA-256 `98965942fa61038239fbf9bf3064b4bbc77925b40f8cae283cdb4a59cd48634c`.
- `pipeline_integrity.structural`: `NativeFrameAssembly`, `build_native_frame`, `audit_frame_equilibrium`, `assemble_frame_stiffness` — [소스](../../src/pipeline_integrity/structural.py), SHA-256 `89b1874a8f58b0824792fe9ea6a02c61edc25a6bbb7799ac3ee2b123552f9a3c`.

## 이 저장소에 이미 작성한 범위

- Public source-bound native frame construction, equilibrium audit and Ke/Kg assembly APIs now exist in structural.py; soil_pipe and buckling consume them.

## 요구 동작

- Preserve and stabilize the existing public build_native_frame/audit_frame_equilibrium/assemble_frame_stiffness APIs and source/DOF/support/reference-load receipts; do not recreate the factory or copied private assembly.
- Add unilateral/gap/nonlinear soil contact and finite pipe stiffness in the original structural solver with active-set and equilibrium evidence.
- Qualify shell ovalization/collapse, plasticity/imperfection and follower-load behavior separately from linear frame eigen buckling.

## 필요한 수락 증거

- Rigid/gauge and reference-load scaling, reaction equilibrium, energy and mesh/step refinement are recorded.
- No positive finite eigenvalue is not infinite strength; unsupported contact/shell domains stay unqualified.

## 공통 계약

- SI and absolute-pressure/reference-state meaning preserved
- asset/scenario/clock/ports/source/configuration and evidence bound to the evaluated request
- unknown/unsupported/nonconverged stays explicit; no inferred probability or safety authorization
- original engine owns physics/statistics; consumer adapter must not copy that engine

## 인수인계

[상세 매뉴얼](../BUCKLING_KO.md)을 먼저 읽고 현재 계약의 지원/미지원 범위를 유지하세요. 구현한 원 API·버전·소스·라이선스·실제 시험 결과와 실패/보류를 기록한 뒤 소비자 어댑터와 함께 검토하세요. 현재 수치 시험은 누리온 대기이며 실행하지 않았습니다.

연결 기술: `structural.soil_pipe`, `structural.buckling`, `construction.pull`.
