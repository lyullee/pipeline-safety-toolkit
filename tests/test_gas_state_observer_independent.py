"""State-coordinate laws and bounded thermodynamic consistency, not EOS calibration."""
import math
import pytest
from CoolProp.CoolProp import PropsSI
from pipeline_integrity.gas_caloric_mixture import GasCaloricComponent as Component
from pipeline_integrity.gas_state_observer import GasStateObservationRequest as Request,observe_gas_state
from pipeline_integrity.thermo import CoolPropProvider,StateRequest

RU=8.31446261815324

def ideal(**changes):
    args=dict(model='ideal',coordinate='pressure_temperature',components=(Component(id='a',fraction=.25,molar_mass_kg_mol=.02),
        Component(id='b',fraction=.75,molar_mass_kg_mol=.04)),fraction_basis='mole',temperature_K=300.,pressure_Pa_abs=2e5,
        property_basis_id='authored',composition_and_gas_property_domain_reviewed=True)
    return Request(**(args|changes))

def real(fluid='Methane',**changes):
    args=dict(model='HEOS',coordinate='pressure_temperature',components=(Component(id=fluid,fraction=1.,molar_mass_kg_mol=None),),
        fraction_basis='mole',temperature_K=350.,pressure_Pa_abs=2e6,property_basis_id='reviewed-HEOS',
        composition_and_gas_property_domain_reviewed=True)
    return Request(**(args|changes))

@pytest.mark.parametrize('basis,mass',[('mole',.035),('mass',1/(.25/.02+.75/.04))])
def test_ideal_forward_density_uses_mole_or_mass_basis_without_g_per_mol_confusion(basis,mass):
    q=ideal(fraction_basis=basis);r=observe_gas_state(q)
    assert r.mixture_molar_mass_kg_mol==pytest.approx(mass,rel=2e-15)
    assert r.density_kg_m3==pytest.approx(2e5*mass/(RU*300.),rel=3e-15)
    assert r.compressibility_factor==1.
    assert r.isothermal_compressibility_1_Pa==pytest.approx(1/2e5)
    assert r.phase=='ideal_gas_assumed'

@pytest.mark.parametrize('temperature,volume',[(300.,1.),(600.,1.),(300.,2.),(400.,.5)])
def test_closed_mass_endpoint_pressure_matches_boyle_charles_and_exact_molar_count(temperature,volume):
    r=observe_gas_state(ideal(coordinate='mass_volume_temperature',pressure_Pa_abs=None,mass_kg=2.,volume_m3=volume,temperature_K=temperature))
    assert r.pressure_Pa_abs==pytest.approx((2/.035)*RU*temperature/volume,rel=3e-15)
    assert abs(r.state_mass_residual_kg)<1e-14

def test_closed_mass_inverse_volume_and_amount_extensivity():
    a=observe_gas_state(ideal(coordinate='mass_pressure_temperature',mass_kg=2.))
    b=observe_gas_state(ideal(coordinate='mass_pressure_temperature',mass_kg=10.))
    assert a.volume_m3==pytest.approx(2*RU*300/(.035*2e5),rel=3e-15)
    assert b.volume_m3==pytest.approx(5*a.volume_m3,rel=3e-15)

def test_missing_molar_mass_and_missing_review_stay_unknown():
    q=ideal(components=(Component(id='unknown',fraction=1.,molar_mass_kg_mol=None),))
    assert observe_gas_state(q).status=='needs_data'
    assert observe_gas_state(ideal(composition_and_gas_property_domain_reviewed=False)).status=='needs_data'

@pytest.mark.parametrize('bad',[-1.,0.,float('nan')])
def test_absolute_state_variables_are_required(bad):
    with pytest.raises(ValueError):ideal(pressure_Pa_abs=bad)

def test_overdetermined_endpoint_and_composition_syntax_are_rejected():
    with pytest.raises(ValueError):ideal(mass_kg=2.)
    with pytest.raises(ValueError):ideal(components=(Component(id='a[1]',fraction=1.,molar_mass_kg_mol=.02),))
    with pytest.raises(ValueError):observe_gas_state(ideal(components=(Component(id='a',fraction=.9,molar_mass_kg_mol=.02),)))

@pytest.mark.parametrize('fluid',['Methane','Nitrogen','Hydrogen'])
def test_real_dilute_limit_matches_independent_molar_ideal_law(fluid):
    r=observe_gas_state(real(fluid,pressure_Pa_abs=100.))
    m=PropsSI('molar_mass','HEOS::'+fluid);gas=PropsSI('gas_constant','HEOS::'+fluid)
    ideal_rho=100*m/(gas*350)
    assert r.density_kg_m3==pytest.approx(ideal_rho,rel=2e-5)
    assert r.compressibility_factor==pytest.approx(1.,abs=2e-5)
    assert r.provider_identity['phase_imposed'] is False

