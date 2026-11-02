"""Pre-reflection vapor onset/rejoining reference; execute numerically on Nurion.

The incident right-going invariant stays constant before the reservoir reflection
returns. Endpoint liquid inflow at saturation is therefore known independently.
Integrating the specified withdrawal minus that inflow gives a cavity-volume
reference and its two event times without invoking the implementation's solver.
"""

from functools import lru_cache
import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.transient import History, LiquidNode, LiquidPipe, LiquidTransient
from pipeline_integrity.vapor_cavity_moc import (
    VaporCavityMOCRequest,
    evaluate_vapor_cavity_moc,
    vapor_cavity_moc_signal_frames,
)


LENGTH = 100.0
WAVE_SPEED = 1000.0
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
END = 0.09
AREA = math.pi * DIAMETER**2 / 4
INITIAL_PRESSURE = AMBIENT + DENSITY * GRAVITY * INITIAL_HEAD
CRITICAL_FLOW = AREA * (INITIAL_PRESSURE - SATURATION) / (DENSITY * WAVE_SPEED)
ONSET = RAMP_END * CRITICAL_FLOW / PEAK_WITHDRAWAL
WITHDRAWN_VOLUME = PEAK_WITHDRAWAL * (HOLD_END - RAMP_END + RAMP_END)
COLLAPSE = WITHDRAWN_VOLUME / CRITICAL_FLOW + ONSET / 2


def _withdrawal_integral(time):
    if time <= RAMP_END:
        return PEAK_WITHDRAWAL * time**2 / (2 * RAMP_END)
    first = PEAK_WITHDRAWAL * RAMP_END / 2
    if time <= HOLD_END:
        return first + PEAK_WITHDRAWAL * (time - RAMP_END)
    middle = first + PEAK_WITHDRAWAL * (HOLD_END - RAMP_END)
    elapsed = min(time, PULSE_END) - HOLD_END
    return middle + PEAK_WITHDRAWAL * (elapsed - elapsed**2 / (2 * (PULSE_END - HOLD_END)))


def _analytic_void(time):
    if not ONSET < time < COLLAPSE:
        return 0.0
    return _withdrawal_integral(time) - _withdrawal_integral(ONSET) - CRITICAL_FLOW * (time - ONSET)


def _case(dt):
    dx = WAVE_SPEED * dt
    # A grid node's pressure jump has a finite trapezoidal storage weight.
    # This one-cell compressibility bound is specified before execution and
    # shrinks with dx. It does not assert exact finite-grid liquid conservation.
    storage_bound = AREA * dx * (INITIAL_PRESSURE - SATURATION) / (DENSITY * WAVE_SPEED**2)
    liquid = LiquidTransient(
        nodes=(
            LiquidNode(id="reservoir", elevation_m=0.0, initial_head_m=INITIAL_HEAD,
                       boundary="head", history=History(time_s=(0.0,), values=(INITIAL_HEAD,))),
            LiquidNode(id="withdrawal", elevation_m=0.0, initial_head_m=INITIAL_HEAD,
                       boundary="withdrawal", history=History(
                           time_s=(0.0, RAMP_END, HOLD_END, PULSE_END, END),
                           values=(0.0, PEAK_WITHDRAWAL, PEAK_WITHDRAWAL, 0.0, 0.0))),
        ),
        pipes=(LiquidPipe(id="pipe", start_node="reservoir", end_node="withdrawal",
                         length_m=LENGTH, diameter_m=DIAMETER, wave_speed_m_s=WAVE_SPEED,
                         darcy_friction_factor=0.0, initial_volume_flow_m3_s=0.0),),
        duration_s=END, requested_timestep_s=dt, density_kg_m3=DENSITY,
        gravity_m_s2=GRAVITY, ambient_pressure_pa_abs=AMBIENT,
        vapour_pressure_pa_abs=SATURATION,
    )
    request = VaporCavityMOCRequest(
        id="pre_reflection_vapor_pulse", asset_id="pipe", clock_id="seconds", liquid=liquid,
        maximum_absolute_pressure_Pa=1e6,
        event_time_tolerance_s=1e-11, event_pressure_tolerance_Pa=1e-4,
        event_volume_tolerance_m3=1e-11,
        global_liquid_volume_tolerance_m3=storage_bound + 1e-12,
        relative_global_volume_tolerance=1e-9,
        parameter_evidence_id="review", saturation_and_initial_void_evidence_id="review",
        discretization_review_evidence_id="review",
        pure_vapor_negligible_vapor_mass_and_constant_saturation_pressure_reviewed=True,
        characteristic_interpolation_cavity_events_and_grid_sensitivity_reviewed=True,
        small_void_fraction_acoustic_liquid_and_omitted_thermal_energy_reviewed=True,
    )
    context = RunContext(
        asset_id="pipe", scenario_id="pre_reflection_pulse", clock_id="seconds", origin="simulation",
        evidence=(EvidenceRef(id="review", origin="synthetic", sha256="c" * 64,
                              citation="Authored frictionless pre-reflection characteristic reference"),),
    )
    return request, context, storage_bound


