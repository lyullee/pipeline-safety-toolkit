"""Native cantilever contact and section-stress references for Nurion.

The beam, gap and stress-transfer values are authored elastic examples. No
standard stress factors or acceptance limits are inferred from these cases.
"""

import pytest

from pipeline_integrity.component_stress import (
    ComponentStressRequest,
    ComponentStressTransfer,
    evaluate_component_stress,
)
from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.gap_contact import GapContact, GapContactRequest, evaluate_gap_contact_frame
from pipeline_integrity.structural import (
    FrameCombination,
    FrameMaterial,
    FrameMember,
    FrameNode,
    FrameNodeLoad,
    FrameRequest,
    FrameSection,
    FrameSupport,
)


LENGTH = 2.0
YOUNG = 2e11
INERTIA = 1e-5
AREA = 0.01
BEAM_STIFFNESS = 3 * YOUNG * INERTIA / LENGTH**3
GAP = 0.001
CONTACT_STIFFNESS = 1.5e6


def _context():
    return RunContext(
        asset_id="cantilever", scenario_id="elastic_reference", clock_id="snapshot", origin="synthetic",
        evidence=(EvidenceRef(id="review", citation="Authored beam/contact and section-equilibrium reference",
                              origin="synthetic", sha256="f" * 64),),
    )


def _frame(loads, combinations, cases):
    return FrameRequest(
        asset_id="cantilever", clock_id="snapshot", time_s=0.0,
        nodes=(FrameNode(id="root", position_m=(0.0, 0.0, 0.0)),
               FrameNode(id="tip", position_m=(LENGTH, 0.0, 0.0))),
        members=(FrameMember(id="beam", i_node="root", j_node="tip", section_id="section",
                             material_id="elastic", sample_fractions=(0.0, 0.25, 0.5, 1.0)),),
        sections=(FrameSection(id="section", area_m2=AREA, iy_m4=INERTIA, iz_m4=INERTIA,
                               torsion_constant_m4=2 * INERTIA, evidence_id="review"),),
        materials=(FrameMaterial(id="elastic", young_modulus_pa=YOUNG, poisson_ratio=0.3,
                                 evidence_id="review"),),
        supports=(FrameSupport(node_id="root", fixed_dofs=("DX", "DY", "DZ", "RX", "RY", "RZ"),
                               evidence_id="review"),),
        load_cases=cases, node_loads=loads, combinations=combinations,
        geometry_evidence_id="review", geometry_and_connectivity_reviewed=True,
        stiffness_and_loads_reviewed=True, supports_reviewed=True,
        linear_small_displacement_applicability_reviewed=True,
    )


def _gap_request(sign, initialization):
    frame = _frame(
        (FrameNodeLoad(node_id="tip", direction="FY", value=sign * 1000.0,
                       case_id="point", evidence_id="review"),),
        tuple(FrameCombination(id=name, factors={"point": factor}, evidence_id="review")
              for name, factor in (("open", 0.5), ("closed", 1.5), ("reverse", -0.5))),
        ("point",),
    )
    return GapContactRequest(
        id="contact", frame=frame, initialization=initialization,
        contacts=(GapContact(
            id="stop", node_id="tip", dof="DY", gap_m=GAP, stiffness_n_m=CONTACT_STIFFNESS,
            closing_direction="positive_displacement" if sign == 1 else "negative_displacement",
            evidence_id="review", elastic_frictionless_small_displacement_contact_reviewed=True,
        ),),
    )


@pytest.mark.parametrize("sign", [-1, 1])
@pytest.mark.parametrize("initialization", ["all_active", "explicit"])
def test_native_cantilever_finite_gap_contact_matches_piecewise_static_solution(sign, initialization):
    request = _gap_request(sign, initialization)
    record = evaluate_gap_contact_frame(request, _context())
    assert record["status"] == "computed", record["combinations"]
    for name, closing_load in (("open", 500.0), ("closed", 1500.0), ("reverse", -500.0)):
        # With d measured in the closing direction:
        # Kb*d + kc*max(d-gap,0) = closing_load.
        closed = closing_load > BEAM_STIFFNESS * GAP
        displacement = ((closing_load + CONTACT_STIFFNESS * GAP) / (BEAM_STIFFNESS + CONTACT_STIFFNESS)
                        if closed else closing_load / BEAM_STIFFNESS)
        support_force = -sign * CONTACT_STIFFNESS * max(displacement - GAP, 0.0)
        physical = record["combinations"][name]["physical_result"]
        tip = physical["nodes"]["tip"]
        root = physical["nodes"]["root"]
        assert tip["translation_m"][1] == pytest.approx(sign * displacement, abs=1e-11)
        assert tip["reaction_force_n"][1] == pytest.approx(support_force, abs=1e-5)
        assert root["reaction_force_n"][1] == pytest.approx(-sign * closing_load - support_force, abs=1e-5)
        assert root["reaction_moment_nm"][2] == pytest.approx(
            -(sign * closing_load + support_force) * LENGTH, abs=1e-5,
        )
        state = physical["contact_states"][0]
        assert state["active"] is closed
        assert state["signed_closure_m"] == pytest.approx(displacement - GAP, abs=1e-11)
        assert state["physical_contact_force_n"] == pytest.approx(support_force, abs=1e-5)
        assert state["stored_contact_energy_J"] == pytest.approx(
            0.5 * CONTACT_STIFFNESS * max(displacement - GAP, 0.0)**2, abs=1e-10,
        )
        assert physical["static_balance"]["passed"] and physical["nodal_balance"]["passed"]
        assert state["contact_law_passed"] and state["active_branch_gap_condition_passed"]
    # The two initial guesses deliberately require opposite active-set switches.
    changed_case = "open" if initialization == "all_active" else "closed"
    assert record["combinations"][changed_case]["iterations"] >= 2
    assert record["numerical_stabilization_applied"] is False
    assert record["affine_preloads_are_physical_external_loads"] is False