@pytest.mark.parametrize('fluid,pressure,temperature',[('Methane',3e6,320.),('Nitrogen',4e6,330.),('CarbonDioxide',12e6,350.)])
def test_real_density_derivatives_obey_independent_heat_capacity_identity(fluid,pressure,temperature):
    def density(p,t):return observe_gas_state(real(fluid,pressure_Pa_abs=p,temperature_K=t)).density_kg_m3
    rho=density(pressure,temperature);dp=pressure*2e-5;dt=temperature*2e-5
    alpha=-(density(pressure,temperature+dt)-density(pressure,temperature-dt))/(2*dt*rho)
    kappa=(density(pressure+dp,temperature)-density(pressure-dp,temperature))/(2*dp*rho)
    state=CoolPropProvider(use_cache=False).resolve(StateRequest(fluid=fluid,inputs={'P':pressure,'T':temperature}))
    assert kappa>0
    assert observe_gas_state(real(fluid,pressure_Pa_abs=pressure,temperature_K=temperature)).isothermal_compressibility_1_Pa==pytest.approx(kappa,rel=3e-6)
    assert state.cp_j_kg_k-state.cv_j_kg_k==pytest.approx(temperature*alpha**2/(rho*kappa),rel=3e-6)

@pytest.mark.parametrize('fluid',['Methane','Nitrogen'])
def test_real_pure_closed_mass_volume_inverse_exposes_pressure_without_energy_path_claim(fluid):
    first=observe_gas_state(real(fluid));mass=first.density_kg_m3*3.
    inverse=observe_gas_state(real(fluid,coordinate='mass_volume_temperature',pressure_Pa_abs=None,mass_kg=mass,volume_m3=3.))
    assert inverse.pressure_Pa_abs==pytest.approx(2e6,rel=2e-9)
    assert abs(inverse.state_mass_residual_kg)<2e-9*mass
    assert inverse.thermal_path_or_phase_split_inferred is False

def mixture(**changes):
    return real(components=(Component(id='Methane',fraction=.8,molar_mass_kg_mol=None),Component(id='Nitrogen',fraction=.2,molar_mass_kg_mol=None)),**changes)

def test_mixture_molar_mass_and_dilute_density_do_not_use_unweighted_species_average():
    r=observe_gas_state(mixture(pressure_Pa_abs=100.))
    m=.8*PropsSI('molar_mass','Methane')+.2*PropsSI('molar_mass','Nitrogen')
    assert r.mixture_molar_mass_kg_mol==pytest.approx(m,rel=3e-15)
    assert r.density_kg_m3==pytest.approx(100*m/(r.molar_gas_constant_J_mol_K*350),rel=2e-5)
    assert r.mass_fractions['Nitrogen']>r.mole_fractions['Nitrogen']

def test_mixture_closed_volume_inverse_requires_reviewed_bracket_then_preserves_mass():
    f=observe_gas_state(mixture());mass=f.density_kg_m3*2.
    q=mixture(coordinate='mass_volume_temperature',pressure_Pa_abs=None,mass_kg=mass,volume_m3=2.)
    assert observe_gas_state(q).status=='needs_data'
    q=q.model_copy(update={'inverse_pressure_bracket_Pa_abs':(1e6,3e6),'single_phase_isotherm_bracket_reviewed':True})
    r=observe_gas_state(q)
    assert r.pressure_Pa_abs==pytest.approx(2e6,rel=3e-10)
    assert abs(r.state_mass_residual_kg)<1e-8*mass
    with pytest.raises(ValueError,match='outside the supplied pressure bracket'):
        observe_gas_state(q.model_copy(update={'inverse_pressure_bracket_Pa_abs':(1e5,5e5)}))

def test_heos_mass_fraction_coordinate_reuses_existing_composition_ledger():
    a=observe_gas_state(mixture())
    c=tuple(Component(id=k,fraction=y,molar_mass_kg_mol=None) for k,y in a.mass_fractions.items())
    b=observe_gas_state(real(components=c,fraction_basis='mass'))
    assert b.density_kg_m3==pytest.approx(a.density_kg_m3,rel=2e-13)
    assert b.mole_fractions==pytest.approx(a.mole_fractions,rel=2e-13)

def test_gas_domain_and_species_molar_mass_identity_are_not_silently_overridden():
    with pytest.raises(ValueError,match='phase domain'):observe_gas_state(real('Water',temperature_K=300.,pressure_Pa_abs=1e5))
    with pytest.raises(ValueError,match='molar mass disagrees'):
        observe_gas_state(real(components=(Component(id='Methane',fraction=1.,molar_mass_kg_mol=16.),)))
