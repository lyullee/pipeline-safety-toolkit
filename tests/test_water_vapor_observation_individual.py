"""Independent phase/Clapeyron checks and high-precision dry-carrier ledgers."""
import math
from decimal import Decimal,localcontext
import pytest
from scipy.integrate import quad
from CoolProp.CoolProp import PropsSI
from CoolProp.HumidAirProp import HAProps_Aux
from pipeline_integrity.water_vapor_observation import (WaterVaporObservationRequest,observe_water_vapor,
    observe_water_saturation,moisture_gas_basis_state)
from pipeline_integrity.thermo import CoolPropProvider


def request(**changes):
    d=dict(gas_temperature_K=320,total_pressure_Pa_abs=1e5,humidity_reference_phase='liquid_water',
        condensation_reference_phase='liquid_water',relative_humidity_fraction=.5,
        condensation_temperature_bracket_K=(273.16,330),property_basis_id='reviewed-ideal-partial-pressure',
        ideal_partial_pressure_equilibrium_applicability_reviewed=True,ideal_mixture_pressure_support_Pa_abs=(1,2e5))
    d.update(changes);return WaterVaporObservationRequest(**d)


def run(**changes):return observe_water_vapor(request(**changes))


@pytest.mark.parametrize('t',[280.,310.,350.])
def test_liquid_saturation_slope_matches_independent_clausius_clapeyron(t):
    dt=.002
    a=observe_water_saturation(t-dt,'liquid_water').equilibrium_vapor_pressure_Pa_abs
    b=observe_water_saturation(t+dt,'liquid_water').equilibrium_vapor_pressure_Pa_abs
    latent=PropsSI('Hmass','T',t,'Q',1,'HEOS::Water')-PropsSI('Hmass','T',t,'Q',0,'HEOS::Water')
    dv=1/PropsSI('Dmass','T',t,'Q',1,'HEOS::Water')-1/PropsSI('Dmass','T',t,'Q',0,'HEOS::Water')
    assert (b-a)/(2*dt)==pytest.approx(latent/(t*dv),rel=2e-7)


@pytest.mark.parametrize('t',[250.,260.,270.])
def test_ice_sublimation_slope_matches_ice_vapor_latent_heat(t):
    dt=.002;p=observe_water_saturation(t,'ice_Ih').equilibrium_vapor_pressure_Pa_abs
    hi,unit=HAProps_Aux('h_Ice',t,p,0);rhoi,runit=HAProps_Aux('rho_Ice',t,p,0)
    assert unit=='J/kg' and runit=='kg/m^3'
    hv=PropsSI('Hmass','T',t,'P|gas',p,'HEOS::Water');rhov=PropsSI('Dmass','T',t,'P|gas',p,'HEOS::Water')
    a=observe_water_saturation(t-dt,'ice_Ih').equilibrium_vapor_pressure_Pa_abs
    b=observe_water_saturation(t+dt,'ice_Ih').equilibrium_vapor_pressure_Pa_abs
    assert (b-a)/(2*dt)==pytest.approx((hv-hi)/(t*(1/rhov-1/rhoi)),rel=6e-4)


def test_liquid_pressure_change_from_integrated_latent_heat_not_inverse_roundtrip():
    lo,hi=285.,325.
    def slope(t):
        h=PropsSI('Hmass','T',t,'Q',1,'Water')-PropsSI('Hmass','T',t,'Q',0,'Water')
        dv=1/PropsSI('Dmass','T',t,'Q',1,'Water')-1/PropsSI('Dmass','T',t,'Q',0,'Water')
        return h/(t*dv)
    dp=quad(slope,lo,hi,epsabs=1e-7,epsrel=1e-10)[0]
    a=observe_water_saturation(lo,'liquid_water');b=observe_water_saturation(hi,'liquid_water')
    assert b.equilibrium_vapor_pressure_Pa_abs-a.equilibrium_vapor_pressure_Pa_abs==pytest.approx(dp,rel=1e-9)


def test_triple_reference_and_named_phase_seam_not_a_single_unlabelled_curve():
    a=observe_water_saturation(273.16,'liquid_water');b=observe_water_saturation(273.16,'ice_Ih')
    assert a.equilibrium_vapor_pressure_Pa_abs==pytest.approx(611.657,rel=1e-5)
    assert b.equilibrium_vapor_pressure_Pa_abs==pytest.approx(611.657,rel=1e-10)
    assert a.reference_phase!=b.reference_phase


