"""Identifiability, ideal particle-density and Hess reaction-energy references."""
import math
import pytest
from pipeline_integrity.gas_combustion_reference import ComponentCombustionDatum
from pipeline_integrity.gravity_constrained_calorific import GravityCalorificRequest,observe_gravity_constrained_calorific

R=8.31446261815324
CO2=-393500.;WATER=-285800.;CH4=-74800.;C2H6=-84700.


def request(nitrogen=.15,first_fuel_share=.6,water=.02):
    y1=(1-nitrogen)*first_fuel_share;y2=1-nitrogen-y1
    masses=(.016,.030,.028);md=y1*masses[0]+y2*masses[1]+nitrogen*masses[2]
    # Equal-PT ideal particle-density ratio; water belongs to the measurement
    # gas when present. No methane-only gravity mapping is assumed.
    mwet=(1-water)*md+water*.018
    datum=lambda name,m,h:ComponentCombustionDatum(fluid=name,molar_mass_kg_mol=m,released_combustion_energy_J_mol=h,combustion_reference_id='manufactured-Hess-reference')
    q=GravityCalorificRequest(gas_specific_gravity_to_air=mwet/.02897,dry_nitrogen_mole_fraction=nitrogen,air_molar_mass_kg_mol=.02897,
        nitrogen_data=datum('Nitrogen',.028,0.),fuel_data=(datum('Methane',.016,-(CO2+2*WATER-CH4)),datum('Ethane',.030,-(2*CO2+3*WATER-C2H6))),
        gravity_measurement_basis='wet_gas' if water else 'dry_gas',gravity_measurement_water_mole_fraction=water,
        reference_water_mole_fraction=water,water_molar_mass_kg_mol=.018,reference_temperature_K=300.,reference_pressure_Pa_abs=100000.,
        combustion_reference_id='manufactured-Hess-reference',calorific_basis='gross_liquid_product_water',
        two_known_fuels_and_nitrogen_are_complete_dry_composition_reviewed=True,
        same_PT_ideal_gas_gravity_and_single_gas_reference_reviewed=True,supplied_combustion_reference_and_water_basis_reviewed=True)
    return q,(y1,y2,nitrogen)


@pytest.mark.parametrize('nitrogen',[0.,.15,.7])
@pytest.mark.parametrize('fuel_share',[.15,.6,.9])
@pytest.mark.parametrize('water',[0.,.03])
def test_independent_molecular_density_inverse_and_hess_total_reaction_energy(nitrogen,fuel_share,water):
    q,(y1,y2,yn)=request(nitrogen,fuel_share,water);r=observe_gravity_constrained_calorific(q)
    assert r.status=='computed'
    assert r.calorific_basis==q.calorific_basis
    assert r.combustion_reference_id==q.combustion_reference_id
    assert r.reference_temperature_K==q.reference_temperature_K
    assert r.reference_pressure_Pa_abs==q.reference_pressure_Pa_abs
    assert r.reference_water_mole_fraction==water
    assert list(r.dry_mole_fractions.values())==pytest.approx([y1,y2,yn],abs=1e-14)
    # Burn one mole of dry mixture: product carbon and hydrogen counts give
    # CO2 and liquid-water moles independently of constituent heat averaging.
    carbon=y1+2*y2;product_water=2*y1+3*y2
    reaction_heat=-(carbon*CO2+product_water*WATER-y1*CH4-y2*C2H6)
    total_moles=q.reference_pressure_Pa_abs/(R*q.reference_temperature_K)
    dry_moles=total_moles*(1-water)
    expected_energy=dry_moles*reaction_heat
    assert r.dry_molar_combustion_energy_J_mol==pytest.approx(reaction_heat,rel=2e-15)
    assert r.reference_volumetric_combustion_energy_J_m3==pytest.approx(expected_energy,rel=3e-15)
    direct_mass=total_moles*((1-water)*(y1*.016+y2*.030+yn*.028)+water*.018)
    assert r.reference_density_kg_m3==pytest.approx(direct_mass,rel=2e-15)
    assert r.reference_mass_combustion_energy_J_kg*direct_mass==pytest.approx(expected_energy,rel=3e-15)
    assert r.gravity_mass_residual_kg_mol==pytest.approx(0.,abs=1e-16)


