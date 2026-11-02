"""Independent half-space uptake and analytically constructed trap equilibrium.

Uses the actual material owner through the public transport adapter. Authored
parameters test mathematical transport laws, not hydrogen damage thresholds,
gas-to-solid conversion, measured diffusivity or material qualification.
"""
from copy import deepcopy
import importlib.util
import math
from pathlib import Path

import pytest

from pipeline_integrity.hydrogen_transport import EQUILIBRIUM, SEMI, evaluate_hydrogen_transport

_spec = importlib.util.spec_from_file_location('hydrogen_reference_example',
    Path(__file__).resolve().parents[1] / 'examples' / 'hydrogen_transport.py')
_example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_example)


@pytest.mark.parametrize('surface', [110.0, 0.0])
def test_half_space_uptake_equals_spatial_inventory_and_time_integrated_surface_flux(surface):
    requests, context = _example.make_cases()
    base = requests[SEMI]
    diffusivity, duration, initial = 1e-8, 4.0, 10.0
    scale = math.sqrt(diffusivity * duration)
    count = 1024
    # Integrate on a fixed nondimensional domain; the omitted erfc tail beyond
    # depth/(2 sqrt(Dt))=8 is negligible compared with the declared tolerance.
    spacing = 16.0 * scale / count
    depths = [i * spacing for i in range(count + 1)]
    parameters = dict(diffusivity_m2_s=diffusivity, exposure_s=duration, depth_m=depths,
        initial_concentration_mol_m3=initial, surface_concentration_mol_m3=surface)
    request = base.model_copy(update={'parameters': parameters,
        'depth_point_ids': tuple(f'depth-{i}' for i in range(count + 1))})
    record = evaluate_hydrogen_transport(request, context)
    assert record['status'] == 'computed', record['issues']
    values = record['values']
    excess = [value - initial for value in values['concentration_mol_m3']]
    inventory = spacing / 3.0 * (excess[0] + excess[-1]
        + 4.0 * math.fsum(excess[1:-1:2]) + 2.0 * math.fsum(excess[2:-1:2]))
    # Substitute t=u² to remove the t=0 flux singularity. q(u²)*2u
    # is constant, yielding an independently integrated boundary inventory.
    boundary_inventory = 2.0 * (surface - initial) * math.sqrt(diffusivity / math.pi) * math.sqrt(duration)
    assert inventory == pytest.approx(boundary_inventory, rel=1e-7, abs=1e-12)
    assert values['integrated_excess_mol_m2'] == pytest.approx(inventory, rel=1e-7, abs=1e-12)
    assert values['surface_flux_mol_m2_s'] * (2.0 * duration) == pytest.approx(inventory, rel=1e-7, abs=1e-12)
    assert values['concentration_mol_m3'][0] == surface
    assert values['concentration_mol_m3'][-1] == pytest.approx(initial, abs=1e-10)
    if surface > initial:
        assert all(a >= b for a, b in zip(excess, excess[1:]))
    else:
        assert all(a <= b for a, b in zip(excess, excess[1:]))


@pytest.mark.parametrize('temperature', [295.0, 305.0])
@pytest.mark.parametrize('binding_ratio', [1.0, 9.0, 1000.0])
def test_trap_partition_recovers_constructed_chemical_equilibrium_and_differential_storage(
    temperature, binding_ratio,
):
    requests, context = _example.make_cases()
    base = requests[EQUILIBRIUM]
    parameters = deepcopy(base.parameters)
    theta = 0.02
    lattice_sites, trap_sites = 1000.0, 100.0
    trapped_fraction = binding_ratio * theta / (1.0 + (binding_ratio - 1.0) * theta)
    total = lattice_sites * theta + trap_sites * trapped_fraction
    # The model's gas constant is SI molar R. Construct total inventory from
    # prescribed occupancy, then ask its mass-root solver to recover occupancy.
    gas_constant = 8.31446261815324
    parameters.update(total_hydrogen_atoms_mol_m3=total, temperature_K=temperature)
    parameters['traps'][0].update(binding_energy_J_mol=gas_constant * temperature * math.log(binding_ratio),
        maximum_supported_occupancy=1.0)
    request = base.model_copy(update={'parameters': parameters, 'declared_temperature_K': temperature})
    record = evaluate_hydrogen_transport(request, context)
    assert record['status'] == 'computed', record['issues']
    values = record['values']
    assert values['lattice_occupancy'] == pytest.approx(theta, rel=1e-9)
    assert values['traps'][0]['occupancy'] == pytest.approx(trapped_fraction, rel=1e-9)
    assert values['lattice_hydrogen_atoms_mol_m3'] + values['traps'][0]['hydrogen_atoms_mol_m3'] == pytest.approx(total, abs=1e-8)
    retardation = 1.0 + (trap_sites / lattice_sites) * binding_ratio / (1.0 + (binding_ratio - 1.0) * theta)**2
    assert values['differential_retardation_factor'] == pytest.approx(retardation, rel=1e-9)
    assert values['local_differential_diffusivity_m2_s'] == pytest.approx(1e-8 / retardation, rel=1e-9)
    assert abs(values['mass_balance_residual_mol_m3']) < 1e-8