@lru_cache(maxsize=4)
def _solution(dt):
    request, context, bound = _case(dt)
    return request, evaluate_vapor_cavity_moc(request, context), bound


@pytest.mark.parametrize("dt", [0.005, 0.0025, 0.00125, 0.000625])
def test_vapor_onset_collapse_and_rejoining_match_independent_pre_reflection_solution(dt):
    request, record, _ = _solution(dt)
    assert ONSET < HOLD_END < PULSE_END < COLLAPSE < END < 2 * LENGTH / WAVE_SPEED
    assert record["status"] == "computed_diagnostic", record["failure"]
    assert 0 < record["maximum_onset_pressure_roundoff_bound_Pa"] < 1e-6
    assert record["positive_cavity_inventory_roundoff_threshold_applied"] is False
    n = record["grid"]["segments"]
    events = record["events"]
    assert [(e["section_index"], e["kind"]) for e in events] == [(n, "onset"), (n, "collapse")]
    assert events[0]["event_time_s"] == pytest.approx(ONSET, abs=1e-7)
    assert events[1]["event_time_s"] == pytest.approx(COLLAPSE, abs=1e-7)
    assert abs(events[0]["pressure_crossing_residual_Pa"]) <= request.event_pressure_tolerance_Pa
    collapse = events[1]
    assert abs(collapse["proven_volume_residual_m3"]) <= request.event_volume_tolerance_m3
    assert collapse["pre_rejoining_pressure_Pa"] == SATURATION
    assert collapse["post_rejoining_pressure_Pa"] == pytest.approx(INITIAL_PRESSURE, abs=1e-3)
    assert collapse["rejoined_flow_m3_s"] == pytest.approx(0.0, abs=1e-12)
    assert collapse["upstream_flow_before_m3_s"] > collapse["downstream_flow_before_m3_s"]
    assert any(row["cavity_volume_m3"][-1] > 0 for row in record["states"])
    for row in record["states"]:
        assert row["cavity_volume_m3"][-1] == pytest.approx(_analytic_void(row["time_s"]), abs=1e-9)
        assert min(row["cavity_volume_m3"]) >= 0.0
        assert min(row["absolute_pressure_Pa"]) >= SATURATION - request.event_pressure_tolerance_Pa


