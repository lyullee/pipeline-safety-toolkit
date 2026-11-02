"""Bounded closed-form qualification for buoyancy-loaded pipe/support response."""
import pytest
from examples.soil_pipe import make_case
from pipeline_integrity.soil_pipe import SoilPipeRequest, evaluate_soil_pipe, soil_pipe_boundary_metadata, verify_soil_pipe_record

G = 9.80665
CASES = (
    pytest.param(8.0, 2.0, 0.007, 0.75, 1000.0, id="net-downward"),
    pytest.param(3.0, 0.5, 0.007, 0.75, 1000.0, id="buoyancy-reverses-load"),
    pytest.param(6.0, 1.0, 0.010, 0.70, 1000.0, id="neutral-buoyancy"),
)

@pytest.mark.parametrize("pipe_mass,contents_mass,displaced_area,immersed_fraction,external_density", CASES)
def test_buoyancy_and_linear_support_match_independent_cantilever_solution(
    pipe_mass, contents_mass, displaced_area, immersed_fraction, external_density
):
    request, context = make_case(reviewed=True, ground_m=0.0)
    body = request.body_states[0].model_copy(update={
        "pipe_and_attached_mass_kg_m": pipe_mass,
        "contents_mass_kg_m": contents_mass,
        "displaced_area_m2": displaced_area,
        "immersed_fraction": immersed_fraction,
        "external_density_kg_m3": external_density,
    })
    request = SoilPipeRequest.model_validate(request.model_copy(update={"body_states": (body,)}).model_dump(mode="python"))
    context = context.model_copy(update={"metadata": {"soil_pipe_boundary": soil_pipe_boundary_metadata(request)}})
    result = evaluate_soil_pipe(request, context)
    assert result["status"] == "computed_diagnostic"

    # Independent statics and Euler-Bernoulli cantilever with a tip spring.
    effective_mass = pipe_mass + contents_mass - immersed_fraction * external_density * displaced_area
    q = -G * effective_mass
    length = 2.0
    section = request.frame.sections[0]
    material = request.frame.materials[0]
    ei = material.young_modulus_pa * section.iz_m4
    k = 100_000.0
    compliance = length**3 / (3.0 * ei)
    free_tip = q * length**4 / (8.0 * ei)
    expected_tip = free_tip / (1.0 + k * compliance)
    expected_spring = -k * expected_tip
    actual = result["solution"]["soil_response"][0]["axes"][0]
    assert result["body_load_ledger"][0]["effective_mass_kg_m"] == pytest.approx(effective_mass, rel=1e-13, abs=1e-13)
    assert result["body_load_ledger"][0]["body_force_N_m"][1] == pytest.approx(q, rel=1e-13, abs=1e-13)
    assert actual["pipe_displacement_m"] == pytest.approx(expected_tip, rel=2e-7, abs=2e-10)
    assert actual["soil_force_on_pipe_N"] == pytest.approx(expected_spring, rel=2e-7, abs=2e-8)
    anchor_reaction = result["solution"]["physical_reactions_by_node"]["anchor"][1]
    assert anchor_reaction + expected_spring + q * length == pytest.approx(0.0, abs=2e-5)
    assert result["solution"]["physical_static_balance"]["passed"]
    assert result["solution"]["physical_nodal_balance"]["passed"]
    assert verify_soil_pipe_record(result, request=request, context=context)
