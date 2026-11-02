"""Charge transport references, not validation of a universal freshwater fit."""
import math
import pytest
from pydantic import ValidationError
from pipeline_integrity.ionic_water_conductivity import (
    DissolvedSpecies, IonicWaterRequest, observe_ionic_water_conductivity,
)

NA = 6.02214076e23
E_CHARGE = 1.602176634e-19
KB = 1.380649e-23


def request(stoich=(1, -1), numbers=(2., 2.), masses=(.023, .0355), frictions=(3e-12, 4e-12), field=3.):
    # Friction is force/velocity for one particle. Electrical mobility follows
    # separately from equilibrium of the electrical force and linear drag.
    density = math.fsum(c*m for c,m in zip(numbers, masses))
    species = tuple(DissolvedSpecies(species_id=f'ion{i}', dissolved_mass_fraction=c*m/density,
        molar_mass_kg_mol=m, charge_number=z,
        electrical_mobility_m2_V_s=abs(z)*E_CHARGE/f)
        for i,(z,c,m,f) in enumerate(zip(stoich,numbers,masses,frictions)))
    return IonicWaterRequest(dissolved_solids_kg_m3=density,species=species,
        temperature_K=298.15,pressure_Pa_abs=101325.,coefficient_temperature_K=298.15,
        coefficient_pressure_Pa_abs=101325.,analysis_id='manufactured-analysis',
        transport_coefficient_id='specified-particle-friction',ionic_analysis_and_transport_domain_reviewed=True,
        background_conductivity_S_m=0.,electric_field_V_m=field)


@pytest.mark.parametrize('z,c,m,f', [((1,-1),(2.,2.),(.023,.0355),(3e-12,4e-12)),
    ((2,-1),(1.,2.),(.04,.0355),(4e-12,5e-12)),
    ((1,-2),(4.,2.),(.023,.096),(2e-12,6e-12))])
@pytest.mark.parametrize('field', [-9.,0.,7.])
def test_independent_single_particle_force_drag_charge_current_and_joule_budget(z,c,m,f,field):
    q=request(z,c,m,f,field); r=observe_ionic_water_conductivity(q)
    assert r.status=='computed'
    # Both charge signs drift oppositely but carry current in the same direction.
    velocities=[zi*E_CHARGE*field/fi for zi,fi in zip(z,f)]
    current=math.fsum(ci*NA*zi*E_CHARGE*vi for ci,zi,vi in zip(c,z,velocities))
    dissipation=math.fsum(ci*NA*fi*vi**2 for ci,fi,vi in zip(c,f,velocities))
    assert r.current_density_A_m2==pytest.approx(current,rel=3e-15,abs=1e-20)
    assert r.joule_heating_W_m3==pytest.approx(dissipation,rel=3e-15,abs=1e-20)
    assert r.net_charge_concentration_C_m3==pytest.approx(0.,abs=1e-8)
    assert r.dissolved_mass_closure_kg_m3==pytest.approx(0.,abs=1e-15)
    assert [s.concentration_mol_m3 for s in r.species]==pytest.approx(c)
    assert r.resistivity_ohm_m*r.conductivity_S_m==pytest.approx(1.)


@pytest.mark.parametrize('step', [1e-9,5e-10,2.5e-10])
def test_independent_detailed_balance_random_walk_small_field_reference(step):
    # Biased hopping rates obey exp(q E dx/kBT). Their low-field limiting
    # current provides a separate discrete stochastic charge-flux reference.
    q=request(field=2.); r=observe_ionic_water_conductivity(q); current=0.
    for s in q.species:
        c=q.dissolved_solids_kg_m3*s.dissolved_mass_fraction/s.molar_mass_kg_mol
        charge=s.charge_number*E_CHARGE
        diffusion=s.electrical_mobility_m2_V_s*KB*q.temperature_K/abs(charge)
        rate=diffusion/step**2
        a=charge*q.electric_field_V_m*step/(2*KB*q.temperature_K)
        drift=2*rate*step*math.sinh(a)
        current+=c*NA*charge*drift
    assert r.current_density_A_m2==pytest.approx(current,rel=1e-12)


