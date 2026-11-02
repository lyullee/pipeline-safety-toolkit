# 펌프 정지 동특성 공급자 경계

`pump_transient`는 펌프 정지·관성 해석기를 새로 구현하지 않는다. 정적 펌프 곡선이나
affinity 법칙을 시간응답으로 재표시하지도 않는다. 원 펌프/구동 소유자가 계산한 elapsed
시간, 각속도, 부호 있는 체적유량, 양정, 축·수력·손실 동력과 회전자 에너지 원장을
`evaluate_pump_coastdown`에서 검증한다.

`PumpCoastdownRequest`는 asset/pump/clock, 유체 밀도, 중력, 회전자 관성, 초기 각속도,
원 모델 ID, 세 가지 실제 evidence ID와 점수·동력·에너지·수치보정 예산을 받는다.
provider는 `id`, `version`, 소문자 SHA-256 source digest를 제공해야 한다. context에
hydraulic/rotor/drive evidence가 실제로 없으면 원 해석기를 호출하지 않는다.

검증기는 다음을 별도로 확인한다.

- 0에서 시작해 요청 duration에 끝나는 엄격 증가 시간격자
- 음수가 아닌 각속도와 손실동력, 유한한 signed 유량·양정·축/수력 동력
- `rho*g*Q*H`와 공급 수력동력의 호출자 허용오차
- 동력 이력의 사다리꼴 적분과 drive/hydraulic/dissipated 에너지 원장
- `0.5*I*omega^2`와 초기·최종 회전자 에너지
- 회전자 초기 + 구동 - 수력 - 손실 - 회전자 최종 + 수치보정 에너지 잔차
- 별도 수치보정 절대 상한과 provider convergence

어느 게이트라도 실패하면 결과는 `withheld`다. `verify_pump_coastdown_record`는 같은
request/context/provider로 전체 결과를 재생한다. 출력의 `moc_boundary`는 향후 결합에
필요한 시간·유량·양정을 보존하지만 `direct_moc_coupling_implemented=False`다. 현재
MOC와 펌프를 같은 시각에 연립하거나 역류 4사분면, check valve, 모터/구동 토크,
공동화·column separation을 해결했다고 주장하지 않는다.

원 소유자에 필요한 동시해법·독립 수치/실험 검증은
[PIPE-EXT-014](owner_requests/PIPE-EXT-014.md)에 정의한다. 새 회귀는 누리온 실행 대기다.
