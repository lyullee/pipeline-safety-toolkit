"""Invalid-input API gates only; valid model calculations are not executed."""
import runpy
from pathlib import Path

import pytest
from pydantic import ValidationError

from pipeline_integrity import closed_liquid_compatibility as liquid
from pipeline_integrity import gas_meter_sizing as meter
from pipeline_integrity import growing_pit_leak as pit
from pipeline_integrity import slurry_transport as slurry

ROOT = Path(__file__).resolve().parents[1]


def pit_request():
    return pit.GrowingPitLeakRequest(geometry='spherical_intersection', density_kg_m3=1000.,
        wall_thickness_m=.01, initial_pit_radius_m=.009, radial_growth_m_s=1e-8,
        history=(pit.LeakHistoryPoint(time_s=0., liquid_head_m=2., discharge_coefficient=.6),
                 pit.LeakHistoryPoint(time_s=100., liquid_head_m=1., discharge_coefficient=.6)))


@pytest.mark.parametrize('updates,reason', [
    ({'radial_growth_m_s': None}, 'spherical geometry'),
    ({'initial_pit_radius_m': None}, 'spherical geometry'),
    ({'wall_thickness_m': None}, 'spherical geometry'),
    ({'area_coefficients_m2_m2_s_m2_s2': (0., 1., 0.)}, 'spherical geometry'),
    ({'geometry': 'supplied_area_polynomial'}, 'polynomial geometry'),
])
def test_pit_geometry_contradiction_before_quadrature(updates, reason, monkeypatch):
    import scipy.integrate
    monkeypatch.setattr(scipy.integrate, 'quad', lambda *a, **k: pytest.fail('invalid input reached integration'))
    q = pit_request()
    assert pit.GrowingPitLeakRequest.model_validate(q.model_dump()) == q
    with pytest.raises(ValidationError, match=reason):
        pit.evaluate_growing_pit_leak(q.model_copy(update=updates))


@pytest.mark.parametrize('first,last', [(1., 2.), (0., 0.), (10., 0.)])
def test_pit_clock_origin_or_order(first, last):
    data = pit_request().model_dump()
    data['history'][0]['time_s'], data['history'][1]['time_s'] = first, last
    with pytest.raises(ValidationError, match='history starts at zero'):
        pit.GrowingPitLeakRequest.model_validate(data)


def injection_request():
    pressure = liquid.TestPressureCoordinate(value_Pa=1e5, reference='absolute')
    return liquid.RequiredLiquidInjectionRequest(asset_id='pipe', clock_id='clock', reference_clock_id='clock',
        reference_time_s=0., target_time_s=10., reference_pressure=pressure, target_pressure=pressure,
        reference_external_pressure_Pa_abs=1e5, target_external_pressure_Pa_abs=1e5,
        reference_water_temperature_K=300., target_water_temperature_K=301.,
        reference_wall_temperature_K=300., target_wall_temperature_K=301.,
        fluid_id='water', property_basis_id='basis', geometry_basis_id='geometry',
        uniform_quasistatic_pure_liquid_without_trapped_gas_reviewed=True)


class ForbiddenProvider:
    def __getattr__(self, name):
        pytest.fail('invalid input reached property provider: ' + name)


def test_injection_clock_mismatch_before_provider():
    q = injection_request()
    assert liquid.RequiredLiquidInjectionRequest.model_validate(q.model_dump()) == q
    with pytest.raises(ValidationError, match='clocks must be reconciled'):
        liquid.evaluate_required_liquid_injection(q.model_copy(update={'clock_id': 'other'}),
            density_provider=ForbiddenProvider(), volume_provider=ForbiddenProvider())


@pytest.mark.parametrize('updates', [
    {'reference': 'gauge'}, {'ambient_pressure_Pa_abs': 1e5},
    {'value_Pa': 0.}, {'value_Pa': -1.}, {'value_Pa': float('nan')},
    {'value_Pa': '1 bar'}, {'reference': 'unknown'},
])
def test_pressure_reference_missing_units_or_invalid_value(updates):
    q = injection_request().reference_pressure
    with pytest.raises(ValidationError):
        liquid.TestPressureCoordinate.model_validate({**q.model_dump(), **updates})


