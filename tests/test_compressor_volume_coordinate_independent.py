"""Dimensioned supplied correlations, not pulsation attenuation calibration."""
from decimal import Decimal, localcontext

import pytest

from pipeline_integrity.compressor_volume_coordinate import CompressorSurgeVolumeRequest as Surge, AcousticTerminalVolumeRequest as Terminal, evaluate_compressor_surge_volume as surge, evaluate_acoustic_terminal_volume as terminal


def request(**changes):
    values=dict(net_displaced_volume_per_revolution_m3=.0173,isentropic_exponent=1.31,
        suction_temperature_k=311.,molar_mass_kg_mol=.023,temperature_reference_k=100.,molar_mass_reference_kg_mol=.01,
        stage_pressure_ratio=3.71,supplied_suction_multiplier=2.37,supplied_discharge_multiplier=1.21,
        supplied_minimum_volume_m3=.003,coefficient_gas_state_unit_and_applicability_basis_id="authored-reviewed-correlation",
        coefficient_reference_scales_and_gas_state_reviewed=True)
    values.update(changes);return Surge(**values)


def terminal_request(**changes):
    values=dict(cylinder_displaced_volume_per_revolution_m3=.0071,molar_mass_kg_mol=.029,
        molar_mass_reference_kg_mol=.001,supplied_exponential_multiplier=23.1,
        supplied_exponential_decay_coefficient=.071,supplied_linear_multiplier=.83,
        coefficient_unit_and_acoustic_applicability_basis_id="authored-terminal-correlation",
        coefficient_and_terminal_correlation_applicability_reviewed=True)
    values.update(changes);return Terminal(**values)


@pytest.mark.parametrize("ratio,minimum",[(1.,0.),(2.9,.003),(17.,.1)])
def test_quarter_power_coordinate_with_independent_eighty_digit_and_bounds(ratio,minimum):
    q=request(stage_pressure_ratio=ratio,supplied_minimum_volume_m3=minimum)
    with localcontext() as ctx:
        ctx.prec=80;d=Decimal.from_float
        gas=d(q.isentropic_exponent)*d(q.suction_temperature_k)/d(q.temperature_reference_k)*d(q.molar_mass_reference_kg_mol)/d(q.molar_mass_kg_mol)
        su=max(d(minimum),d(q.supplied_suction_multiplier)*d(q.net_displaced_volume_per_revolution_m3)*gas.sqrt().sqrt())
        out=max(d(minimum),min(su,d(q.supplied_discharge_multiplier)*su/d(ratio).sqrt().sqrt()))
    r=surge(q)
    assert r['status']=='computed'
    assert r['suction_surge_volume_coordinate_m3']==pytest.approx(float(su),rel=3e-15)
    assert r['discharge_surge_volume_coordinate_m3']==pytest.approx(float(out),rel=3e-15)
    assert minimum<=r['discharge_surge_volume_coordinate_m3']<=r['suction_surge_volume_coordinate_m3']
    assert not r['acoustic_attenuation_or_vessel_acceptance_qualified']


def test_temperature_molar_reference_rescaling_preserves_calibrated_coordinate():
    q=request();base=surge(q)
    scale=18.
    r=surge(request(temperature_reference_k=100.*scale,supplied_suction_multiplier=q.supplied_suction_multiplier*scale**.25))
    assert r['suction_surge_volume_coordinate_m3']==pytest.approx(base['suction_surge_volume_coordinate_m3'])
    r=surge(request(molar_mass_reference_kg_mol=.01*scale,supplied_suction_multiplier=q.supplied_suction_multiplier/scale**.25))
    assert r['suction_surge_volume_coordinate_m3']==pytest.approx(base['suction_surge_volume_coordinate_m3'])


@pytest.mark.parametrize("molar_mass",[.002,.029,.13])
def test_terminal_exponential_plus_linear_against_eighty_digit_reference(molar_mass):
    q=terminal_request(molar_mass_kg_mol=molar_mass)
    with localcontext() as ctx:
        ctx.prec=80;d=Decimal.from_float;m=d(molar_mass)/d(q.molar_mass_reference_kg_mol)
        expected=d(q.cylinder_displaced_volume_per_revolution_m3)*(d(q.supplied_exponential_multiplier)*(-d(q.supplied_exponential_decay_coefficient)*m).exp()+d(q.supplied_linear_multiplier)*m)
    r=terminal(q)
    assert r['terminal_volume_coordinate_m3']==pytest.approx(float(expected),rel=3e-15)
    assert not r['terminal_reflection_or_attenuation_qualified']


@pytest.mark.parametrize("field",['supplied_suction_multiplier','supplied_discharge_multiplier','supplied_minimum_volume_m3'])
def test_missing_surge_calibration_or_minimum_is_not_selected(field):
    assert surge(request(**{field:None}))['status']=='needs_data'


@pytest.mark.parametrize("field",['supplied_exponential_multiplier','supplied_exponential_decay_coefficient','supplied_linear_multiplier'])
def test_missing_terminal_calibration_is_not_selected(field):
    assert terminal(terminal_request(**{field:None}))['status']=='needs_data'


def test_missing_review_and_exact_volume_scaling():
    assert surge(request(coefficient_reference_scales_and_gas_state_reviewed=False))['status']=='needs_data'
    assert terminal(terminal_request(coefficient_and_terminal_correlation_applicability_reviewed=False))['status']=='needs_data'
    assert terminal(terminal_request(cylinder_displaced_volume_per_revolution_m3=.0142))['terminal_volume_coordinate_m3']==pytest.approx(2*terminal(terminal_request())['terminal_volume_coordinate_m3'])


@pytest.mark.parametrize("case",['stage','gas_overflow','terminal_overflow'])
def test_compression_stage_and_finite_arithmetic_domain(case):
    if case=='stage':result=surge(request(stage_pressure_ratio=.7))
    elif case=='gas_overflow':result=surge(request(suction_temperature_k=1e308,temperature_reference_k=1e-308))
    else:result=terminal(terminal_request(supplied_linear_multiplier=1e308,molar_mass_kg_mol=1e308))
    assert result['status']=='out_of_scope'
