"""Reaction-product energy and reference-volume/water-transfer conservation."""
import math
import pytest
from pipeline_integrity.contracts import EvidenceRef,RunContext
from pipeline_integrity.gas_basis import GasBasisState,GasBasisRequest,REVIEWS
from pipeline_integrity.gas_meter import GasComponent
from pipeline_integrity.gas_combustion_reference import (
    ComponentCombustionDatum as Datum,GasCombustionReferenceRequest as Request,observe_gas_combustion_reference)


R=8.31446261815324
M={'Methane':.016,'Hydrogen':.002,'Nitrogen':.028}
# Authored thermochemical fixture, not measured property values or a copied table.
FORMATION={'Methane':-70000.,'Hydrogen':0.,'Nitrogen':0.,'Oxygen':0.,
           'CarbonDioxide':-400000.,'Water_liquid':-290000.,'Water_vapor':-245000.}


def state(name,p=1e5,t=300.,water=0.,z=1.):
    return GasBasisState(id=name,pressure_pa_abs=p,temperature_K=t,water_vapor_mole_fraction=water,
        volume_basis='wet_gas' if water else 'dry_gas',compressibility_Z_common_R=z,
        state_evidence_id='basis',water_fraction_evidence_id='basis',gas_phase_evidence_id='basis',compressibility_evidence_id='basis')


def request(*,source=None,target=None,quantity=2.,kind='volume',fractions=(.6,.2,.2),basis='gross_liquid_product_water'):
    s=source or state('source');t=target or state('target',1.2e5,330.,.08,.96)
    context=RunContext(asset_id='gas',scenario_id='combustion-coordinate',clock_id='snapshot',origin='synthetic',
        evidence=(EvidenceRef(id='basis',citation='Authored fixed-composition thermochemical fixture and explicit gas reference states',origin='synthetic',sha256='a'*64),))
    gas=GasBasisRequest(id='reference',asset_id='gas',clock_id='snapshot',event_id='sample',time_s=0.,quantity_kind=kind,
        source_value=quantity,dry_composition_id='authored-fuel',
        dry_components=tuple(GasComponent(fluid=k,mole_fraction=x) for k,x in zip(M,fractions) if x>0),
        composition_evidence_id='basis',source=s,target=t,Z_mode='supplied',
        reviews=dict.fromkeys(REVIEWS,True),review_evidence_ids=dict.fromkeys(REVIEWS,'basis'),evidence_ids=('basis',))
    water_product='Water_liquid' if basis=='gross_liquid_product_water' else 'Water_vapor'
    heat={
        'Methane':FORMATION['Methane']+2*FORMATION['Oxygen']-FORMATION['CarbonDioxide']-2*FORMATION[water_product],
        'Hydrogen':FORMATION['Hydrogen']+.5*FORMATION['Oxygen']-FORMATION[water_product],
        'Nitrogen':0.}
    data=tuple(Datum(fluid=k,molar_mass_kg_mol=M[k],released_combustion_energy_J_mol=heat[k],combustion_reference_id='thermochemical-fixture') for k,x in zip(M,fractions) if x>0)
    q=Request(gas_basis=gas,component_data=data,calorific_basis=basis,combustion_reference_id='thermochemical-fixture',
        combustion_reference_temperature_K=298.15,combustion_reference_pressure_Pa_abs=1e5,vapor_water_molar_mass_kg_mol=.018,
        supplied_component_combustion_data_additivity_and_reference_reviewed=True)
    return q,context


