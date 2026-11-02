# 2026-11-03 추가 독립 물리시험

총50/223개 제한 영역,477/477건 통과. 추가11개 모델102건이며 기존 실측 적합성 실패4건은 유지한다.

| 모델 ID | 추가 통과 건수 | 시험 범위 |
|---|---:|---|
| `buried_upheaval_buckling` | 3 | Straight elastic beam with distributed linear transverse foundation represented by tributary nodal springs and ideal end supports; no nonlinear soil uplift. |
| `compression.real_process_and_stage_path` | 19 | Steady ideal/native-EOS single-phase differential-efficiency gas compression with supplied stage pressures and cooling; no compressor map calibration. |
| `dynamics.linear_sdof` | 11 | Linear generalized oscillator driven by prescribed held force stages with declared damping; no nonlinear/contact response. |
| `fracture.paris_growth` | 2 | Synthetic constant-geometry Paris growth with ordered stress blocks and prescribed depth event; no material calibration or other fracture failure mechanisms. |
| `framework.evolving_thermoelastic_pipe_field` | 17 | Ideal-gas conservative field with pressure-compliant elastic area, constant exterior pressure and prescribed wall temperature; thermal-expansion wall variant remains outside this profile. |
| `liquid.fixed_inertance_column_dynamics` | 1 | Constant imposed head reversal and return of a fixed-inertance liquid column; trapped polytropic gas pocket variant remains outside this profile. |
| `pump.thermal_pipe_operating_point` | 1 | Pressure-boundary operating-point solver in the synthetic constant-property laminar limit; temperature-dependent liquid heating remains outside this profile. |
| `shell.linear_radial_pressure_dynamics` | 6 | Uniform thin isotropic shell radial pressure pulse under declared plane-stress or plane-strain idealization; no contact or nonlinear shell qualification. |
| `thermoelasticity.radial_cylinder` | 24 | Small-strain isotropic axisymmetric thermoelastic cylinder with prescribed temperature field and axial restraint or force. |
| `thermoelasticity.restrained_cylinder` | 6 | Uniform-temperature linear isotropic cylinder with zero axial strain and zero exterior pressure; no plasticity or creep. |
| `waves.cylindrical_scalar_wave` | 12 | Regular axisymmetric scalar wave with outer zero-displacement boundary and supplied speed; no elastic solid vector modes. |

실행 전후 소스 변경0건. 의존 소스를 고정한 전체 배치 최종 실행163.484초,4개 병렬 프로세스.
독립 기준과 실행 선택자는 `research/physical_verification_223.json`, 재현 명령은 `python scripts/run_physical_campaign_223.py`다.
외부 소유 프로젝트를 사용하는 시험은 해당 소스와 선택 계산 의존성의 준비가 필요하다.
의존 소스는 `PIPELINE_FORMULA_MATERIAL_SRC`로 실행 전 고정한 복사본을 지정할 수 있다.
