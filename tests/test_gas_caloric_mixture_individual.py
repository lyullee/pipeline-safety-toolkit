"""Independent mole/mass and constant-pressure parcel enthalpy ledgers."""
import pytest
from scipy.integrate import quad
from pipeline_integrity.gas_caloric_mixture import (
    GasCaloricComponent,GasCaloricMixtureRequest,evaluate_gas_caloric_mixture,R_UNIVERSAL_J_MOL_K,
    GasCaloricAmountComponent,GasCaloricAmountsRequest,gas_caloric_mixture_from_amounts)


def component(name,fraction,mass,cp=None,t=None):
    return GasCaloricComponent(id=name,fraction=fraction,molar_mass_kg_mol=mass,
        ideal_gas_molar_cp_J_mol_K=cp,heat_capacity_temperature_K=t)


def request(**changes):
    d=dict(mode='ideal_gas_caloric',fraction_basis='mole',temperature_K=350.,property_basis_id='synthetic-components',
        components=(component('a',.2,.002,25.,350.),component('b',.3,.028,30.,350.),component('c',.5,.044,45.,350.)),
        nonreacting_component_composition_ledger_reviewed=True,ideal_gas_molar_heat_capacity_additivity_reviewed=True)
    d.update(changes);return GasCaloricMixtureRequest(**d)


@pytest.mark.parametrize('total_moles',[.03,19.,1300.])
def test_weighed_component_mass_and_parcel_heat_ledgers(total_moles):
    q=request();r=evaluate_gas_caloric_mixture(q)
    measured_mass=sum(total_moles*c.fraction*c.molar_mass_kg_mol for c in q.components)
    heat=sum(total_moles*c.fraction*quad(lambda t:c.ideal_gas_molar_cp_J_mol_K,350,370)[0] for c in q.components)
    assert r.status=='computed'
    assert r.mixture_molar_mass_kg_mol==pytest.approx(measured_mass/total_moles)
    assert r.ideal_gas_mass_cp_J_kg_K==pytest.approx(heat/(measured_mass*20.))
    assert r.ideal_gas_molar_cp_J_mol_K==pytest.approx(heat/(total_moles*20.))
    # Constant-pressure expansion work supplies the Cp-Cv difference per mole.
    expansion_work=total_moles*R_UNIVERSAL_J_MOL_K*20.
    assert r.ideal_gas_molar_cv_J_mol_K==pytest.approx((heat-expansion_work)/(total_moles*20.))


def test_mass_fraction_basis_reconstructs_independently_counted_moles():
    # Independent container measurements: counts in moles and their weighed masses.
    counts=(2.,3.,5.);masses=(.002,.028,.044);cps=(25.,30.,45.)
    weights=tuple(n*m for n,m in zip(counts,masses));total=sum(weights)
    components=tuple(component(str(i),w/total,m,cp,350.) for i,(w,m,cp) in enumerate(zip(weights,masses,cps)))
    r=evaluate_gas_caloric_mixture(request(fraction_basis='mass',components=components))
    assert r.status=='computed'
    assert r.mixture_molar_mass_kg_mol==pytest.approx(total/sum(counts))
    assert tuple(r.mole_fractions.values())==pytest.approx((.2,.3,.5))
    assert tuple(r.mass_fractions.values())==pytest.approx(tuple(w/total for w in weights))


def test_molar_mass_only_needs_no_viscosity_or_heat_capacity():
    r=evaluate_gas_caloric_mixture(request(mode='molar_mass_only',components=(component('one',.7,.028),component('two',.3,.044))))
    assert r.status=='computed' and r.mixture_molar_mass_kg_mol==pytest.approx(.7*.028+.3*.044)
    assert r.ideal_gas_molar_cp_J_mol_K is None


def test_zero_fraction_unknown_species_is_retained_not_guessed():
    q=request();r=evaluate_gas_caloric_mixture(request(components=(*q.components,component('inactive',0,None))))
    assert r.status=='computed' and r.mole_fractions['inactive']==r.mass_fractions['inactive']==0