def test_reference_pressure_temperature_and_water_dilution_preserve_dry_molar_energy():
    q,_=request(water=0.);a=observe_gravity_constrained_calorific(q)
    b=observe_gravity_constrained_calorific(q.model_copy(update={'reference_pressure_Pa_abs':2e5,'reference_temperature_K':600.,'reference_water_mole_fraction':.1}))
    assert b.dry_molar_combustion_energy_J_mol==a.dry_molar_combustion_energy_J_mol
    assert b.reference_volumetric_combustion_energy_J_m3==pytest.approx(.9*a.reference_volumetric_combustion_energy_J_m3)


@pytest.mark.parametrize('field',['gas_specific_gravity_to_air','dry_nitrogen_mole_fraction','air_molar_mass_kg_mol','gravity_measurement_water_mole_fraction','reference_water_mole_fraction','reference_temperature_K','water_molar_mass_kg_mol'])
def test_missing_information_never_replaced_by_empirical_headlee_constant(field):
    q,_=request();assert observe_gravity_constrained_calorific(q.model_copy(update={field:None})).status=='needs_data'


@pytest.mark.parametrize('field',['two_known_fuels_and_nitrogen_are_complete_dry_composition_reviewed','same_PT_ideal_gas_gravity_and_single_gas_reference_reviewed','supplied_combustion_reference_and_water_basis_reviewed'])
def test_unreviewed_composition_class_or_reference_withheld(field):
    q,_=request();assert observe_gravity_constrained_calorific(q.model_copy(update={field:False})).status=='needs_data'


def test_equal_molar_masses_have_no_unique_inverse_even_with_different_heats():
    q,_=request();a,b=q.fuel_data
    r=observe_gravity_constrained_calorific(q.model_copy(update={'fuel_data':(a,b.model_copy(update={'molar_mass_kg_mol':a.molar_mass_kg_mol}))}))
    assert r.status=='not_identifiable' and r.dry_mole_fractions is None


@pytest.mark.parametrize('gravity',[.01,3.])
def test_infeasible_gravity_not_clipped_into_binary_convex_domain(gravity):
    q,_=request();assert observe_gravity_constrained_calorific(q.model_copy(update={'gas_specific_gravity_to_air':gravity})).status=='out_of_scope'


def test_pure_inert_zero_heat_and_inconsistent_inert_gravity():
    q,_=request(nitrogen=1.,water=0.);r=observe_gravity_constrained_calorific(q)
    assert r.status=='computed' and r.reference_volumetric_combustion_energy_J_m3==0
    assert observe_gravity_constrained_calorific(q.model_copy(update={'gas_specific_gravity_to_air':1.5})).status=='out_of_scope'


@pytest.mark.parametrize('kind',['reference','inert','dry_water','nonfinite','duplicate'])
def test_inconsistent_and_invalid_declared_contract(kind):
    q,_=request()
    if kind=='reference':update={'fuel_data':tuple(d.model_copy(update={'combustion_reference_id':'other'}) for d in q.fuel_data)}
    elif kind=='inert':update={'nitrogen_data':q.nitrogen_data.model_copy(update={'released_combustion_energy_J_mol':1.})}
    elif kind=='dry_water':update={'gravity_measurement_basis':'dry_gas'}
    elif kind=='nonfinite':update={'gas_specific_gravity_to_air':math.inf}
    else:update={'fuel_data':(q.fuel_data[0],q.fuel_data[0])}
    with pytest.raises(ValueError):observe_gravity_constrained_calorific(q.model_copy(update=update))