@pytest.mark.parametrize('basis',['gross_liquid_product_water','net_vapor_product_water'])
@pytest.mark.parametrize('fractions',[(.6,.2,.2),(1.,0.,0.),(0.,.4,.6)])
def test_independent_complete_reaction_product_energy_and_dry_mass(basis,fractions):
    q,c=request(fractions=fractions,basis=basis);a=observe_gas_combustion_reference(q,c)
    dry_moles=q.gas_basis.source_value*1e5/(R*300.)
    methane,hydrogen,nitrogen=[dry_moles*x for x in fractions]
    oxygen=2*methane+.5*hydrogen
    # Count actual atom-balanced reactants/products before evaluating any heat.
    product_CO2=methane;product_water=2*methane+hydrogen
    assert 4*methane+2*hydrogen==pytest.approx(2*product_water,rel=2e-14)
    assert 2*oxygen==pytest.approx(2*product_CO2+product_water,rel=2e-14)
    assert .016*methane+.002*hydrogen+.028*nitrogen+.032*oxygen==pytest.approx(
        .044*product_CO2+.018*product_water+.028*nitrogen,rel=2e-14)
    product='Water_liquid' if basis=='gross_liquid_product_water' else 'Water_vapor'
    reactants=methane*FORMATION['Methane']+hydrogen*FORMATION['Hydrogen']+nitrogen*FORMATION['Nitrogen']+oxygen*FORMATION['Oxygen']
    products=product_CO2*FORMATION['CarbonDioxide']+product_water*FORMATION[product]+nitrogen*FORMATION['Nitrogen']
    released=reactants-products
    fuel_mass=methane*M['Methane']+hydrogen*M['Hydrogen']+nitrogen*M['Nitrogen']
    assert a.status=='computed'
    assert a.source_combustion_energy_quantity==pytest.approx(released,rel=3e-14)
    assert a.target_combustion_energy_quantity==pytest.approx(released,rel=3e-14)
    assert a.dry_molar_combustion_energy_J_mol==pytest.approx(released/dry_moles,rel=3e-14)
    assert a.dry_mass_combustion_energy_J_kg==pytest.approx(released/fuel_mass,rel=3e-14)


@pytest.mark.parametrize('ys,yt',[(0.,.1),(.1,0.),(.04,.16),(.2,.2)])
def test_wet_dry_energy_is_invariant_but_mass_changes_only_by_water(ys,yt):
    q,c=request(source=state('source',9e4,310.,ys,1.02),target=state('target',1.4e5,345.,yt,.93))
    a=observe_gas_combustion_reference(q,c)
    nsource=2*9e4/(1.02*R*310.);ndry=nsource*(1-ys);ntarget=ndry/(1-yt)
    hdry=.6*910000+.2*290000
    assert a.source_volumetric_combustion_energy_J_m3==pytest.approx(9e4/(1.02*R*310)*(1-ys)*hdry,rel=3e-14)
    assert a.target_volumetric_combustion_energy_J_m3==pytest.approx(1.4e5/(.93*R*345)*(1-yt)*hdry,rel=3e-14)
    assert a.target_combustion_energy_quantity==pytest.approx(ndry*hdry,rel=3e-14)
    assert a.water_mass_to_gas_quantity==pytest.approx((ntarget*yt-nsource*ys)*.018,abs=1e-14)
    assert abs(a.mass_reference_conversion_residual)<1e-14
    assert abs(a.energy_reference_conversion_residual)<1e-7
    assert a.source_mass_combustion_energy_J_kg==pytest.approx(ndry*hdry/a.source_mass_quantity,rel=3e-14)


def test_Z_correction_is_molar_density_not_a_new_heating_fit():
    qa,c=request(source=state('source'),target=state('target'))
    qb=qa.model_copy(update={'gas_basis':qa.gas_basis.model_copy(update={'source':state('source',z=.8),'target':state('target',z=.8)})})
    a=observe_gas_combustion_reference(qa,c);b=observe_gas_combustion_reference(qb,c)
    assert b.dry_molar_combustion_energy_J_mol==a.dry_molar_combustion_energy_J_mol
    assert b.source_volumetric_combustion_energy_J_m3==pytest.approx(a.source_volumetric_combustion_energy_J_m3/.8)


def test_existing_native_gas_reference_backend_reaches_independent_dilute_energy_density_limit():
    from CoolProp.CoolProp import PropsSI
    from pipeline_integrity.gas_basis import gas_basis_backend_identity
    q,c=request(source=state('source',100.,300.),target=state('target',200.,350.),fractions=(1.,0.,0.))
    def native(s):return s.model_copy(update={'compressibility_Z_common_R':None,'compressibility_evidence_id':None})
    gas=q.gas_basis.model_copy(update={'source':native(q.gas_basis.source),'target':native(q.gas_basis.target),
        'Z_mode':'coolprop','coolprop_pin':gas_basis_backend_identity()})
    datum=q.component_data[0].model_copy(update={'molar_mass_kg_mol':PropsSI('molar_mass','Methane')})
    a=observe_gas_combustion_reference(q.model_copy(update={'gas_basis':gas,'component_data':(datum,)}),c)
    assert a.status=='computed'
    assert a.source_volumetric_combustion_energy_J_m3==pytest.approx(100/(R*300)*910000,rel=3e-5)
    assert a.target_volumetric_combustion_energy_J_m3==pytest.approx(200/(R*350)*910000,rel=3e-5)
    assert a.target_combustion_energy_quantity==pytest.approx(a.source_combustion_energy_quantity,rel=3e-14)
    assert a.gas_basis_receipt['gas_phase_check']=='native_single_gas_phase_at_both_explicit_compositions'