@pytest.mark.parametrize('phase,t,bracket',[('liquid_water',300.,(273.16,320.)),('ice_Ih',250.,(220.,273.16))])
def test_partial_pressure_RH_mole_fraction_and_condensation_coordinates(phase,t,bracket):
    gas_t=320 if phase=='liquid_water' else 260
    point=observe_water_saturation(t,phase)
    out=run(gas_temperature_K=gas_t,humidity_reference_phase=phase,condensation_reference_phase=phase,
        relative_humidity_fraction=None,water_partial_pressure_Pa_abs=point.equilibrium_vapor_pressure_Pa_abs,
        condensation_temperature_bracket_K=bracket)
    assert out.status=='computed'
    assert out.condensation_point_temperature_K==pytest.approx(t,abs=1e-7)
    pw=out.water_partial_pressure_Pa_abs
    by_rh=run(gas_temperature_K=gas_t,humidity_reference_phase=phase,condensation_reference_phase=phase,
        relative_humidity_fraction=out.relative_humidity_fraction,condensation_temperature_bracket_K=bracket)
    by_y=run(gas_temperature_K=gas_t,humidity_reference_phase=phase,condensation_reference_phase=phase,
        relative_humidity_fraction=None,water_mole_fraction=pw/1e5,condensation_temperature_bracket_K=bracket)
    assert by_rh.water_partial_pressure_Pa_abs==pytest.approx(pw)
    assert by_y.condensation_point_temperature_K==pytest.approx(t,abs=1e-7)
    assert abs(out.condensation_pressure_residual_Pa)<1e-5


def test_RH_liquid_reference_at_warm_gas_and_separate_cold_frost_point():
    pw=observe_water_saturation(250,'ice_Ih').equilibrium_vapor_pressure_Pa_abs
    out=run(gas_temperature_K=300,relative_humidity_fraction=None,water_partial_pressure_Pa_abs=pw,
        condensation_reference_phase='ice_Ih',condensation_temperature_bracket_K=(220,273.16))
    assert out.status=='computed' and out.condensation_point_kind=='ice_frost_point'
    assert out.condensation_point_temperature_K==pytest.approx(250,abs=1e-7)
    assert out.relative_humidity_fraction==pytest.approx(pw/observe_water_saturation(300,'liquid_water').equilibrium_vapor_pressure_Pa_abs)


def test_supplied_point_forward_does_not_need_unnecessary_inverse_bracket():
    out=run(relative_humidity_fraction=None,condensation_point_temperature_K=300,condensation_temperature_bracket_K=None)
    assert out.status=='computed' and out.condensation_point_temperature_K==300


@pytest.mark.parametrize('phase,t,bracket',[('liquid_water',300.,(273.16,320.)),('ice_Ih',250.,(220.,273.16))])
def test_saturated_state_returns_phase_reference_and_same_condensation_point(phase,t,bracket):
    out=run(gas_temperature_K=t,humidity_reference_phase=phase,condensation_reference_phase=phase,
        relative_humidity_fraction=None,saturated_at_gas_temperature=True,condensation_temperature_bracket_K=bracket)
    assert out.status=='computed' and out.relative_humidity_fraction==1
    assert out.condensation_point_temperature_K==pytest.approx(t,abs=1e-7)


@pytest.mark.parametrize('basis',['mass_amount_kg','mass_flow_kg_s','molar_amount_mol','molar_flow_mol_s'])
def test_wet_dry_mass_and_mole_ledgers_against_independent_decimal_conservation(basis):
    amount=17.;md=.04401;pw=5000.;p=1e5;mw=PropsSI('molar_mass','Water')
    out=run(relative_humidity_fraction=None,water_partial_pressure_Pa_abs=pw,dry_quantity=amount,
        dry_quantity_basis=basis,dry_molar_mass_kg_mol=md,quantity_clock_id='explicit-clock',
        dry_carrier_excludes_water_and_molar_mass_basis_reviewed=True)
    with localcontext() as ctx:
        ctx.prec=50;D=Decimal
        nd=D(str(amount))/D(str(md)) if basis.startswith('mass') else D(str(amount))
        nw=nd*D(str(pw))/(D(str(p))-D(str(pw)))
        expected_mass=nd*D(str(md))+nw*D(str(mw))
    assert out.status=='computed'
    assert out.water_molar_quantity==pytest.approx(float(nw),rel=3e-15)
    assert out.wet_mass_quantity==pytest.approx(float(expected_mass),rel=3e-15)
    assert out.wet_molar_quantity==pytest.approx(float(nd+nw),rel=3e-15)
    assert out.wet_composition.mole_fractions['Water']==pytest.approx(.05)
    assert abs(out.mass_quantity_residual)<1e-13
    assert out.mass_unit==('kg/s' if basis.endswith('_s') else 'kg')
    assert out.molar_unit==('mol/s' if basis.endswith('_s') else 'mol')
    assert out.quantity_clock_id=='explicit-clock'


