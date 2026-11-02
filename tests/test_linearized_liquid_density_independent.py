"""Reference tangents, signed changes, inversion and explicit property support."""
import numpy as np
import pytest
from pipeline_integrity.linearized_liquid_density import LinearizedLiquidDensityLaw,evaluate_linearized_liquid_density,LinearizedLiquidCompatibilityProvider


def law(**kw):
    return LinearizedLiquidDensityLaw(fluid_id='caller-liquid',property_basis_id='authored-reference-tangents',
        reference_density_kg_m3=812.,reference_pressure_pa_abs=1e5,reference_temperature_K=300.,
        reference_isothermal_bulk_modulus_pa=1.6e9,reference_volumetric_thermal_expansion_K_inv=.0007,
        pressure_support_pa_abs=(5e4,3e7),temperature_support_K=(280.,320.),
        maximum_absolute_fractional_density_perturbation=.04,single_liquid_linearized_property_support_reviewed=True,**kw)


@pytest.mark.parametrize('pressure,temperature',[(1e5,300.),(1e7,300.),(5e4,300.),(1e5,285.),(1e5,315.),(1e7,310.)])
def test_density_matches_independent_reference_mass_volume_perturbation(pressure,temperature):
    q=law();r=evaluate_linearized_liquid_density(q,pressure_pa_abs=pressure,temperature_K=temperature)
    dp=pressure-1e5;dt=temperature-300.
    expected=812.+(812./1.6e9)*dp-(812.*.0007)*dt
    assert r.status=='computed'
    assert r.density_kg_m3==pytest.approx(expected,rel=2e-16)
    assert not r.adiabatic_acoustic_modulus_inferred
    # Exact mass-compatible volume is inverse density, rather than adding the
    # same first-order fraction directly to both density and volume.
    mass=73.;volume=mass/r.density_kg_m3
    assert volume*r.density_kg_m3==pytest.approx(mass,rel=2e-16)


def test_finite_difference_tangents_are_current_normalized_not_acoustic():
    q=law();p=9e6;t=308.;r=evaluate_linearized_liquid_density(q,pressure_pa_abs=p,temperature_K=t)
    rho=lambda p,t:evaluate_linearized_liquid_density(q,pressure_pa_abs=p,temperature_K=t).density_kg_m3
    dp=(rho(p+100.,t)-rho(p-100.,t))/200.
    dt=(rho(p,t+.001)-rho(p,t-.001))/.002
    assert dp==pytest.approx(r.density_pressure_derivative_kg_m3_pa,rel=2e-9)
    assert dt==pytest.approx(r.density_temperature_derivative_kg_m3_K,rel=2e-10)
    assert r.current_isothermal_compressibility_pa_inv==pytest.approx(dp/rho(p,t),rel=2e-9)
    assert r.current_volumetric_expansion_K_inv==pytest.approx(-dt/rho(p,t),rel=2e-10)


def test_existing_mass_compatibility_provider_keeps_explicit_fluid_identity():
    q=law();p=LinearizedLiquidCompatibilityProvider(q)
    r=p.resolve_density(pressure_Pa_abs=2e6,water_temperature_K=305.)
    direct=evaluate_linearized_liquid_density(q,pressure_pa_abs=2e6,temperature_K=305.)
    assert r.fluid_id=='caller-liquid' and r.phase=='liquid'
    assert r.density_kg_m3==direct.density_kg_m3
    assert r.isothermal_compressibility_Pa_inv==direct.current_isothermal_compressibility_pa_inv


@pytest.mark.parametrize('failure',['review','pressure','temperature','individual','absolute','unknown'])
def test_domain_and_unsupported_cancellation_are_rejected(failure):
    q=law()
    if failure=='review':assert evaluate_linearized_liquid_density(q.model_copy(update={'single_liquid_linearized_property_support_reviewed':False}),pressure_pa_abs=1e5,temperature_K=300.).status=='needs_data'
    elif failure=='pressure':assert evaluate_linearized_liquid_density(q,pressure_pa_abs=4e7,temperature_K=300.).status=='out_of_scope'
    elif failure=='temperature':assert evaluate_linearized_liquid_density(q,pressure_pa_abs=1e5,temperature_K=330.).status=='out_of_scope'
    elif failure=='individual':
        q=q.model_copy(update={'maximum_absolute_fractional_density_perturbation':.005})
        assert evaluate_linearized_liquid_density(q,pressure_pa_abs=1e5+1.6e9*.007,temperature_K=310.).status=='out_of_scope'
    elif failure=='absolute':
        with pytest.raises(ValueError):evaluate_linearized_liquid_density(q,pressure_pa_abs=-1.,temperature_K=300.)
    else:
        with pytest.raises(ValueError):LinearizedLiquidDensityLaw.model_validate(dict(q.model_dump(),infer_sound_speed=True))
