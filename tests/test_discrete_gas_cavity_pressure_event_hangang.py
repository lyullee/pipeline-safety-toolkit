"""Positive-gas discrete cavity response to an aligned withdrawal event.

Synthetic authored line and gas inventory. The test checks event onset,
polytropic gas invariants, fixed-gas mass, expansion work and liquid-volume
conservation at each accepted state across two MOC timestep grids.
"""

import math

import pytest

from pipeline_integrity.cavity_moc import CavityMOCRequest, evaluate_cavity_moc
from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.transient import History, LiquidNode, LiquidPipe, LiquidTransient


RHO = 1000.0
G = 9.80665
AMBIENT = 101325.0
VAPOUR = 2000.0
HEAD = 10.0
R_GAS = 287.0
T_GAS = 300.0
EXPONENT = 1.4
VOID = 1e-3
FLOW = 1e-5
LENGTH = 100.0
DIAMETER = 0.1
WAVE = 1000.0
EVENT_START = 0.04
EVENT_FULL = 0.05
END = 0.1


def _case(step_s):
    sections = round(LENGTH / (WAVE * step_s))
    dx = LENGTH / sections
    area = math.pi * DIAMETER**2 / 4.0
    volumes = [VOID * area * dx] * sections
    volumes[-1] *= 0.5  # terminal dual cell has half the interior width
    liquid = LiquidTransient(
        nodes=(
            LiquidNode(
                id="reservoir",
                elevation_m=0.0,
                initial_head_m=HEAD,
                boundary="head",
                history=History(time_s=(0.0,), values=(HEAD,)),
            ),
            LiquidNode(
                id="outlet",
                elevation_m=0.0,
                initial_head_m=HEAD,
                boundary="withdrawal",
                history=History(
                    time_s=(0.0, EVENT_START, EVENT_FULL, END),
                    values=(0.0, 0.0, FLOW, FLOW),
                ),
            ),
        ),
        pipes=(
            LiquidPipe(
                id="line",
                start_node="reservoir",
                end_node="outlet",
                length_m=LENGTH,
                diameter_m=DIAMETER,
                wave_speed_m_s=WAVE,
                darcy_friction_factor=0.0,
                initial_volume_flow_m3_s=0.0,
            ),
        ),
        duration_s=END,
        requested_timestep_s=step_s,
        density_kg_m3=RHO,
        ambient_pressure_pa_abs=AMBIENT,
        vapour_pressure_pa_abs=VAPOUR,
        maximum_wave_speed_adjustment=1e-12,
        mass_balance_tolerance_m3=1e-8,
    )
    request = CavityMOCRequest(
        id=f"cavity-event-{step_s}",
        asset_id="synthetic-positive-gas-cavity-line",
        clock_id="elapsed",
        liquid=liquid,
        initial_cavity_volume_m3=tuple(volumes),
        initial_gas_temperature_K=(T_GAS,) * sections,
        polytropic_exponent=(EXPONENT,) * sections,
        noncondensable_specific_gas_constant_J_kg_K=R_GAS,
        minimum_cavity_volume_m3=1e-12,
        maximum_supported_absolute_pressure_Pa=1e6,
        absolute_cavity_volume_tolerance_m3=1e-12,
        relative_cavity_volume_tolerance=1e-9,
        relative_fixed_gas_mass_tolerance=1e-10,
        absolute_global_liquid_volume_tolerance_m3=1e-9,
        relative_global_liquid_volume_tolerance=1e-7,
        parameter_evidence_id="authored-fixture",
        gas_inventory_evidence_id="authored-fixture",
        closure_review_evidence_id="authored-fixture",
        supplied_fixed_gas_locations_amount_and_polytropic_response_reviewed=True,
        constant_liquid_vapor_pressure_and_negligible_vapor_mass_reviewed=True,
        small_void_fraction_acoustic_liquid_and_omitted_slip_reviewed=True,
    )
    evidence = EvidenceRef(
        id="authored-fixture",
        citation="Synthetic analytical fixture created for numerical verification",
        origin="synthetic",
        sha256="a" * 64,
    )
    context = RunContext(
        asset_id=request.asset_id,
        scenario_id="aligned-downstream-withdrawal-event",
        clock_id=request.clock_id,
        origin="synthetic",
        evidence=(evidence,),
    )
    return request, context


@pytest.mark.parametrize("step_s", (0.01, 0.005))
def test_positive_gas_cavity_event_preserves_polytropic_mass_and_volume_ledgers(step_s):
    request, context = _case(step_s)
    record = evaluate_cavity_moc(request, context)
    assert record["status"] == "computed_diagnostic", record["failure"]
    assert record["grid"]["segments"] == round(LENGTH / (WAVE * step_s))
    rows = record["states"]
    pressure0 = AMBIENT + RHO * G * HEAD
    partial0 = pressure0 - VAPOUR
    initial_volume = request.initial_cavity_volume_m3
    initial_mass = record["initial_noncondensable_gas_mass_kg"]

    before = [row for row in rows if row["time_s"] < EVENT_START]
    assert before
    for row in before:
        assert row["cavity_volume_m3"] == pytest.approx(initial_volume, rel=1e-12)
        assert row["absolute_pressure_Pa"] == pytest.approx([pressure0] * (len(initial_volume) + 1), abs=1e-6)

    def first_changed_time():
        for row in rows:
            if any(abs(v - v0) > 1e-15 for v, v0 in zip(row["cavity_volume_m3"], initial_volume, strict=True)):
                return row["time_s"]
        raise AssertionError("withdrawal event produced no cavity volume response")

    onset = first_changed_time()
    assert EVENT_START < onset <= EVENT_START + step_s + 1e-12
    assert sum(rows[-1]["cavity_volume_m3"]) > math.fsum(initial_volume)

    for row in rows:
        for i, (volume, partial, temperature, work) in enumerate(
            zip(
                row["cavity_volume_m3"],
                row["gas_partial_pressure_Pa"],
                row["gas_temperature_K"],
                row["gas_partial_pressure_expansion_work_J"],
                strict=True,
            )
        ):
            v0 = initial_volume[i]
            assert partial * volume**EXPONENT == pytest.approx(partial0 * v0**EXPONENT, rel=2e-9)
            assert partial * volume / (R_GAS * temperature) == pytest.approx(initial_mass[i], rel=2e-9)
            expected_work = (partial * volume - partial0 * v0) / (1.0 - EXPONENT)
            assert work == pytest.approx(expected_work, rel=2e-8, abs=1e-11)

        independent_residual = (
            row["linearized_liquid_pressure_storage_change_m3"]
            - math.fsum(v - v0 for v, v0 in zip(row["cavity_volume_m3"], initial_volume, strict=True))
            - row["integrated_external_liquid_inflow_m3"]
        )
        assert abs(independent_residual) <= 1.1e-9
