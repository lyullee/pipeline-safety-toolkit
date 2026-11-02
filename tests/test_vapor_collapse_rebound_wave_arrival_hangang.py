"""Track the post-collapse pressure pulse through a vapor-cavity MOC line.

Synthetic single-phase liquid and pure-vapor acoustic fixture. The cavity
event is independently timed before the first reservoir round trip; the test
then measures its regenerated pressure wave at an upstream grid station and
reconstructs the supported liquid/void volume budget.
"""

import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.transient import History, LiquidNode, LiquidPipe, LiquidTransient
from pipeline_integrity.vapor_cavity_moc import VaporCavityMOCRequest, evaluate_vapor_cavity_moc


LENGTH = 100.0
WAVE = 1000.0
DIAMETER = 0.1
DENSITY = 1000.0
GRAVITY = 9.80665
INITIAL_HEAD = 10.0
AMBIENT = 101325.0
SATURATION = 2000.0
PEAK_WITHDRAWAL = 0.002
RAMP_END = 0.02
HOLD_END = 0.06
PULSE_END = 0.08
END = 0.11
MEASURE_X = 80.0
AREA = math.pi * DIAMETER**2 / 4.0
INITIAL_PRESSURE = AMBIENT + DENSITY * GRAVITY * INITIAL_HEAD
CRITICAL_FLOW = AREA * (INITIAL_PRESSURE - SATURATION) / (DENSITY * WAVE)
ONSET_TIME = RAMP_END * CRITICAL_FLOW / PEAK_WITHDRAWAL
TOTAL_WITHDRAWAL = PEAK_WITHDRAWAL * HOLD_END
COLLAPSE_TIME = TOTAL_WITHDRAWAL / CRITICAL_FLOW + ONSET_TIME / 2.0


def _case(step_s):
    dx = WAVE * step_s
    storage_bound = AREA * dx * (INITIAL_PRESSURE - SATURATION) / (DENSITY * WAVE**2)
    liquid = LiquidTransient(
        nodes=(
            LiquidNode(
                id="reservoir",
                elevation_m=0.0,
                initial_head_m=INITIAL_HEAD,
                boundary="head",
                history=History(time_s=(0.0,), values=(INITIAL_HEAD,)),
            ),
            LiquidNode(
                id="withdrawal",
                elevation_m=0.0,
                initial_head_m=INITIAL_HEAD,
                boundary="withdrawal",
                history=History(
                    time_s=(0.0, RAMP_END, HOLD_END, PULSE_END, END),
                    values=(0.0, PEAK_WITHDRAWAL, PEAK_WITHDRAWAL, 0.0, 0.0),
                ),
            ),
        ),
        pipes=(
            LiquidPipe(
                id="pipe",
                start_node="reservoir",
                end_node="withdrawal",
                length_m=LENGTH,
                diameter_m=DIAMETER,
                wave_speed_m_s=WAVE,
                darcy_friction_factor=0.0,
                initial_volume_flow_m3_s=0.0,
            ),
        ),
        duration_s=END,
        requested_timestep_s=step_s,
        density_kg_m3=DENSITY,
        gravity_m_s2=GRAVITY,
        ambient_pressure_pa_abs=AMBIENT,
        vapour_pressure_pa_abs=SATURATION,
        maximum_wave_speed_adjustment=1e-12,
        mass_balance_tolerance_m3=1e-5,
    )
    request = VaporCavityMOCRequest(
        id=f"collapse-rebound-{step_s}",
        asset_id="synthetic-vapor-line",
        clock_id="elapsed",
        liquid=liquid,
        maximum_absolute_pressure_Pa=1e6,
        event_time_tolerance_s=1e-11,
        event_pressure_tolerance_Pa=1e-4,
        event_volume_tolerance_m3=1e-11,
        global_liquid_volume_tolerance_m3=storage_bound + 1e-12,
        relative_global_volume_tolerance=1e-9,
        parameter_evidence_id="review",
        saturation_and_initial_void_evidence_id="review",
        discretization_review_evidence_id="review",
        pure_vapor_negligible_vapor_mass_and_constant_saturation_pressure_reviewed=True,
        characteristic_interpolation_cavity_events_and_grid_sensitivity_reviewed=True,
        small_void_fraction_acoustic_liquid_and_omitted_thermal_energy_reviewed=True,
    )
    context = RunContext(
        asset_id=request.asset_id,
        scenario_id="collapse-pressure-front-to-interior-station",
        clock_id=request.clock_id,
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="review",
                origin="synthetic",
                sha256="d" * 64,
                citation="Authored lossless characteristic-wave reference fixture",
            ),
        ),
    )
    return request, context, storage_bound