def test_vapor_volume_ledger_and_grid_refinement_have_independent_shrinking_storage_bound():
    coarse_bound = None
    maximum_residuals = []
    for dt in (0.005, 0.0025, 0.00125, 0.000625):
        request, record, bound = _solution(dt)
        assert record["status"] == "computed_diagnostic", record["failure"]
        dx = record["grid"]["chainage_m"][1]
        maximum_residual = 0.0
        for row in record["states"]:
            head_change = [head - INITIAL_HEAD for head in row["head_m"]]
            integral = dx * (sum(head_change[1:-1]) + (head_change[0] + head_change[-1]) / 2)
            storage = GRAVITY * AREA / WAVE_SPEED**2 * integral
            residual = storage - sum(row["cavity_volume_m3"]) - row["net_external_liquid_inflow_m3"]
            assert storage == pytest.approx(row["linearized_liquid_pressure_storage_m3"], abs=1e-13)
            assert residual == pytest.approx(row["liquid_volume_residual_m3"], abs=1e-13)
            assert abs(residual) <= bound + 1e-10
            maximum_residual = max(maximum_residual, abs(residual))
        last = record["states"][-1]
        for index, volume in enumerate(last["cavity_volume_m3"]):
            assert volume == pytest.approx(
                record["gross_cavity_growth_m3"][index]
                - record["gross_cavity_condensation_m3"][index]
                + record["event_roundoff_volume_projection_m3"][index],
                abs=request.event_volume_tolerance_m3,
            )
        if coarse_bound is not None:
            assert bound == pytest.approx(coarse_bound / 2, rel=1e-12)
        coarse_bound = bound
        maximum_residuals.append(maximum_residual)
    # A halved mesh should reduce the leading endpoint storage quadrature
    # error; the additive floor covers arithmetic rather than a fitted result.
    for coarse, fine in zip(maximum_residuals, maximum_residuals[1:]):
        assert fine <= 0.75 * coarse + 1e-10


def test_vapor_event_signal_projection_keeps_all_original_rows_and_rejoining_side():
    _, record, _ = _solution(0.0025)
    assert record["status"] == "computed_diagnostic", record["failure"]
    n = record["grid"]["segments"]
    frames, receipt = vapor_cavity_moc_signal_frames(record, section_index=n, stream_id="outlet")
    groups = receipt["source_row_groups"]
    assert [index for group in groups for index in group] == list(range(len(record["states"])))
    assert len(frames) == len(groups) == receipt["published_frame_count"]
    assert all(b.time_s > a.time_s for a, b in zip(frames, frames[1:]))
    for frame, group in zip(frames, groups, strict=True):
        row = record["states"][group[-1]]
        assert all(record["states"][index]["time_s"] == frame.time_s for index in group)
        assert frame.values["pressure_pa_abs"] == row["absolute_pressure_Pa"][n]
        assert frame.values["cavity_volume_m3"] == row["cavity_volume_m3"][-1]
    assert frames[-1].values["active_vapor_cavity"] == 0.0
    assert frames[-1].values["pressure_pa_abs"] == pytest.approx(INITIAL_PRESSURE, abs=1e-3)


def test_real_sub_tolerance_pressure_excursion_still_nucleates_positive_vapor_inventory():
    request, context, _ = _case(0.0025)
    # This depression is smaller than the declared event residual tolerance,
    # but much larger than characteristic floating-point cancellation. It must
    # remain a physical crossing rather than being hidden by that user tolerance.
    peak = CRITICAL_FLOW * (1 + 1e-10)
    outlet = request.liquid.nodes[-1]
    history = outlet.history.model_copy(update={"values": (0.0, peak, peak, 0.0, 0.0)})
    outlet = outlet.model_copy(update={"history": history})
    liquid = request.liquid.model_copy(update={"nodes": (request.liquid.nodes[0], outlet)})
    record = evaluate_vapor_cavity_moc(request.model_copy(update={"liquid": liquid}), context)
    assert record["status"] == "computed_diagnostic", record["failure"]
    depression = DENSITY * WAVE_SPEED * (peak - CRITICAL_FLOW) / AREA
    assert record["maximum_onset_pressure_roundoff_bound_Pa"] < depression < request.event_pressure_tolerance_Pa
    n = record["grid"]["segments"]
    assert any(event["kind"] == "onset" and event["section_index"] == n for event in record["events"])
    assert max(row["cavity_volume_m3"][-1] for row in record["states"]) > 0.0