@pytest.mark.parametrize('cp',[20.,80.])
def test_single_component_limit(cp):
    r=evaluate_gas_caloric_mixture(request(components=(component('single',1.,.02,cp,350.),)))
    assert r.status=='computed' and r.ideal_gas_molar_cp_J_mol_K==cp
    assert r.ideal_gas_heat_capacity_ratio==pytest.approx(cp/(cp-R_UNIVERSAL_J_MOL_K))


@pytest.mark.parametrize('change',[dict(components=(component('active',1,None),)),
    dict(nonreacting_component_composition_ledger_reviewed=False)])
def test_unknown_composition_property_not_a_fake_viscosity_argument(change):
    r=evaluate_gas_caloric_mixture(request(**change));assert r.status=='needs_data' and r.mixture_molar_mass_kg_mol is None


@pytest.mark.parametrize('change',[dict(components=(component('a',1.,.028),)),
    dict(components=(component('a',1.,.028,30.,None),)),
    dict(ideal_gas_molar_heat_capacity_additivity_reviewed=False)])
def test_unknown_caloric_closure_retains_known_molar_mass(change):
    r=evaluate_gas_caloric_mixture(request(**change));assert r.status=='needs_data'
    assert r.mixture_molar_mass_kg_mol is not None and r.ideal_gas_molar_cp_J_mol_K is None


def test_different_component_temperature_not_averaged():
    with pytest.raises(ValueError):evaluate_gas_caloric_mixture(request(components=(component('a',1,.028,30.,400.),)))


def test_nonphysical_ideal_gas_cv_not_clipped():
    r=evaluate_gas_caloric_mixture(request(components=(component('a',1,.028,5.,350.),)))
    assert r.status=='out_of_scope' and r.ideal_gas_molar_cp_J_mol_K is None


@pytest.mark.parametrize('components',[(component('a',2,.028),),(component('a',0,.028),),
    (component('same',.5,.028),component('same',.5,.044))])
def test_weights_or_duplicate_identity_not_silently_normalized(components):
    with pytest.raises(ValueError):request(components=components)


@pytest.mark.parametrize('basis',['molar_amount_mol','mass_amount_kg','molar_flow_mol_s','mass_flow_kg_s'])
def test_explicit_absolute_quantity_adapter_reuses_same_composition(basis):
    counts=(2.,3.,5.);masses=(.002,.028,.044);cps=(25.,30.,45.)
    quantities=tuple(n*m if basis.startswith('mass') else n for n,m in zip(counts,masses))
    components=tuple(GasCaloricAmountComponent(id=str(i),input_quantity=n,molar_mass_kg_mol=m,
        ideal_gas_molar_cp_J_mol_K=cp,heat_capacity_temperature_K=350.) for i,(n,m,cp) in enumerate(zip(quantities,masses,cps)))
    r=gas_caloric_mixture_from_amounts(GasCaloricAmountsRequest(mode='ideal_gas_caloric',quantity_basis=basis,
        components=components,common_clock_id='shared-observation-clock',temperature_K=350.,property_basis_id='synthetic-components',
        nonreacting_component_composition_ledger_reviewed=True,ideal_gas_molar_heat_capacity_additivity_reviewed=True))
    assert r.mixture.status=='computed' and r.total_supplied_quantity==sum(quantities)
    assert r.common_clock_id=='shared-observation-clock' and r.quantity_basis==basis
    assert tuple(r.mixture.mole_fractions.values())==pytest.approx((.2,.3,.5))
    assert r.mixture.mixture_molar_mass_kg_mol==pytest.approx(sum(n*m for n,m in zip(counts,masses))/sum(counts))


def test_zero_absolute_quantity_has_no_composition():
    with pytest.raises(ValueError):gas_caloric_mixture_from_amounts(GasCaloricAmountsRequest(mode='molar_mass_only',
        quantity_basis='molar_flow_mol_s',components=(GasCaloricAmountComponent(id='empty',input_quantity=0,molar_mass_kg_mol=.028),),
        common_clock_id='empty',temperature_K=350.,property_basis_id='synthetic'))
