"""Two unequal thermal volumes: independent exponential decay and energy balance."""
import math

import pytest

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.phase_material import PhaseMaterialRequest
from pipeline_integrity.spatial_phase_change import (
    PhaseConductionFace, PhaseVolumeCell, SpatialPhaseRequest, SpatialPhaseWindow,
    evaluate_spatial_phase_change,
)


def _material():
    return PhaseMaterialRequest(
        id='phase', material_id='solid', temperature_support_K=(250.0, 300.0),
        solidus_temperature_K=270.0, liquidus_temperature_K=280.0,
        solid_volumetric_heat_capacity_J_m3_K=1000.0,
        liquid_volumetric_heat_capacity_J_m3_K=1000.0,
        volumetric_latent_heat_J_m3=100000.0,
        solid_conductivity_W_m_K=1.0, liquid_conductivity_W_m_K=1.0,
        maximum_enthalpy_table_error_J_m3=100.0,
        maximum_conductivity_interpolation_error_W_m_K=0.01,
        evidence_id='reference', apparent_capacity_and_transition_interval_reviewed=True)


@pytest.mark.parametrize('reverse_face', [False, True])
def test_unequal_volume_exchange_conserves_energy_and_converges_to_exponential(reverse_face):
    material = _material()
    context = RunContext(asset_id='pair', scenario_id='exchange', clock_id='seconds',
        origin='simulation', evidence=(EvidenceRef(id='reference', origin='synthetic',
        citation='Authored constant-capacity two-volume exponential reference'),))
    equilibrium = (1000.0 * 255.0 + 2000.0 * 265.0) / 3000.0
    rate = 10.0 * (1 / 1000.0 + 1 / 2000.0)
    exact_difference = -10.0 * math.exp(-rate * 20.0)
    errors = []
    for dt in (2.0, 1.0, 0.5):
        request = SpatialPhaseRequest(id='pair', asset_id='pair', clock_id='seconds',
            materials=(material,), cells=(
                PhaseVolumeCell(id='a', material_id='solid', volume_m3=1.0,
                    initial_temperature_K=255.0, evidence_id='reference'),
                PhaseVolumeCell(id='b', material_id='solid', volume_m3=2.0,
                    initial_temperature_K=265.0, evidence_id='reference')),
            faces=(PhaseConductionFace(id='face',
                left_cell_id='b' if reverse_face else 'a',
                right_cell_id='a' if reverse_face else 'b',
                left_half_length_over_area_m_inv=0.05,
                right_half_length_over_area_m_inv=0.05, evidence_id='reference'),),
            windows=(SpatialPhaseWindow(id='exchange', duration_s=20.0,
                maximum_step_s=dt, cell_heat_W=(0.0, 0.0), evidence_id='reference'),),
            absolute_cell_energy_tolerance_J=1e-5, relative_cell_energy_tolerance=1e-10,
            orthogonal_face_paths_and_fixed_volumes_reviewed=True,
            conduction_only_no_advection_or_volume_change_reviewed=True,
            boundary_conductances_and_heat_sources_reviewed=True)
        record = evaluate_spatial_phase_change(request, context)
        assert record['status'] == 'computed_diagnostic', record['failure']
        for row in record['states']:
            a, b = row['temperature_K']
            assert 255.0 <= a <= equilibrium + 1e-8
            assert equilibrium - 1e-8 <= b <= 265.0
            assert 1000 * a + 2000 * b == pytest.approx(785000.0, abs=0.001)
            assert row['liquid_fraction'] == [0.0, 0.0]
        a, b = record['states'][-1]['temperature_K']
        errors.append(abs((a - b) - exact_difference))
        assert abs(record['energy_residual_J']) < 0.001
        assert record['source_heat_J'] == record['boundary_heat_J'] == 0.0
    assert errors[0] > errors[1] > errors[2] > 0
    for coarse, fine in zip(errors, errors[1:]):
        assert 1.8 < coarse / fine < 2.2


@pytest.mark.parametrize('count', [1, 2, 8])
@pytest.mark.parametrize('freezing', [False, True])
def test_uniform_latent_heating_preserves_exact_enthalpy_under_spatial_partition(count, freezing):
    """Uniform continuum heating has zero conductive flux on every internal face."""
    context = RunContext(asset_id='pair', scenario_id='uniform_heat', clock_id='seconds',
        origin='simulation', evidence=(EvidenceRef(id='reference', origin='synthetic',
        citation='Authored uniform volumetric heat and exact enthalpy balance'),))
    initial_temperature = 277.0 if freezing else 260.0
    initial_enthalpy = 85400.0 if freezing else -10000.0
    heating_power = -4770.0 if freezing else 4770.0
    final_temperature = 260.0 if freezing else 277.0
    request = SpatialPhaseRequest(id='partition', asset_id='pair', clock_id='seconds',
        materials=(_material(),), cells=tuple(PhaseVolumeCell(id=f'c{i}', material_id='solid',
            volume_m3=1.0 / count, initial_temperature_K=initial_temperature, evidence_id='reference')
            for i in range(count)),
        faces=tuple(PhaseConductionFace(id=f'f{i}', left_cell_id=f'c{i}', right_cell_id=f'c{i+1}',
            left_half_length_over_area_m_inv=0.5 / count,
            right_half_length_over_area_m_inv=0.5 / count, evidence_id='reference')
            for i in range(count - 1)),
        windows=(SpatialPhaseWindow(id='heat', duration_s=20.0, maximum_step_s=1.0,
            cell_heat_W=tuple(heating_power / count for _ in range(count)), evidence_id='reference'),),
        absolute_cell_energy_tolerance_J=1e-5, relative_cell_energy_tolerance=1e-10,
        orthogonal_face_paths_and_fixed_volumes_reviewed=True,
        conduction_only_no_advection_or_volume_change_reviewed=True,
        boundary_conductances_and_heat_sources_reviewed=True)
    record = evaluate_spatial_phase_change(request, context)
    assert record['status'] == 'computed_diagnostic', record['failure']
    for row in record['states']:
        expected = initial_enthalpy + heating_power * row['time_s']
        assert row['total_enthalpy_J'] == pytest.approx(expected, abs=0.002)
        for temperature in row['temperature_K']:
            x = min(1.0, max(0.0, (temperature - 270.0) / 10.0))
            enthalpy = 1000.0 * (temperature - 270.0) + 100000.0 * (3*x*x - 2*x*x*x)
            assert enthalpy == pytest.approx(expected, abs=0.002)
    assert record['states'][-1]['temperature_K'] == pytest.approx([final_temperature] * count, abs=1e-6)
    assert abs(record['energy_residual_J']) < 0.002
    for step in record['steps']:
        assert step['face_heat_into_left_J'] == pytest.approx([0.0] * (count - 1), abs=1e-6)
