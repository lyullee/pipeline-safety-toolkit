"""Valid-baseline constructor input checks; no harmonic/energy solves."""
import dataclasses

import pytest

from pipeline_integrity.harmonic_response import HarmonicOscillator
from pipeline_integrity.stored_liquid_energy import LinearLiquidCompression
from pipeline_integrity.gas_acoustic_pipe import IdealGasInternalEnergyEOS
from pipeline_integrity.liquid_moc_friction import LiquidMOCFrictionState

BASELINES = (
    HarmonicOscillator(mass_kg=1., stiffness_N_m=100., damping_N_s_m=1.,
                       force_amplitude_N=10., angular_frequency_rad_s=2.),
    LinearLiquidCompression(pressure_excess_pa=1e5, reference_volume_m3=1., bulk_modulus_pa=2e9),
)


@pytest.mark.parametrize('q,field', [(q, f.name) for q in BASELINES for f in dataclasses.fields(q)])
@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -float('inf'), '1 kPa', None, True])
def test_dataclass_rejects_non_numeric_or_nonfinite_field(q, field, bad):
    assert type(q)(**dataclasses.asdict(q)) == q
    with pytest.raises(ValueError, match=field):
        dataclasses.replace(q, **{field: bad})


@pytest.mark.parametrize('q,field', [(q, f.name) for q in BASELINES for f in dataclasses.fields(q)
                                   if f.default is dataclasses.MISSING])
def test_dataclass_required_field_omission(q, field):
    data = dataclasses.asdict(q)
    del data[field]
    with pytest.raises(TypeError, match=field):
        type(q)(**data)


@pytest.mark.parametrize('field', ['mass_kg', 'stiffness_N_m'])
@pytest.mark.parametrize('bad', [0., -1.])
def test_harmonic_positive_physical_inputs(field, bad):
    with pytest.raises(ValueError, match='mass and stiffness must be positive'):
        dataclasses.replace(BASELINES[0], **{field: bad})


@pytest.mark.parametrize('field', ['damping_N_s_m', 'force_amplitude_N', 'angular_frequency_rad_s'])
def test_harmonic_nonnegative_physical_inputs(field):
    with pytest.raises(ValueError, match='nonnegative'):
        dataclasses.replace(BASELINES[0], **{field: -1.})


def test_linear_liquid_inconsistent_strain_domain():
    with pytest.raises(ValueError, match='entire reference volume'):
        dataclasses.replace(BASELINES[1], pressure_excess_pa=BASELINES[1].bulk_modulus_pa)


@pytest.mark.parametrize('field', ['specific_gas_constant_J_kg_K', 'gamma', 'internal_energy_reference_J_kg'])
@pytest.mark.parametrize('bad', [float('nan'), float('inf'), -float('inf'), '287 J/kg/K', None, True])
def test_ideal_eos_rejects_wrong_parameter_types(field, bad):
    q = IdealGasInternalEnergyEOS()
    assert dataclasses.replace(q) == q
    with pytest.raises(ValueError, match='finite R'):
        dataclasses.replace(q, **{field: bad})


@pytest.mark.parametrize('field,bad', [('specific_gas_constant_J_kg_K', 0.),
                                     ('specific_gas_constant_J_kg_K', -1.), ('gamma', 1.), ('gamma', .99)])
def test_ideal_eos_rejects_unphysical_constitutive_inputs(field, bad):
    with pytest.raises(ValueError, match='gamma>1'):
        dataclasses.replace(IdealGasInternalEnergyEOS(), **{field: bad})


@pytest.mark.parametrize('updates,reason', [
    ({'pipe_id': ''}, 'identity'), ({'pipe_id': None}, 'identity'),
    ({'time_s': float('nan')}, 'time_s'), ({'density_kg_m3': True}, 'diameter and density'),
    ({'inner_diameter_m': 0.}, 'diameter and density'),
    ({'chainage_m': (0., 1.)}, 'aligned immutable'),
    ({'velocity_m_s': [0., 0., 0.]}, 'aligned immutable'),
    ({'pressure_pa_abs': (1e5, 0., 1e5)}, 'absolute station pressures'),
    ({'pressure_pa_abs': (1e5, float('inf'), 1e5)}, 'finite station'),
    ({'velocity_m_s': (0., '1 m/s', 0.)}, 'finite station'),
    ({'chainage_m': (0., 2., 1.)}, 'strictly increase'),
    ({'chainage_m': (0., 1., 1.)}, 'strictly increase'),
])
def test_moc_state_rejects_mismatched_or_invalid_samples(updates, reason):
    q = LiquidMOCFrictionState(pipe_id='pipe', time_s=0., inner_diameter_m=.1,
        density_kg_m3=1000., chainage_m=(0., 1., 2.),
        pressure_pa_abs=(1e5, 1e5, 1e5), velocity_m_s=(0., 0., 0.))
    assert dataclasses.replace(q) == q
    with pytest.raises(ValueError, match=reason):
        dataclasses.replace(q, **updates)
