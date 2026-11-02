"""Incompressible Newtonian slurry limit: Poiseuille loss and adiabatic enthalpy."""
import importlib.util
import math
from pathlib import Path

import pytest

from pipeline_integrity import heated_slurry as hs
from pipeline_integrity import slurry_transport as slurry
from pipeline_integrity.contracts import EvidenceRef
from pipeline_integrity.datasets import file_digest

_spec = importlib.util.spec_from_file_location(
    'heated_slurry_reference', Path(__file__).parents[1] / 'examples' / 'slurry_transport.py')
_example = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_example)


@pytest.mark.parametrize('parts', [1, 4])
def test_adiabatic_constant_newtonian_limit_preserves_enthalpy_and_pressure_work(parts):
    base, context = _example.make_case()
    evidence = base.evidence_ids[0]
    source = file_digest(Path(__file__))
    prop = base.properties.model_copy(update={'source_sha256': source,
        'rheology': 'newtonian', 'flow_regime': 'newtonian_owner', 'yield_stress_Pa': 0.0})
    table = hs.SlurryThermalTable(mixture_id='authored_mixture', enthalpy_reference_id='thermal_at_290',
        property_template=prop, knots=tuple(hs.SlurryThermalKnot(
            temperature_K=t, thermal_enthalpy_J_kg=4000.0*(t-290.0),
            consistency_Pa_s_n=0.1, flow_index=1.0, yield_stress_Pa=0.0)
            for t in (290.0, 310.0)))
    provider = hs.TabulatedSlurryThermalProvider(table)
    request = hs.HeatedSlurryRequest(id='adiabatic_limit', asset_id=base.asset_id,
        event_id=base.event_id, clock_id=base.clock_id, time_s=0.0,
        mixture_id=table.mixture_id, enthalpy_reference_id=table.enthalpy_reference_id,
        rheology_reference_basis=prop.reference_basis, rheology_model='newtonian',
        inlet_pressure_pa_abs=1000000.0, inlet_temperature_K=300.0,
        mass_flow_kg_s=0.11, inner_diameter_m=0.05,
        reference_mixture_density_kg_m3=1100.0, solid_volume_fraction=0.1,
        cells=tuple(hs.HeatedSlurryCell(id=f'c{i}', inlet_port_id=f'p{i}', outlet_port_id=f'p{i+1}',
            length_m=10.0/parts, roughness_m=0.0, ambient_temperature_K=300.0,
            conductance_W_K=0.0, heater_into_fluid_W=0.0,
            geometry_evidence_id=evidence, heat_boundary_evidence_id=evidence) for i in range(parts)),
        pressure_support_pa_abs=(900000.0, 1100000.0), temperature_support_K=(290.0, 310.0),
        provider_identity=provider.metadata(), property_provider_kind='table',
        property_evidence_id=evidence, property_source_sha256=source, evidence_ids=(evidence,),
        reviews=dict.fromkeys(hs.REVIEWS, True),
        review_evidence_ids=dict.fromkeys(hs.REVIEWS, evidence),
        slurry_reviews=dict.fromkeys(slurry.REVIEWS, True),
        slurry_review_evidence_ids=dict.fromkeys(slurry.REVIEWS, evidence),
        slurry_owner_pins=slurry.native_backend_metadata(),
        coupling_owner_pins=hs.native_heated_slurry_metadata(),
        maximum_wall_stress_bracket_Pa=1000.0, maximum_cell_temperature_change_K=1.0)
    context = context.model_copy(update={'evidence': (EvidenceRef(id=evidence,
        origin='synthetic', sha256=source,
        citation='Authored constant-density constant-viscosity adiabatic slurry reference'),)})
    context = hs.bind_heated_slurry_context(request, context)
    record = hs.evaluate_heated_slurry(request, context, property_table=table)
    assert record['status'] == 'computed_diagnostic', record['issues']
    values = record['values']
    flow = 0.11 / 1100.0
    pressure_loss = 128.0 * 0.1 * 10.0 * flow / (math.pi * 0.05**4)
    expected_temperature = 300.0 + pressure_loss / (1100.0 * 4000.0)
    assert values['net_inlet_minus_outlet_pressure_Pa'] == pytest.approx(pressure_loss, abs=0.002)
    assert values['outlet']['temperature_K'] == pytest.approx(expected_temperature, abs=1e-7)
    assert values['total_heat_into_fluid_W'] == 0.0
    assert abs(values['global_enthalpy_flux_change_W']) < 1e-5
    assert values['summed_native_dissipated_power_W'] == pytest.approx(flow * pressure_loss, abs=1e-5)
    assert values['global_internal_energy_flux_change_W'] == pytest.approx(flow * pressure_loss, abs=1e-5)
    assert values['global_pressure_flow_work_flux_change_W'] == pytest.approx(-flow * pressure_loss, abs=1e-5)
    assert abs(values['global_energy_residual_W']) < 1e-5
    assert hs.verify_heated_slurry_record(record, request=request, context=context, property_table=table)
