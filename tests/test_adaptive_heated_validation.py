"""Independent constant-property heat balance across adaptive mesh levels.

Authored simulation mapping is source-pinned; no measured property validation.
The constant-property, heater-free continuum equation has an exponential solution.
"""
import importlib.util
import sys
import math
from pathlib import Path

import pytest

from pipeline_integrity import adaptive_heated_transport as adaptive
from pipeline_integrity import heated_transport as owner
from pipeline_integrity.contracts import EvidenceRef
from pipeline_integrity.datasets import file_digest

_spec = importlib.util.spec_from_file_location(
    'adaptive_heated_reference_fixture', Path(__file__).with_name('test_heated_transport.py'))
_fixtures = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _fixtures
_spec.loader.exec_module(_fixtures)


@pytest.mark.parametrize('ambient', [280.0, 320.0])
def test_adaptive_constant_liquid_matches_exponential_heat_balance_at_every_level(ambient):
    provider = _fixtures.SyntheticConstantLiquid()
    base, context = _fixtures._example.make_case(property_provider_kind='custom')
    evidence_id = base.evidence_ids[0]
    cell = base.cells[0].model_copy(update={
        'conductance_W_K': 200.0, 'ambient_temperature_K': ambient,
        'heater_into_fluid_W': 0.0})
    base = base.model_copy(update={'cells': (cell,),
        'reviews': dict.fromkeys(owner.REVIEWS, True),
        'review_evidence_ids': dict.fromkeys(owner.REVIEWS, evidence_id)})
    base = base.model_copy(update={
        'owner_pins': owner.native_backend_metadata(base, property_provider=provider)})
    context = context.model_copy(update={'origin': 'simulation', 'evidence': (EvidenceRef(
        id=evidence_id, origin='simulation', sha256=file_digest(Path(__file__)),
        citation='Authored uniform geometry and conductance mapping for constant-liquid reference',
        limitations=('Synthetic fluid properties; no measured or field applicability.',)),)})
    request = adaptive.AdaptiveHeatedTransportRequest(
        id='uniform_reference', base=base, required_consecutive_agreements=2,
        maximum_levels=4, outlet_pressure_absolute_tolerance_Pa=1e-5,
        outlet_pressure_relative_tolerance=0.0,
        outlet_temperature_absolute_tolerance_K=1e-8,
        outlet_temperature_relative_tolerance=0.0,
        total_heat_absolute_tolerance_W=1e-5, total_heat_relative_tolerance=0.0,
        refinement_mapping_evidence_id=evidence_id, evidence_ids=(evidence_id,),
        reviews=dict.fromkeys(adaptive.REVIEWS, True),
        review_evidence_ids=dict.fromkeys(adaptive.REVIEWS, evidence_id),
        refinement_owner_pins=adaptive.adaptive_heated_transport_metadata())
    context = adaptive.bind_adaptive_heated_transport_context(request, context)
    record = adaptive.evaluate_adaptive_heated_transport(request, context, property_provider=provider)
    assert record['status'] == 'computed_diagnostic', record['issues']
    assert record['consecutive_agreements'] == 2
    assert record['accepted_mesh_level'] == 2
    expected_temperature = ambient + (300.0 - ambient) * math.exp(-200.0 / 4000.0)
    expected_heat = 4000.0 * (expected_temperature - 300.0)
    assert [level['refined_cells'] for level in record['mesh_levels']] == [1, 2, 4]
    for level in record['mesh_levels']:
        values = level['native_record']['values']
        assert values['outlet']['temperature_k'] == pytest.approx(expected_temperature, abs=1e-8)
        assert values['total_heat_into_fluid_W'] == pytest.approx(expected_heat, abs=1e-5)
        ledger = level['original_cell_geometry_and_integral_ledger'][0]
        for quantity in ('length_m', 'conductance_W_K', 'distributed_heater_W', 'elevation_change_m'):
            assert abs(ledger[quantity]['residual']) <= ledger[quantity]['floating_point_allowance']
    assert record['field_qualification'] is False
    assert record['rigorous_error_bound'] is False
    assert adaptive.verify_adaptive_heated_transport_record(
        record, request=request, context=context, property_provider=provider)
