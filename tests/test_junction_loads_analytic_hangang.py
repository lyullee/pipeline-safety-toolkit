"""Independent control-volume oracle for quasi-steady junction resultants.

Synthetic, authored port states exercise the load-preparation API only; no
measured plant data or field qualification is represented.
"""

import runpy

import pytest

from pipeline_integrity.contracts import EvidenceRef
from pipeline_integrity.junction_loads import (
    JunctionLoadRequest,
    JunctionPort,
    REVIEWS,
    evaluate_junction_loads,
)
from pipeline_integrity.structural import FrameCombination, FrameNode


EXAMPLE = runpy.run_path("examples/pipe_frame.py")


def _case():
    frame = EXAMPLE["make_case"]()
    branch_node = FrameNode(id="branch-tip", position_m=(0.0, 4.0, 0.0))
    branch = frame.members[0].model_copy(
        update={"id": "branch", "j_node": "branch-tip"}
    )
    frame = frame.model_copy(
        update={
            "nodes": frame.nodes + (branch_node,),
            "members": frame.members + (branch,),
            "load_cases": ("point", "junction"),
            "combinations": (
                FrameCombination(
                    id="junction-service", factors={"junction": 1.0}, evidence_id="reference"
                ),
            ),
        }
    )
    identities = {
        "reference",
        "load-basis",
        "ambient",
        "inventory",
        "port-x",
        "port-y",
    }
    review_evidence = {name: f"review-{index}" for index, name in enumerate(REVIEWS)}
    identities.update(review_evidence.values())
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
                for identity in sorted(identities)
            )
        }
    )
    request = JunctionLoadRequest(
        id="synthetic-right-angle-junction",
        frame=frame,
        junction_node_id="anchor",
        case_id="junction",
        ports=(
            JunctionPort(
                id="east-port",
                member_id="pipe",
                physical_port_id="east",
                clock_id=frame.clock_id,
                time_s=frame.time_s,
                fluid_flow_area_m2=0.02,
                static_pressure_pa_abs=200_000.0,
                density_kg_m3=1000.0,
                mass_flow_out_of_control_volume_kg_s=2.0,
                evidence_id="port-x",
            ),
            JunctionPort(
                id="north-port",
                member_id="branch",
                physical_port_id="north",
                clock_id=frame.clock_id,
                time_s=frame.time_s,
                fluid_flow_area_m2=0.01,
                static_pressure_pa_abs=200_000.0,
                density_kg_m3=1000.0,
                mass_flow_out_of_control_volume_kg_s=-2.0,
                evidence_id="port-y",
            ),
        ),
        exterior_ambient_pressure_pa_abs=100_000.0,
        fluid_mass_in_control_volume_kg=0.0,
        gravity_vector_m_s2=(0.0, 0.0, 0.0),
        fluid_inventory_and_gravity_evidence_id="inventory",
        ambient_pressure_evidence_id="ambient",
        reviews={name: True for name in REVIEWS},
        review_evidence_ids=review_evidence,
        evidence_ids=("load-basis", "ambient", "inventory"),
        solve_prepared_frame=False,
    )
    return request, context


def test_pressure_momentum_resultant_matches_independent_control_volume_oracle():
    request, context = _case()
    record = evaluate_junction_loads(request, context)

    # Outward normals are +x and +y. For each port, the independent resultant
    # is (p_static - p_ambient)*A + mdot**2/(rho*A); reaction on the wall points
    # opposite each outward normal.
    assert record["status"] == "ready"
    assert record["mass_balance_residual_kg_s"] == pytest.approx(0.0, abs=1e-14)
    assert record["fluid_on_wall_force_vector_N"] == pytest.approx(
        [-2000.2, -1000.4, 0.0], abs=1e-10
    )
    terms = {term["physical_port_id"]: term for term in record["port_force_terms"]}
    assert terms["east"]["gauge_pressure_force_magnitude_N"] == pytest.approx(2000.0)
    assert terms["east"]["momentum_flux_magnitude_N"] == pytest.approx(0.2)
    assert terms["north"]["gauge_pressure_force_magnitude_N"] == pytest.approx(1000.0)
    assert terms["north"]["momentum_flux_magnitude_N"] == pytest.approx(0.4)
    assert record["structural_solve_executed"] is False
    loads = record["prepared_frame"]["node_loads"]
    mapped = {load["direction"]: load["value"] for load in loads if load["case_id"] == "junction"}
    assert mapped["FX"] == pytest.approx(-2000.2, abs=1e-10)
    assert mapped["FY"] == pytest.approx(-1000.4, abs=1e-10)
    assert mapped["FZ"] == pytest.approx(0.0, abs=1e-14)
