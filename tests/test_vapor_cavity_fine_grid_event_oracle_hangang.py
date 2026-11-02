"""Fine-grid event-count and inventory oracle for the lossless vapor-cavity line.

This finer characteristic grid uses a self-contained authored synthetic case.
Closed-form event times and independent liquid/cavity volume reconstruction are
references; this is not a field claim.
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
            LiquidNode(id="reservoir", elevation_m=0.0, initial_head_m=INITIAL_HEAD, boundary="head", history=History(time_s=(0.0,), values=(INITIAL_HEAD,))),
            LiquidNode(id="withdrawal", elevation_m=0.0, initial_head_m=INITIAL_HEAD, boundary="withdrawal", history=History(time_s=(0.0, RAMP_END, HOLD_END, PULSE_END, END), values=(0.0, PEAK_WITHDRAWAL, PEAK_WITHDRAWAL, 0.0, 0.0))),
        ),
        pipes=(LiquidPipe(id="pipe", start_node="reservoir", end_node="withdrawal", length_m=LENGTH, diameter_m=DIAMETER, wave_speed_m_s=WAVE, darcy_friction_factor=0.0, initial_volume_flow_m3_s=0.0),),
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
        id=f"fine-grid-cavity-{step_s}",
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
        scenario_id="fine-grid-onset-collapse-event-reference",
        clock_id=request.clock_id,
        origin="synthetic",
        evidence=(EvidenceRef(id="review", origin="synthetic", sha256="d" * 64, citation="Authored lossless characteristic-wave reference fixture"),),
    )
    return request, context, storage_bound


def test_finer_grid_keeps_one_analytic_onset_and_collapse_without_inventory_drift():
    step_s = 0.000625
    request, context, one_cell_storage_bound = _case(step_s)
    record = evaluate_vapor_cavity_moc(request, context)

    assert record["status"] == "computed_diagnostic", record["failure"]
    assert record["grid"]["segments"] == 160
    assert record["grid"]["nominal_characteristic_step_s"] == pytest.approx(step_s, abs=1e-14)

    # This pulse creates one terminal cavity. Its onset and collapse follow
    # directly from the imposed withdrawal integral and the liquid-column
    # acoustic storage law; extra crossings indicate event chatter.
    assert len(record["events"]) == 2
    onset, collapse = record["events"]
    terminal = record["grid"]["segments"]
    assert (onset["kind"], onset["section_index"]) == ("onset", terminal)
    assert (collapse["kind"], collapse["section_index"]) == ("collapse", terminal)
    assert onset["event_time_s"] == pytest.approx(ONSET_TIME, abs=1e-7)
    assert collapse["event_time_s"] == pytest.approx(COLLAPSE_TIME, abs=1e-7)
    assert abs(collapse["proven_volume_residual_m3"]) <= request.event_volume_tolerance_m3
    assert collapse["pre_rejoining_pressure_Pa"] == pytest.approx(request.liquid.vapour_pressure_pa_abs)

    # Reconstruct the global inventory independently from the stored pressure
    # profile, cavity volumes, and the external withdrawal ledger at every step.
    dx = record["grid"]["chainage_m"][1]
    area = AREA
    gravity = GRAVITY
    wave = WAVE
    initial_head = INITIAL_HEAD
    for row in record["states"]:
        head_delta = [value - initial_head for value in row["head_m"]]
        pressure_storage = gravity * area / wave**2 * dx * (
            sum(head_delta[1:-1]) + (head_delta[0] + head_delta[-1]) / 2.0
        )
        independently_closed = (
            pressure_storage
            - sum(row["cavity_volume_m3"])
            - row["net_external_liquid_inflow_m3"]
        )
        assert independently_closed == pytest.approx(row["liquid_volume_residual_m3"], abs=1e-13)
        assert abs(independently_closed) <= one_cell_storage_bound + 1e-10

    assert record["states"][-1]["time_s"] == pytest.approx(END, abs=1e-14)
