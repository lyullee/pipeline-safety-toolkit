"""Independent closed-form coupling of pipe buoyancy, bending and a soil spring."""

import pytest

from examples.soil_pipe import make_case
from pipeline_integrity.soil_pipe import (
    SoilPipeRequest,
    evaluate_soil_pipe,
    soil_pipe_boundary_metadata,
    verify_soil_pipe_record,
)

G = 9.80665
CASES = (
    # Positive effective mass: net pipe weight acts downwards.
    (8.0, 2.0, 0.007, 0.75, 1000.0),
    # Buoyancy exceeds pipe plus contents mass: load reverses upwards.
    (3.0, 0.5, 0.007, 0.75, 1000.0),
)


@pytest.mark.parametrize(
    "pipe_mass,contents_mass,displaced_area,immersed_fraction,external_density", CASES
)
def test_buoyancy_adjusted_uniform_load_and_tip_spring_match_closed_form(
    pipe_mass, contents_mass, displaced_area, immersed_fraction, external_density
):
    request, context = make_case(reviewed=True, ground_m=0.0)
    body = request.body_states[0].model_copy(
        update={
            "pipe_and_attached_mass_kg_m": pipe_mass,
            "contents_mass_kg_m": contents_mass,
            "displaced_area_m2": displaced_area,
            "immersed_fraction": immersed_fraction,
            "external_density_kg_m3": external_density,
        }
    )
    request = SoilPipeRequest.model_validate(
        request.model_copy(update={"body_states": (body,)}).model_dump(mode="python")
    )
    context = context.model_copy(
        update={"metadata": {"soil_pipe_boundary": soil_pipe_boundary_metadata(request)}}
    )

    record = evaluate_soil_pipe(request, context)
    assert record["status"] == "computed_diagnostic"

    effective_mass = (
        pipe_mass + contents_mass - immersed_fraction * external_density * displaced_area
    )
    line_load_y = -G * effective_mass
    length = 2.0
    section = request.frame.sections[0]
    material = request.frame.materials[0]
    ei = material.young_modulus_pa * section.iz_m4
    spring_stiffness = 100_000.0
    cantilever_tip_compliance = length**3 / (3.0 * ei)
    free_tip_deflection = line_load_y * length**4 / (8.0 * ei)
    expected_tip_displacement = free_tip_deflection / (
        1.0 + spring_stiffness * cantilever_tip_compliance
    )
    response = record["solution"]["soil_response"][0]["axes"][0]
    expected_spring_force = -spring_stiffness * expected_tip_displacement

    assert record["body_load_ledger"][0]["effective_mass_kg_m"] == pytest.approx(
        effective_mass, rel=1e-13, abs=1e-13
    )
    assert record["body_load_ledger"][0]["body_force_N_m"][1] == pytest.approx(
        line_load_y, rel=1e-13, abs=1e-13
    )
    assert response["pipe_displacement_m"] == pytest.approx(
        expected_tip_displacement, rel=2e-7, abs=2e-10
    )
    assert response["soil_force_on_pipe_N"] == pytest.approx(
        expected_spring_force, rel=2e-7, abs=2e-8
    )

    anchor_vertical_reaction = record["solution"]["physical_reactions_by_node"]["anchor"][1]
    assert anchor_vertical_reaction + expected_spring_force + line_load_y * length == pytest.approx(
        0.0, abs=2e-5
    )
    assert record["solution"]["physical_static_balance"]["passed"]
    assert record["solution"]["physical_nodal_balance"]["passed"]
    assert verify_soil_pipe_record(record, request=request, context=context)