def test_gross_minus_net_equals_independent_stoichiometric_product_water_latent_energy():
    gross,c=request();net,_=request(basis='net_vapor_product_water')
    a=observe_gas_combustion_reference(gross,c);b=observe_gas_combustion_reference(net,c)
    assert a.dry_molar_combustion_energy_J_mol-b.dry_molar_combustion_energy_J_mol==pytest.approx((2*.6+.2)*45000)
    assert a.sensible_heat_or_initial_water_condensation_energy_inferred is False


@pytest.mark.parametrize('scale',[0.,.001,50.])
def test_quantity_extensivity_retains_energy_density_and_J_versus_W(scale):
    q,c=request();base=observe_gas_combustion_reference(q,c)
    flow=observe_gas_combustion_reference(q.model_copy(update={'gas_basis':q.gas_basis.model_copy(update={'source_value':2*scale,'quantity_kind':'volume_flow'})}),c)
    assert flow.energy_quantity_unit=='W' and flow.mass_quantity_unit=='kg/s'
    assert base.energy_quantity_unit=='J'
    assert flow.source_combustion_energy_quantity==pytest.approx(base.source_combustion_energy_quantity*scale)
    assert flow.target_volumetric_combustion_energy_J_m3==base.target_volumetric_combustion_energy_J_m3


def test_all_inert_zero_is_explicit_not_unknown_combustion_data():
    q,c=request(fractions=(0.,0.,1.));a=observe_gas_combustion_reference(q,c)
    assert a.status=='computed' and a.source_combustion_energy_quantity==a.target_combustion_energy_quantity==0


def test_saturated_water_fraction_is_supplied_by_existing_moisture_owner_not_a_fixed_factor():
    from pipeline_integrity.water_vapor_observation import observe_water_saturation
    pw=observe_water_saturation(300.,'liquid_water').equilibrium_vapor_pressure_Pa_abs
    dry,c=request(source=state('source'),target=state('target'))
    wet,_=request(source=state('source'),target=state('target',water=pw/1e5))
    a=observe_gas_combustion_reference(dry,c);b=observe_gas_combustion_reference(wet,c)
    assert b.target_volumetric_combustion_energy_J_m3/a.target_volumetric_combustion_energy_J_m3==pytest.approx(1-pw/1e5)


@pytest.mark.parametrize('field',['released_combustion_energy_J_mol','molar_mass_kg_mol'])
def test_unknown_component_data_not_inferred_from_sensible_EOS_or_zero_fraction(field):
    q,c=request();data=list(q.component_data);data[0]=data[0].model_copy(update={field:None})
    a=observe_gas_combustion_reference(q.model_copy(update={'component_data':tuple(data)}),c)
    assert a.status=='needs_data' and a.source_combustion_energy_quantity is None


def test_missing_review_or_wet_mass_basis_is_withheld():
    q,c=request()
    assert observe_gas_combustion_reference(q.model_copy(update={'supplied_component_combustion_data_additivity_and_reference_reviewed':False}),c).status=='needs_data'
    assert observe_gas_combustion_reference(q.model_copy(update={'vapor_water_molar_mass_kg_mol':None}),c).status=='needs_data'


def test_inconsistent_combustion_reference_and_component_identity_rejected():
    q,c=request();data=list(q.component_data);data[0]=data[0].model_copy(update={'combustion_reference_id':'different-products'})
    with pytest.raises(ValueError):observe_gas_combustion_reference(q.model_copy(update={'component_data':tuple(data)}),c)
    with pytest.raises(ValueError):observe_gas_combustion_reference(q.model_copy(update={'component_data':q.component_data[:-1]}),c)


def test_gas_reference_reviews_and_asset_clock_are_preserved():
    q,c=request();reviews=dict(q.gas_basis.reviews);reviews[REVIEWS[0]]=False
    assert observe_gas_combustion_reference(q.model_copy(update={'gas_basis':q.gas_basis.model_copy(update={'reviews':reviews})}),c).status=='needs_data'
    with pytest.raises(ValueError):observe_gas_combustion_reference(q,c.model_copy(update={'clock_id':'wrong'}))