@pytest.mark.parametrize('scale',[1e-5,30.])
def test_dry_carrier_extensivity_preserves_humidity_and_clock(scale):
    data=dict(dry_quantity=1.,dry_molar_mass_kg_mol=.028,quantity_clock_id='same-clock',dry_carrier_excludes_water_and_molar_mass_basis_reviewed=True)
    a=run(**data);data['dry_quantity']=scale;b=run(**data)
    assert b.wet_mass_quantity==pytest.approx(a.wet_mass_quantity*scale)
    assert b.water_molar_quantity==pytest.approx(a.water_molar_quantity*scale)
    assert b.water_vapor_mole_fraction==a.water_vapor_mole_fraction


def test_zero_dry_quantity_has_zero_amounts_without_fake_zero_molar_mass():
    out=run(dry_quantity=0,dry_molar_mass_kg_mol=.028,quantity_clock_id='zero-clock',dry_carrier_excludes_water_and_molar_mass_basis_reviewed=True)
    assert out.status=='computed'
    assert out.wet_mass_quantity==out.water_molar_quantity==0
    assert out.wet_composition.mixture_molar_mass_kg_mol>0


def test_exact_zero_water_has_no_finite_dewpoint_and_no_added_water():
    out=run(relative_humidity_fraction=0,dry_quantity=2,dry_molar_mass_kg_mol=.028,
        quantity_clock_id='clock',dry_carrier_excludes_water_and_molar_mass_basis_reviewed=True)
    assert out.status=='computed' and out.condensation_point_status=='dry_no_finite_point'
    assert out.condensation_point_temperature_K is None
    assert out.wet_mass_quantity==out.dry_mass_quantity==2 and out.water_mass_quantity==0


def test_computed_water_fraction_reuses_existing_reference_volume_owner_and_mass_ledger():
    from pipeline_integrity.gas_basis import GasBasisRequest,GasBasisState,REVIEWS,evaluate_gas_basis
    from pipeline_integrity.gas_meter import GasComponent
    from pipeline_integrity.contracts import EvidenceRef,RunContext,digest
    out=run(dry_quantity=17,dry_molar_mass_kg_mol=.04401,quantity_clock_id='clock',dry_carrier_excludes_water_and_molar_mass_basis_reviewed=True)
    wet=moisture_gas_basis_state(out,id='wet',state_evidence_id='proof',water_fraction_evidence_id='proof',
        gas_phase_evidence_id='proof',single_gas_state_reviewed=True,compressibility_Z_common_R=1.,compressibility_evidence_id='proof')
    dry=GasBasisState(id='dry',pressure_pa_abs=1e5,temperature_K=320,water_vapor_mole_fraction=0,volume_basis='dry_gas',
        state_evidence_id='proof',water_fraction_evidence_id='proof',gas_phase_evidence_id='proof',compressibility_Z_common_R=1,compressibility_evidence_id='proof')
    from pipeline_integrity.gas_basis import R_SI_J_MOL_K
    volume=out.dry_molar_quantity*R_SI_J_MOL_K*320/1e5
    q=GasBasisRequest(id='conversion',asset_id='asset',clock_id='clock',event_id='point',time_s=0,
        quantity_kind='volume_flow',source_value=volume,dry_composition_id='dry-CO2',
        dry_components=(GasComponent(fluid='CarbonDioxide',mole_fraction=1.),),composition_evidence_id='proof',
        source=dry,target=wet,Z_mode='supplied',reviews=dict.fromkeys(REVIEWS,True),review_evidence_ids=dict.fromkeys(REVIEWS,'proof'),evidence_ids=('proof',))
    context=RunContext(asset_id='asset',scenario_id='parameterized-observation',clock_id='clock',origin='synthetic',
        evidence=(EvidenceRef(id='proof',citation='Authored ideal-model coordinate and same-state ledger fixture, no field claim',origin='synthetic',sha256=digest('ideal-defined-coordinates')),))
    converted=evaluate_gas_basis(q,context)
    assert converted['status']=='computed'
    assert converted['molar_ledger']['target_water_vapor_quantity']==pytest.approx(out.water_molar_quantity)
    assert converted['molar_ledger']['target_total_molar_quantity']==pytest.approx(out.wet_molar_quantity)
    assert converted['values']['target_value']/volume==pytest.approx(1/(1-out.water_vapor_mole_fraction))