def _component_request():
    frame = _frame(
        (FrameNodeLoad(node_id="tip", direction="FX", value=2000.0, case_id="axial", evidence_id="review"),
         FrameNodeLoad(node_id="tip", direction="FY", value=1000.0, case_id="bend", evidence_id="review")),
        tuple(FrameCombination(id=name, factors=factors, evidence_id="review") for name, factors in (
            ("axial", {"axial": 1.0}), ("bend", {"bend": 1.0}),
            ("combined", {"axial": 1.0, "bend": 1.0}), ("double", {"axial": 2.0, "bend": 2.0}),
        )),
        ("axial", "bend"),
    )
    transfers = []
    for name, coordinate_y in (("upper", 0.05), ("lower", -0.05)):
        # epsilon_x = N/(EA) - y*Mz/(EI). Residual pressure stresses
        # are explicit independent caller coefficients for this reference.
        transfers.append(ComponentStressTransfer(
            id=name, member_id="beam", stress_location_id=name, stress_coordinate_frame_id="member_local",
            sample_fraction=0.25,
            stress_per_force_Pa_N=((1 / AREA, 0.0, 0.0),) + ((0.0, 0.0, 0.0),) * 5,
            stress_per_moment_Pa_N_m=((0.0, 0.0, -coordinate_y / INERTIA),) + ((0.0, 0.0, 0.0),) * 5,
            stress_per_differential_pressure=(0.0, 2.0, 1.0, 0.0, 0.0, 0.0),
            differential_pressure_Pa_by_combination={"axial": 20000.0, "bend": 30000.0,
                                                    "combined": 50000.0, "double": 100000.0},
            evidence_id="review", physical_resultant_signs_and_local_axes_reviewed=True,
            pressure_term_excludes_resultant_effects_reviewed=True,
            linear_elastic_component_transfer_reviewed=True,
        ))
    return ComponentStressRequest(id="section_stress", frame=frame, transfers=tuple(transfers),
                                  maximum_absolute_tensor_component_Pa=1e8)


def test_component_stress_native_resultants_match_section_equilibrium_and_axial_bending_reference():
    request = _component_request()
    record = evaluate_component_stress(request, _context())
    assert record["status"] == "computed", record["frame_result"].get("issues")
    factors = {combo.id: combo.factors for combo in request.frame.combinations}
    pressure = request.transfers[0].differential_pressure_Pa_by_combination
    for row in record["stress_locations"]:
        combo = row["combination_id"]
        axial = 2000 * factors[combo].get("axial", 0)
        transverse = 1000 * factors[combo].get("bend", 0)
        moment = transverse * LENGTH * (1 - 0.25)
        coordinate_y = 0.05 if row["transfer_id"] == "upper" else -0.05
        assert row["source_force_local_N"] == pytest.approx([axial, transverse, 0.0], abs=1e-6)
        assert row["source_moment_local_N_m"] == pytest.approx([0.0, 0.0, moment], abs=1e-6)
        stress = row["stress_tensor_components_Pa"]
        assert stress["xx"] == pytest.approx(axial / AREA - coordinate_y * moment / INERTIA, abs=0.01)
        assert stress["yy"] == pytest.approx(2 * pressure[combo], abs=1e-8)
        assert stress["zz"] == pytest.approx(pressure[combo], abs=1e-8)
        assert [stress[key] for key in ("xy", "yz", "zx")] == [0.0, 0.0, 0.0]
        assert row["force_contribution_Pa"]["xx"] == pytest.approx(axial / AREA, abs=0.01)
        assert row["moment_contribution_Pa"]["xx"] == pytest.approx(-coordinate_y * moment / INERTIA, abs=0.01)
        assert row["residual_pressure_contribution_Pa"]["xx"] == 0.0
        assert row["source_resultant_face"] == "right_face_of_explicit_left_member_segment"


def test_component_stress_combination_superposition_keeps_all_six_tensor_components():
    record = evaluate_component_stress(_component_request(), _context())
    assert record["status"] == "computed"
    by_key = {(row["combination_id"], row["transfer_id"]): row["stress_tensor_components_Pa"]
              for row in record["stress_locations"]}
    for location in ("upper", "lower"):
        for axis in ("xx", "yy", "zz", "xy", "yz", "zx"):
            assert by_key[("combined", location)][axis] == pytest.approx(
                by_key[("axial", location)][axis] + by_key[("bend", location)][axis], abs=0.01,
            )
            assert by_key[("double", location)][axis] == pytest.approx(
                2 * by_key[("combined", location)][axis], abs=0.01,
            )


def test_component_stress_requires_an_exact_existing_sample_and_reviewed_pressure_partition():
    request = _component_request()
    transfer = request.transfers[0]
    with pytest.raises(ValueError, match="exact declared frame sample"):
        evaluate_component_stress(request.model_copy(update={"transfers": (
            transfer.model_copy(update={"sample_fraction": 0.3}),
        )}), _context())
    with pytest.raises(ValueError, match="pressure partition"):
        evaluate_component_stress(request.model_copy(update={"transfers": (
            transfer.model_copy(update={"pressure_term_excludes_resultant_effects_reviewed": False}),
        )}), _context())
