"""Closed-slab uniform-state and atom-inventory invariants for kinetic transport."""

import runpy

import pytest

from pipeline_integrity.hydrogen_transport import KINETIC, evaluate_hydrogen_transport


REQUESTS, CONTEXT = runpy.run_path("examples/hydrogen_transport.py")["make_cases"]()
BASE = REQUESTS[KINETIC]


def evaluate(*, concentration=100.0, diffusivity=1e-8, times=(0.0, 1.0, 10.0),
             temperatures=None, reviews=None, **request_updates):
    if temperatures is None:
        temperatures = (300.0,) * len(times)
    parameters = dict(BASE.parameters)
    parameters.update(
        traps=[],
        initial_lattice_hydrogen_atoms_mol_m3=[concentration] * parameters["cell_count"],
        elapsed_time_s=list(times),
        uniform_temperature_K=list(temperatures),
        lattice_diffusivity_m2_s=[diffusivity] * len(times),
    )
    request = BASE.model_copy(
        update={
            "parameters": parameters,
            "time_knot_ids": tuple(f"time-{i}" for i in range(len(times))),
            **request_updates,
        }
    )
    if reviews is not None:
        request = request.model_copy(update={"applicability_reviews": reviews})
    return evaluate_hydrogen_transport(request, CONTEXT)


@pytest.mark.parametrize(
    "concentration,diffusivity,times",
    [
        (100.0, 1e-8, (0.0, 1.0, 10.0)),
        (25.0, 2e-7, (0.0, 0.1, 0.5, 2.0)),
        (450.0, 5e-10, (0.0, 5.0, 20.0)),
    ],
)
def test_closed_slab_uniform_lattice_state_is_an_exact_invariant(concentration, diffusivity, times):
    result = evaluate(concentration=concentration, diffusivity=diffusivity, times=times)
    assert result["status"] == "computed"
    expected_inventory = concentration * 0.001
    assert result["values"]["final_lattice_hydrogen_atoms_mol_m3"] == pytest.approx(
        [concentration] * 8, rel=2e-10, abs=1e-10
    )
    assert result["values"]["lattice_mean_hydrogen_atoms_mol_m3"] == pytest.approx(
        [concentration] * len(times), rel=2e-10, abs=1e-10
    )
    assert result["values"]["hydrogen_inventory_mol_m2"] == pytest.approx(
        [expected_inventory] * len(times), rel=2e-10, abs=1e-12
    )
    assert result["values"]["left_inward_flux_mol_m2_s"] == pytest.approx([0.0] * len(times), abs=1e-15)
    assert result["values"]["right_inward_flux_mol_m2_s"] == pytest.approx([0.0] * len(times), abs=1e-15)
    assert result["values"]["relative_mass_balance_error"] <= 1e-12
    assert result["native_receipt"]["native"]["payload"]["model_id"] == KINETIC


def test_closed_slab_has_no_cumulative_boundary_exchange():
    result = evaluate(concentration=160.0)
    values = result["values"]
    assert values["cumulative_left_exchange_mol_m2"] == pytest.approx([0.0, 0.0, 0.0], abs=1e-14)
    assert values["cumulative_right_exchange_mol_m2"] == pytest.approx([0.0, 0.0, 0.0], abs=1e-14)
    assert values["mass_balance_residual_mol_m2"] == pytest.approx([0.0, 0.0, 0.0], abs=1e-14)


def test_missing_geometry_review_withholds_native_computation():
    reviews = dict(BASE.applicability_reviews)
    reviews["planar_cell_averages_and_elapsed_time_origin"] = False
    result = evaluate(reviews=reviews)
    assert result["status"] == "needs_data"
    assert result["native_receipt"] is None
    assert result["values"] == {}


def test_temperature_outside_declared_support_is_withheld():
    result = evaluate(
        temperatures=(320.0, 320.0, 320.0),
        declared_temperature_K=320.0,
    )
    assert result["status"] == "out_of_scope"
    assert result["native_receipt"] is None
    assert result["values"] == {}
