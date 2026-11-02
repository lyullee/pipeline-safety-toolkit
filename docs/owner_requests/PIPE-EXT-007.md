# PIPE-EXT-007 — 동적 파괴 진행과 정지 원 궤적

상태: 원 소유 모듈의 변경 요청입니다. 이 문서의 생성은 구현·시험·승인 증거가 아닙니다.

대상 컴포넌트: matintegrity material-mechanics owner, lh2dt_models decompression owner

이 세션에서는 아래 소비자 계약을 유지하며 대상 원 엔진을 확장하세요. 같은 물리/통계 엔진을 복제하지 마세요. 대상이 이 저장소 내부인 항목은 여기서 구현할 로컬 작업으로 구분하고, 외부 저장소에 잘못 넘기지 마세요.

## 소비자 계약

- `pipeline_integrity.fracture_arrest`: `FractureArrestRequest`, `ArrestTrajectory` — [소스](../../src/pipeline_integrity/fracture_arrest.py), SHA-256 `ef609e4c444376229026006d42f3ad056e91d780202c06ac8005657f605d5657`.

## 요구 동작

- Couple dynamic crack driving energy/resistance, decompression/EOS and propagation velocity on the same space/time path.
- Expose material orientation, temperature/rate support and native convergence/budget receipts.
- Static KI/Paris outputs cannot substitute for dynamic arrest resistance or continuous hold evidence.

## 필요한 수락 증거

- Independent propagation/arrest experiment qualification and spatial/time refinement with pinned sources.
- Re-initiation/gaps and withheld native states never become proved arrest from stationary sample windows.

## 공통 계약

- SI and absolute-pressure/reference-state meaning preserved
- asset/scenario/clock/ports/source/configuration and evidence bound to the evaluated request
- unknown/unsupported/nonconverged stays explicit; no inferred probability or safety authorization
- original engine owns physics/statistics; consumer adapter must not copy that engine

## 인수인계

[상세 매뉴얼](../FRACTURE_ARREST_KO.md)을 먼저 읽고 현재 계약의 지원/미지원 범위를 유지하세요. 구현한 원 API·버전·소스·라이선스·실제 시험 결과와 실패/보류를 기록한 뒤 소비자 어댑터와 함께 검토하세요. 현재 수치 시험은 누리온 대기이며 실행하지 않았습니다.

연결 기술: `structural.fracture_arrest`.