@pytest.mark.parametrize('changes',[{'gas_temperature_K':None},{'total_pressure_Pa_abs':None},
    {'relative_humidity_fraction':None},{'ideal_partial_pressure_equilibrium_applicability_reviewed':False},
    {'ideal_mixture_pressure_support_Pa_abs':None}])
def test_unknown_state_coordinate_and_ideal_domain_are_withheld(changes):
    assert run(**changes).status=='needs_data'


@pytest.mark.parametrize('changes',[{'quantity_clock_id':None},{'dry_molar_mass_kg_mol':None},{'dry_carrier_excludes_water_and_molar_mass_basis_reviewed':False}])
def test_unknown_quantity_clock_or_dry_molar_basis_never_invented(changes):
    d=dict(dry_quantity=1,dry_molar_mass_kg_mol=.028,quantity_clock_id='clock',dry_carrier_excludes_water_and_molar_mass_basis_reviewed=True);d.update(changes)
    out=run(**d)
    assert out.status=='needs_data' and out.wet_mass_quantity is None


def test_zero_point_bracket_and_iteration_failure_are_distinct():
    assert run(condensation_temperature_bracket_K=None).status=='needs_data'
    assert run(condensation_temperature_bracket_K=(273.16,280)).status=='not_bracketed'
    assert run(maximum_root_iterations=1).status=='not_converged'


@pytest.mark.parametrize('phase,t',[('liquid_water',250.),('liquid_water',647.096),('ice_Ih',300.),('ice_Ih',49.)])
def test_phase_specific_native_support_rejects_extrapolation(phase,t):
    with pytest.raises(ValueError):observe_water_saturation(t,phase)


def test_pure_vapor_pressure_is_not_total_vacuum_pressure_without_composition():
    out=run(relative_humidity_fraction=None,water_mole_fraction=.1,total_pressure_Pa_abs=1000,
        condensation_reference_phase='ice_Ih',condensation_temperature_bracket_K=(220,273.16))
    assert out.status=='computed' and out.water_partial_pressure_Pa_abs==100
    assert out.water_partial_pressure_Pa_abs!=out.total_pressure_Pa_abs


@pytest.mark.parametrize('changes',[{'water_partial_pressure_Pa_abs':20000.},
    {'total_pressure_Pa_abs':1000.,'saturated_at_gas_temperature':True},
    {'total_pressure_Pa_abs':3e5,'water_partial_pressure_Pa_abs':1000.}])
def test_supersaturated_pure_carrier_or_nonideal_support_are_not_inferred(changes):
    assert run(relative_humidity_fraction=None,**changes).status=='out_of_scope'


def test_bad_temperature_provider_is_not_accepted_as_saturation_boundary():
    class Bad:
        def resolve(self,q):return CoolPropProvider(use_cache=False).resolve(q).model_copy(update={'temperature_k':321.})
    assert observe_water_vapor(request(),property_provider=Bad()).status=='out_of_scope'


def test_inconsistent_redundant_moisture_coordinates_are_rejected():
    with pytest.raises(ValueError):request(water_mole_fraction=.1)


def test_gas_basis_binding_requires_its_own_phase_review():
    out=run()
    with pytest.raises(ValueError):moisture_gas_basis_state(out,id='wet',state_evidence_id='p',water_fraction_evidence_id='p',gas_phase_evidence_id='p',single_gas_state_reviewed=False)
