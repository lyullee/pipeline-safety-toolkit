"""Independent three-dimensional control-volume oracle for frame junction loads.

Inputs are synthetic and authored. This checks quasi-steady force mapping,
not local fitting stress, transient junction response, or field qualification.
"""
import runpy

import pytest

from pipeline_integrity.contracts import EvidenceRef
from pipeline_integrity.junction_loads import (
    JunctionLoadRequest,
    JunctionPort,
    REVIEWS,
    evaluate_junction_loads,
    verify_junction_load_record,
)
from pipeline_integrity.structural import FrameCombination, FrameNode

EXAMPLE = runpy.run_path("examples/pipe_frame.py")


def _three_axis_case():
    frame = EXAMPLE["make_case"]()
    branch_node = FrameNode(id="branch-tip", position_m=(0.0, 4.0, 0.0))
    vertical_tip = FrameNode(id="vertical-tip", position_m=(0.0, 0.0, 4.0))
    branch = frame.members[0].model_copy(update={"id": "branch", "j_node": branch_node.id})
    vertical_member = frame.members[0].model_copy(
        update={"id": "vertical-branch", "j_node": vertical_tip.id}
    )
    frame = frame.model_copy(
        update={
            "nodes": frame.nodes + (branch_node, vertical_tip),
            "members": frame.members + (branch, vertical_member),
            "load_cases": ("point", "junction"),
            "combinations": (
                FrameCombination(
                    id="junction-service", factors={"junction": 1.0}, evidence_id="reference"
                ),
            ),
        }
    )
    review_evidence = {name: f"review-{index}" for index, name in enumerate(REVIEWS)}
    evidence_ids = {
        "reference", "load-basis", "ambient", "inventory", "port-x", "port-y", "port-z",
        *review_evidence.values(),
    }
    context = EXAMPLE["context"]().model_copy(
        update={
            "evidence": tuple(
                EvidenceRef(
                    id=identity,
                    citation="Authored synthetic verification input",
                    origin="synthetic",
                    license_id="MIT",
                    sha256="a" * 64,
                )
                for identity in sorted(evidence_ids)
            )
        }
    )
    ports = (
        JunctionPort(
            id="east-port", member_id="pipe", physical_port_id="east",
            clock_id=frame.clock_id, time_s=frame.time_s, fluid_flow_area_m2=0.02,
            static_pressure_pa_abs=200_000.0, density_kg_m3=1000.0,
            mass_flow_out_of_control_volume_kg_s=3.0, evidence_id="port-x",
        ),
        JunctionPort(
            id="north-port", member_id="branch", physical_port_id="north",
            clock_id=frame.clock_id, time_s=frame.time_s, fluid_flow_area_m2=0.01,
            static_pressure_pa_abs=200_000.0, density_kg_m3=1000.0,
            mass_flow_out_of_control_volume_kg_s=-1.0, evidence_id="port-y",
        ),
        JunctionPort(
            id="vertical-port", member_id="vertical-branch", physical_port_id="vertical",
            clock_id=frame.clock_id, time_s=frame.time_s, fluid_flow_area_m2=0.04,
            static_pressure_pa_abs=200_000.0, density_kg_m3=1000.0,
            mass_flow_out_of_control_volume_kg_s=-2.0, evidence_id="port-z",
        ),
    )
    request = JunctionLoadRequest(
        id="synthetic-three-axis-junction", frame=frame, junction_node_id="anchor",
        case_id="junction", ports=ports, exterior_ambient_pressure_pa_abs=100_000.0,
        fluid_mass_in_control_volume_kg=3.0, gravity_vector_m_s2=(0.0, 0.0, -9.81),
        fluid_inventory_and_gravity_evidence_id="inventory",
        ambient_pressure_evidence_id="ambient", reviews={name: True for name in REVIEWS},
        review_evidence_ids=review_evidence, evidence_ids=("load-basis", "ambient", "inventory"),
        solve_prepared_frame=False,
    )
    return request, context


def test_three_axis_pressure_momentum_and_inventory_weight_match_closed_form():
    request, context = _three_axis_case()
    record = evaluate_junction_loads(request, context)
    assert record["status"] == "ready", "\\n".join(record.get("issues", []))
    assert record["mass_balance_residual_kg_s"] == pytest.approx(0.0, abs=1e-14)
    # For each port, F_out=(p_abs-p_ambient)A + mdot^2/(rho A).
    # The load on the junction opposes each outward port axis; gravity applies
    # to the separately declared lumped fluid inventory.
    assert record["fluid_on_wall_force_vector_N"] == pytest.approx([-2000.45, -1000.1, -4029.53], abs=1e-9)
    terms = {item["physical_port_id"]: item for item in record["port_force_terms"]}
    assert terms["vertical"]["gauge_pressure_force_magnitude_N"] == pytest.approx(4000.0)
    assert terms["vertical"]["momentum_flux_magnitude_N"] == pytest.approx(0.1)
    assert record["structural_solve_executed"] is False
    assert verify_junction_load_record(record, request=request, context=context)


def test_common_ambient_shift_and_global_flow_reversal_preserve_resultant():
    request, context = _three_axis_case()
    baseline = evaluate_junction_loads(request, context)
    shifted = request.model_copy(
        update={
            "exterior_ambient_pressure_pa_abs": request.exterior_ambient_pressure_pa_abs + 250_000.0,
            "ports": tuple(
                port.model_copy(update={"static_pressure_pa_abs": port.static_pressure_pa_abs + 250_000.0})
                for port in request.ports
            ),
        }
    )
    shifted_record = evaluate_junction_loads(shifted, context)
    assert shifted_record["fluid_on_wall_force_vector_N"] == pytest.approx(
        baseline["fluid_on_wall_force_vector_N"], abs=1e-9
    )
    reversed_request = request.model_copy(
        update={
            "ports": tuple(
                port.model_copy(
                    update={"mass_flow_out_of_control_volume_kg_s": -port.mass_flow_out_of_control_volume_kg_s}
                )
                for port in request.ports
            )
        }
    )
    reversed_record = evaluate_junction_loads(reversed_request, context)
    assert reversed_record["fluid_on_wall_force_vector_N"] == pytest.approx(
        baseline["fluid_on_wall_force_vector_N"], abs=1e-9
    )
    assert reversed_record["mass_balance_residual_kg_s"] == pytest.approx(0.0, abs=1e-14)