def meter_case():
    request, context = runpy.run_path(str(ROOT / 'examples/gas_meter.py'))['make_case'](reviewed=True)
    q = meter.GasMeterSizingRequest(template=request, target_signed_mass_flow_kg_s=1.,
        secondary_dimension_bracket_m=(.02, .08),
        geometry_and_calibration_valid_over_bracket_reviewed=True,
        continuous_single_geometry_branch_reviewed=True)
    return q, context


@pytest.mark.parametrize('updates,reason', [
    ({'secondary_dimension_bracket_m': (.08, .02)}, 'dimension bracket'),
    ({'secondary_dimension_bracket_m': (.02, .1)}, 'dimension bracket'),
    ({'target_signed_mass_flow_kg_s': 0.}, 'zero flow'),
    ({'target_signed_mass_flow_kg_s': float('nan')}, 'finite'),
])
def test_meter_inverse_invalid_geometry_never_calls_flow_owner(updates, reason, monkeypatch):
    q, context = meter_case()
    assert meter.GasMeterSizingRequest.model_validate(q.model_dump()) == q
    monkeypatch.setattr(meter, 'evaluate_gas_meter', lambda *a, **k: pytest.fail('invalid geometry reached flow owner'))
    with pytest.raises(ValidationError, match=reason):
        meter.evaluate_gas_meter_sizing(q.model_copy(update=updates), context)


@pytest.mark.parametrize('units', [None, {}, {'pressure': 'bar'}])
def test_meter_missing_or_conflicting_unit_map(units):
    q, _ = meter_case()
    data = q.template.model_dump()
    if units is None:
        del data['input_units']
    else:
        data['input_units'] = units
    with pytest.raises(ValidationError) as error:
        type(q.template).model_validate(data)
    assert any(e['loc'] == ('input_units',) or 'SI absolute-pressure' in str(e.get('ctx', {}))
               for e in error.value.errors())


@pytest.mark.parametrize('field,value,reason', [
    ('flow_regime', 'unknown', 'flow regime'),
    ('homogeneous_condition', 'unknown', 'homogeneous operating'),
    ('settling_condition', 'unknown', 'homogeneous operating'),
    ('solid_volume_fraction', None, 'solid concentration'),
    ('supported_absolute_velocity_m_s', None, 'velocity support'),
    ('particle_diameter_m', None, 'particle geometry'),
    ('carrier_density_kg_m3', None, 'constituent densities'),
    ('temperature_K', 400., 'temperature'),
])
@pytest.mark.parametrize('rheology', ['bingham', 'power_law'])
def test_pressure_driven_material_unknowns_rejected_before_integral(field, value, reason, rheology, monkeypatch):
    template, _ = runpy.run_path(str(ROOT / 'examples/slurry_transport.py'))['make_case'](reviewed=True)
    properties = template.properties
    if rheology == 'power_law':
        properties = type(properties).model_validate({**properties.model_dump(),
            'rheology': 'power_law', 'flow_index': .6, 'yield_stress_Pa': 0.})
    q = slurry.LaminarPressureFlowRequest(properties=properties,
        inner_diameter_m=.1, length_m=10., driving_pressure_difference_Pa=1000.,
        laminar_homogeneous_no_slip_domain_reviewed=True)
    assert slurry.LaminarPressureFlowRequest.model_validate(q.model_dump()) == q
    properties = q.properties.model_copy(update={field: value})
    monkeypatch.setattr(slurry, '_constitutive_flow_at_stress', lambda *a, **k: pytest.fail('invalid material reached radial integration'))
    with pytest.raises((ValidationError, ValueError), match=reason):
        slurry.evaluate_laminar_pressure_flow(q.model_copy(update={'properties': properties}))