@pytest.mark.parametrize("step_s", (0.0025, 0.00125))
def test_collapse_rejoins_liquid_and_regenerated_pressure_wave_reaches_upstream_station(step_s):
    request, context, storage_bound = _case(step_s)
    record = evaluate_vapor_cavity_moc(request, context)
    assert record["status"] == "computed_diagnostic", record["failure"]
    assert ONSET_TIME < HOLD_END < PULSE_END < COLLAPSE_TIME < END < 2.0 * LENGTH / WAVE
    assert record["grid"]["nominal_characteristic_step_s"] == pytest.approx(step_s, abs=1e-14)

    terminal = record["grid"]["segments"]
    events = record["events"]
    onset = next(event for event in events if event["kind"] == "onset" and event["section_index"] == terminal)
    collapse = next(event for event in events if event["kind"] == "collapse" and event["section_index"] == terminal)
    assert onset["event_time_s"] == pytest.approx(ONSET_TIME, abs=1e-7)
    assert collapse["event_time_s"] == pytest.approx(COLLAPSE_TIME, abs=1e-7)
    assert abs(collapse["proven_volume_residual_m3"]) <= request.event_volume_tolerance_m3
    assert collapse["pre_rejoining_pressure_Pa"] == SATURATION
    pressure_jump = collapse["post_rejoining_pressure_Pa"] - collapse["pre_rejoining_pressure_Pa"]
    assert pressure_jump > 0.9 * (INITIAL_PRESSURE - SATURATION)
    assert collapse["rejoined_flow_m3_s"] == pytest.approx(0.0, abs=1e-12)

    dx = record["grid"]["chainage_m"][1]
    station = round(MEASURE_X / dx)
    assert record["grid"]["chainage_m"][station] == pytest.approx(MEASURE_X, abs=1e-12)
    expected_arrival = collapse["event_time_s"] + (LENGTH - MEASURE_X) / WAVE
    threshold = 0.1 * pressure_jump
    before_rows = [row for row in record["states"] if row["time_s"] <= expected_arrival - 2 * step_s]
    after_rows = [row for row in record["states"] if row["time_s"] >= expected_arrival]
    assert before_rows and after_rows
    before = before_rows[-1]["absolute_pressure_Pa"][station]
    pressure_crossing = next(
        row for row in after_rows
        if row["absolute_pressure_Pa"][station] >= before + threshold
    )
    assert pressure_crossing["time_s"] == pytest.approx(expected_arrival, abs=3 * step_s)

    for row in record["states"]:
        head_change = [head - INITIAL_HEAD for head in row["head_m"]]
        integral = dx * (sum(head_change[1:-1]) + (head_change[0] + head_change[-1]) / 2.0)
        storage = GRAVITY * AREA / WAVE**2 * integral
        independently_reconstructed_residual = (
            storage - math.fsum(row["cavity_volume_m3"]) - row["net_external_liquid_inflow_m3"]
        )
        assert storage == pytest.approx(row["linearized_liquid_pressure_storage_m3"], abs=1e-13)
        assert independently_reconstructed_residual == pytest.approx(row["liquid_volume_residual_m3"], abs=1e-13)
        assert abs(independently_reconstructed_residual) <= storage_bound + 1e-10
