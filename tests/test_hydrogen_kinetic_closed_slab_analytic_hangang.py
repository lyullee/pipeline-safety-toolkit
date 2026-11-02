"""Independent closed-slab kinetics oracle for hydrogen trap capture/release."""

from copy import deepcopy
import importlib.util
import math
from pathlib import Path

import pytest

from pipeline_integrity.hydrogen_transport import KINETIC, evaluate_hydrogen_transport, verify_hydrogen_transport_record

_spec = importlib.util.spec_from_file_location(
    "hydrogen_kinetic_example", Path(__file__).resolve().parents[1] / "examples" / "hydrogen_transport.py"
)
_example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_example)


LATTICE_SITES_MOL_M3 = 1000.0
TIMES_S = (0.0, 0.5, 2.0, 5.0)
RATE_PER_S = (0.02, 0.08, 0.03, 0.06)


def _exact_trap_occupancy(site_ratio, initial_occupancy):
    """Exact solution when capture and release frequencies are equal and boundaries are closed."""
    equilibrium = 1.0 / (1.0 + site_ratio)
    values = [initial_occupancy]
    current = initial_occupancy
    for index, (left, right) in enumerate(zip(TIMES_S[:-1], TIMES_S[1:], strict=True)):
        integrated_rate = 0.5 * (RATE_PER_S[index] + RATE_PER_S[index + 1]) * (right - left)
        current = equilibrium + (current - equilibrium) * math.exp(
            -(1.0 + site_ratio) * integrated_rate
        )
        values.append(current)
    return values


def _run(site_ratio, initial_trap_occupancy, maximum_step_s):
    requests, context = _example.make_cases()
    base = requests[KINETIC]
    parameters = deepcopy(base.parameters)
    cells = 8
    trap = parameters["traps"][0]
    trap["population"]["site_density_mol_m3"] = site_ratio * LATTICE_SITES_MOL_M3
    trap["population"]["maximum_supported_occupancy"] = 1.0
    trap["rate_model"] = "independent_frequencies"
    trap["capture_frequency_per_s"] = list(RATE_PER_S)
    trap["release_frequency_per_s"] = list(RATE_PER_S)
    trap["initial_occupancy"] = [initial_trap_occupancy] * cells
    trap["independent_rate_histories_reviewed"] = True
    parameters.update(
        cell_count=cells,
        lattice_site_density_mol_m3=LATTICE_SITES_MOL_M3,
        initial_lattice_hydrogen_atoms_mol_m3=[
            LATTICE_SITES_MOL_M3 * (1.0 - site_ratio * initial_trap_occupancy)
        ]
        * cells,
        elapsed_time_s=list(TIMES_S),
        uniform_temperature_K=[300.0] * len(TIMES_S),
        lattice_diffusivity_m2_s=[1e-8] * len(TIMES_S),
        maximum_supported_lattice_occupancy=1.0,
        maximum_supported_total_trap_to_lattice_site_ratio=0.5,
        detailed_balance_rate_ratio_reviewed=False,
        relative_tolerance=1e-5,
        absolute_occupancy_tolerance=1e-9,
        maximum_step_s=maximum_step_s,
        maximum_step_attempts=50000,
        maximum_relative_mass_balance_error=1e-8,
        store_profiles=True,
    )
    request = base.model_copy(
        update={
            "parameters": parameters,
            "cell_ids": tuple(f"kinetic-cell-{i}" for i in range(cells)),
            "time_knot_ids": tuple(f"kinetic-time-{i}" for i in range(len(TIMES_S))),
        }
    )
    record = evaluate_hydrogen_transport(request, context)
    assert record["status"] == "computed", record["issues"]
    verify_hydrogen_transport_record(record)
    return record["values"]


def _assert_against_closed_form(values, site_ratio, initial_trap_occupancy, *, atol):
    expected_trap = _exact_trap_occupancy(site_ratio, initial_trap_occupancy)
    trap_values = values["traps"][0]["mean_hydrogen_atoms_mol_m3"]
    lattice_values = values["lattice_mean_hydrogen_atoms_mol_m3"]
    for trap_density, lattice_density, theta_trap in zip(
        trap_values,
        lattice_values,
        expected_trap,
        strict=True,
    ):
        actual_trap = trap_density / (site_ratio * LATTICE_SITES_MOL_M3)
        actual_lattice = lattice_density / LATTICE_SITES_MOL_M3
        expected_lattice = 1.0 - site_ratio * theta_trap
        assert actual_trap == pytest.approx(theta_trap, abs=atol)
        assert actual_lattice == pytest.approx(expected_lattice, abs=atol)
        assert actual_lattice + site_ratio * actual_trap == pytest.approx(1.0, abs=atol)
    inventory = values["hydrogen_inventory_mol_m2"]
    assert inventory == pytest.approx([1.0] * len(TIMES_S), abs=2e-10)
    assert values["relative_mass_balance_error"] < 1e-8
    assert max(abs(v) for v in values["left_inward_flux_mol_m2_s"]) < 1e-14
    assert max(abs(v) for v in values["right_inward_flux_mol_m2_s"]) < 1e-14
    assert all(
        abs(v) < 1e-14
        for v in values["cumulative_left_exchange_mol_m2"] + values["cumulative_right_exchange_mol_m2"]
    )


@pytest.mark.parametrize(
    "site_ratio,initial_trap_occupancy",
    [(0.1, 0.05), (0.2, 0.25)],
)
def test_closed_slab_kinetic_trap_history_matches_exact_capture_release_solution(
    site_ratio, initial_trap_occupancy
):
    values = _run(site_ratio, initial_trap_occupancy, maximum_step_s=0.1)
    _assert_against_closed_form(values, site_ratio, initial_trap_occupancy, atol=3e-4)
    assert values["traps"][0]["final_occupancy"] == pytest.approx(
        [values["traps"][0]["final_occupancy"][0]] * 8, abs=1e-12
    )


def test_closed_slab_kinetic_solution_converges_under_maximum_step_refinement():
    site_ratio, initial_trap_occupancy = 0.1, 0.05
    coarse = _run(site_ratio, initial_trap_occupancy, maximum_step_s=0.2)
    fine = _run(site_ratio, initial_trap_occupancy, maximum_step_s=0.05)
    exact = _exact_trap_occupancy(site_ratio, initial_trap_occupancy)[-1]
    coarse_actual = coarse["traps"][0]["final_occupancy"][0]
    fine_actual = fine["traps"][0]["final_occupancy"][0]
    coarse_error, fine_error = abs(coarse_actual - exact), abs(fine_actual - exact)
    assert fine_error <= coarse_error + 2e-7
    assert fine_error < 3e-4
    _assert_against_closed_form(fine, site_ratio, initial_trap_occupancy, atol=3e-4)