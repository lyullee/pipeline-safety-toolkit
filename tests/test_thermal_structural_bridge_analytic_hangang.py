"""Independent oracle for thermal-chain to frame-mesh snapshot binding."""

import pytest

from examples.pipe_frame import make_case
from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.liquid_thermal_chain import (
    LiquidThermalCell,
    LiquidThermalRequest,
    LiquidThermalWindow,
    evaluate_liquid_thermal_chain,
)
from pipeline_integrity.structural import FrameRequest
from pipeline_integrity.thermal_expansion import ExpansionCurve, ExpansionKnot
from pipeline_integrity.thermal_structural_bridge import (
    WallCellPieceMapping,
    WallMemberMapping,
    prepare_wall_temperature_frame,
)


def test_wall_snapshot_binding_matches_heating_and_piecewise_linear_expansion_oracle():
    # Deliberately isolate wall storage: with no fluid coupling or ambient loss,
    # the exact balance is C_wall * delta_T = Q_wall * duration.
    evidence_ids = ("reference", "thermal-input", "mapping", "expansion")
    evidence = tuple(
        EvidenceRef(
            id=identity,
            citation=f"Authored synthetic {identity} inputs",
            origin="synthetic",
            sha256=digest(("thermal-frame-bridge-oracle", identity)),
        )
        for identity in evidence_ids
    )
    context = RunContext(
        asset_id="authored-frame",
        scenario_id="bridge-heating-oracle",
        clock_id="declared-snapshot",
        origin="synthetic",
        evidence=evidence,
    )

    duration_s = 0.1
    capacity_J_K = 1200.0
    heat_W = 1200.0
    thermal_request = LiquidThermalRequest(
        id="single-wall-heating",
        asset_id=context.asset_id,
        clock_id=context.clock_id,
        density_kg_m3=1000.0,
        heat_capacity_J_kg_K=4000.0,
        cells=(
            LiquidThermalCell(
                id="cell-0",
                fluid_volume_m3=0.001,
                wall_heat_capacity_J_K=capacity_J_K,
                fluid_wall_conductance_W_K=0.0,
                wall_ambient_conductance_W_K=0.0,
                initial_fluid_temperature_K=300.0,
                initial_wall_temperature_K=300.0,
                evidence_id="thermal-input",
            ),
        ),
        windows=(
            LiquidThermalWindow(
                id="heating-window",
                duration_s=duration_s,
                mass_flow_left_to_right_kg_s=0.0,
                left_supply_temperature_K=300.0,
                right_supply_temperature_K=300.0,
                ambient_temperature_K=300.0,
                wall_heat_W=(heat_W,),
                evidence_id="thermal-input",
            ),
        ),
        maximum_step_s=duration_s,
        constant_properties_and_single_phase_reviewed=True,
        mechanical_energy_absent_or_in_supplied_heat_reviewed=True,
        evidence_ids=("thermal-input",),
    )
    source = evaluate_liquid_thermal_chain(thermal_request, context)

    frame_payload = make_case().model_dump(mode="python")
    frame_payload.update(time_s=duration_s, node_loads=(), line_loads=())
    frame = FrameRequest.model_validate(frame_payload)
    curve = ExpansionCurve(
        id="synthetic-linear-alpha",
        material_id="declared-elastic",
        knots=(
            ExpansionKnot(temperature_K=290.0, tangent_expansion_per_K=10e-6),
            ExpansionKnot(temperature_K=310.0, tangent_expansion_per_K=30e-6),
        ),
        evidence_id="expansion",
        tangent_small_engineering_strain_basis_reviewed=True,
    )
    mapping = WallMemberMapping(
        member_id="pipe",
        case_id="point",
        section_y_extent_m=(-0.01, 0.01),
        section_z_extent_m=(-0.01, 0.01),
        pieces=(
            WallCellPieceMapping(
                source_cell_id="cell-0",
                start_fraction=0.0,
                end_fraction=1.0,
                stress_free_temperature_K=300.0,
                expansion_curve=curve,
                evidence_id="mapping",
            ),
        ),
        uniform_section_temperature_and_axial_cell_mapping_reviewed=True,
    )

    derived, receipt = prepare_wall_temperature_frame(
        source, frame, (mapping,), context, state_index=1
    )

    expected_wall_temperature_K = 300.0 + heat_W * duration_s / capacity_J_K
    assert source["states"][1]["wall_temperature_K"][0] == pytest.approx(
        expected_wall_temperature_K, abs=1e-12
    )
    piece = derived.members[0].pieces[0]
    assert derived.frame.time_s == duration_s
    assert piece.centroid_temperature_K == pytest.approx(
        expected_wall_temperature_K, abs=1e-12
    )

    # Independent trapezoidal integral of alpha(T)=10e-6 + (T-290)*1e-6.
    delta_T_K = expected_wall_temperature_K - 300.0
    expected_strain = (20e-6 + 0.5e-6 * delta_T_K) * delta_T_K
    expected_equivalent_alpha = expected_strain / delta_T_K
    assert receipt["mapping_records"][0]["expansion"]["free_engineering_strain"] == pytest.approx(
        expected_strain, rel=1e-12, abs=1e-15
    )
    assert piece.expansion_coefficient_per_K == pytest.approx(
        expected_equivalent_alpha, rel=1e-12, abs=1e-15
    )
    assert receipt["source_state_index"] == 1
    assert receipt["source_time_s"] == duration_s
    assert receipt["cross_section_gradient_inferred"] is False