def test_mass_concentration_scaling_reference_unit_and_no_universal_TDS_mapping():
    q=request();a=observe_ionic_water_conductivity(q)
    b=observe_ionic_water_conductivity(q.model_copy(update={'dissolved_solids_kg_m3':q.dissolved_solids_kg_m3*1000}))
    assert b.conductivity_S_m==pytest.approx(a.conductivity_S_m*1000)
    assert b.resistivity_ohm_m==pytest.approx(a.resistivity_ohm_m/1000)
    # 1 kg/m3 = 1000 mg/L. Resistivity ohm*m -> ohm*cm multiplies by100.
    assert (1e-3/(1e-3))==1.
    slower=tuple(s.model_copy(update={'electrical_mobility_m2_V_s':s.electrical_mobility_m2_V_s/2}) for s in q.species)
    c=observe_ionic_water_conductivity(q.model_copy(update={'species':slower}))
    assert c.resistivity_ohm_m==pytest.approx(a.resistivity_ohm_m*2)


def test_neutral_solute_does_not_create_carrier_current():
    q=request(); items=tuple(s.model_copy(update={'dissolved_mass_fraction':s.dissolved_mass_fraction/2}) for s in q.species)
    neutral=DissolvedSpecies(species_id='neutral',dissolved_mass_fraction=.5,molar_mass_kg_mol=.1,charge_number=0,electrical_mobility_m2_V_s=None)
    a=observe_ionic_water_conductivity(q);b=observe_ionic_water_conductivity(q.model_copy(update={'species':(*items,neutral)}))
    assert b.conductivity_S_m==pytest.approx(a.conductivity_S_m/2)
    assert b.species[-1].conductivity_S_m==0


@pytest.mark.parametrize('background',[0.,5e-6])
def test_zero_dissolved_inventory_background_and_no_infinity_serialization(background):
    q=request().model_copy(update={'dissolved_solids_kg_m3':0.,'background_conductivity_S_m':background})
    r=observe_ionic_water_conductivity(q)
    assert r.status=='computed' and r.conductivity_S_m==background
    assert r.resistivity_ohm_m==(1/background if background else None)
    assert 'Infinity' not in r.model_dump_json()


@pytest.mark.parametrize('update', [dict(analysis_id=None),dict(transport_coefficient_id=None),
    dict(temperature_K=None),dict(background_conductivity_S_m=None),
    dict(ionic_analysis_and_transport_domain_reviewed=False),dict(species=())])
def test_missing_analysis_state_or_review_is_not_universal_fit(update):
    assert observe_ionic_water_conductivity(request().model_copy(update=update)).status=='needs_data'


def test_unknown_active_mobility_preserves_unknown():
    q=request();items=(q.species[0].model_copy(update={'electrical_mobility_m2_V_s':None}),q.species[1])
    assert observe_ionic_water_conductivity(q.model_copy(update={'species':items})).status=='needs_data'


@pytest.mark.parametrize('update',[dict(coefficient_temperature_K=300.),dict(coefficient_pressure_Pa_abs=2e5)])
def test_state_shift_is_not_silently_corrected(update):
    assert observe_ionic_water_conductivity(request().model_copy(update=update)).status=='out_of_scope'


def test_non_neutral_missing_counterion_rejected():
    q=request(); items=(q.species[0].model_copy(update={'charge_number':2}),q.species[1])
    r=observe_ionic_water_conductivity(q.model_copy(update={'species':items}))
    assert r.status=='out_of_scope' and r.conductivity_S_m is None


@pytest.mark.parametrize('kind',['duplicate','fractions','nan','negative','fractional_charge'])
def test_strict_input_contract(kind):
    q=request()
    if kind=='duplicate':update={'species':(q.species[0],q.species[0])}
    elif kind=='fractions':update={'species':tuple(s.model_copy(update={'dissolved_mass_fraction':.2}) for s in q.species)}
    elif kind=='nan':update={'electric_field_V_m':float('nan')}
    elif kind=='negative':update={'dissolved_solids_kg_m3':-.1}
    else:update={'species':(q.species[0].model_copy(update={'charge_number':1.5}),q.species[1])}
    with pytest.raises((ValueError,ValidationError)):
        observe_ionic_water_conductivity(q.model_copy(update=update))
