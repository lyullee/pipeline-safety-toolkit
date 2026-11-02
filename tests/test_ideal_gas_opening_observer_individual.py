"""Independent sonic continuity/energy maxima and conserved reference parcels."""
import hashlib,math
from pathlib import Path
import pytest
from pydantic import ValidationError
from scipy.optimize import minimize_scalar
from pipeline_integrity.ideal_gas_opening_observer import (
    IdealOpeningGas as Gas,IdealOpeningGeometry as Geometry,SonicPressureInverseRequest as Inverse,
    IdealOpeningFlowRequest as Forward,invert_sonic_throat_pressure as invert,
    observe_ideal_opening_flow as observe,opening_reference_gas_ledger)
from pipeline_integrity.contracts import RunContext,EvidenceRef
from pipeline_integrity.gas_basis import GasBasisState,GasBasisRequest,REVIEWS,R_SI_J_MOL_K
from pipeline_integrity.gas_meter import GasComponent


def gas(k=1.4):return Gas(composition_id='declared-dry-gas',molar_mass_kg_mol=.028,heat_capacity_ratio=k,
    property_basis_id='authored-perfect-gas',dry_calorically_perfect_gas_and_molar_mass_reviewed=True)
def geometry():return Geometry(actual_throat_area_m2=.003,discharge_coefficient=.8,
    coefficient_basis_id='supplied-Cd-not-inferred',supplied_geometry_and_coefficient_reviewed=True)
def forward(**changes):
    d=dict(gas=gas(),geometry=geometry(),upstream_stagnation_pressure_Pa_abs=8e5,
        upstream_stagnation_temperature_K=350.,back_pressure_Pa_abs=1e5,asset_id='line',clock_id='clock',event_id='opening',observation_time_s=10.)
    d.update(changes);return Forward(**d)
def inverse(**changes):
    d=dict(gas=gas(),geometry=geometry(),target_mass_flow_kg_s=1.,temperature_K=300.,
        temperature_definition='throat_static',back_pressure_Pa_abs=1e4,asset_id='line',clock_id='clock',event_id='opening',observation_time_s=10.)
    d.update(changes);return Inverse(**d)


@pytest.mark.parametrize('k',[1.1,1.4,1.67])
@pytest.mark.parametrize('basis',['throat_static','upstream_stagnation'])
def test_inverse_from_independent_sonic_density_velocity_and_total_enthalpy(k,basis):
    r=R_SI_J_MOL_K/.028;ts=280.;ps=2e5;area=.003*.8
    sound=math.sqrt(k*r*ts);rho=ps/(r*ts);mass=area*rho*sound
    cp=k*r/(k-1);t0=ts+sound**2/(2*cp);p0=ps*(t0/ts)**(k/(k-1))
    out=invert(inverse(gas=gas(k),target_mass_flow_kg_s=mass,temperature_K=ts if basis=='throat_static' else t0,temperature_definition=basis,back_pressure_Pa_abs=1e5))
    assert out.status=='computed'
    assert out.ideal_throat_static_pressure_Pa_abs==pytest.approx(ps,rel=3e-13)
    assert out.upstream_stagnation_pressure_Pa_abs==pytest.approx(p0,rel=3e-13)
    assert out.ideal_throat_static_temperature_K==pytest.approx(ts,rel=3e-13)
    assert out.ideal_throat_velocity_m_s==pytest.approx(sound,rel=3e-13)
    assert abs(out.normalized_kernel_mass_residual_kg_s)<mass*4e-13
    assert abs(out.ideal_continuity_residual_kg_s)<mass*4e-13
    assert abs(out.specific_total_enthalpy_residual_J_kg)<1e-8


@pytest.mark.parametrize('k',[1.1,1.4,1.67])
def test_forward_critical_capacity_is_independent_isentropic_energy_flux_maximum(k):
    q=forward(gas=gas(k));r=R_SI_J_MOL_K/.028;cp=k*r/(k-1);p0=q.upstream_stagnation_pressure_Pa_abs;t0=q.upstream_stagnation_temperature_K
    def flux(log_ratio):
        p=p0*math.exp(log_ratio);t=t0*math.exp((k-1)/k*log_ratio)
        return p/(r*t)*math.sqrt(2*cp*(t0-t))
    opt=minimize_scalar(lambda x:-flux(x),bounds=(-15.,-1e-10),method='bounded',options={'xatol':1e-13})
    out=observe(q)
    assert opt.success and out.regime=='choked'
    assert out.mass_flow_kg_s==pytest.approx(-opt.fun*.003*.8,rel=2e-12)
    assert out.ideal_throat_velocity_m_s==pytest.approx(math.sqrt(k*r*out.ideal_throat_static_temperature_K),rel=3e-13)


@pytest.mark.parametrize('ratio',[.7,.95,.9999])
def test_subcritical_mass_energy_and_back_pressure(ratio):
    q=forward(back_pressure_Pa_abs=8e5*ratio);out=observe(q);r=R_SI_J_MOL_K/.028;cp=1.4*r/.4
    t=350*ratio**(.4/1.4);u=math.sqrt(2*cp*(350-t));rho=8e5*ratio/(r*t)
    assert out.regime=='subcritical'
    assert out.mass_flow_kg_s==pytest.approx(.003*.8*rho*u,rel=2e-10)
    assert out.ideal_throat_static_pressure_Pa_abs==q.back_pressure_Pa_abs


def test_sonic_transition_continuity_and_no_forward_drive():
    ratio=(2/2.4)**(1.4/.4)
    a=observe(forward(back_pressure_Pa_abs=8e5*ratio*(1-1e-7)))
    b=observe(forward(back_pressure_Pa_abs=8e5*ratio*(1+1e-7)))
    assert a.mass_flow_kg_s==pytest.approx(b.mass_flow_kg_s,rel=1e-11)
    for pb in [8e5,9e5]:
        out=observe(forward(back_pressure_Pa_abs=pb));assert out.mass_flow_kg_s==0 and out.regime=='no_outflow'


def test_area_coefficient_temperature_scaling_and_duration_not_blowdown():
    a=observe(forward())
    b=observe(forward(geometry=geometry().model_copy(update={'actual_throat_area_m2':.006,'discharge_coefficient':.4}),upstream_stagnation_temperature_K=1400.))
    assert b.mass_flow_kg_s==pytest.approx(a.mass_flow_kg_s/2,rel=2e-13)
    no_amount=observe(forward(constant_boundary_duration_s=300.))
    amount=observe(forward(constant_boundary_duration_s=300.,constant_boundaries_during_interval_reviewed=True))
    assert no_amount.constant_boundary_mass_amount_kg is None and no_amount.issues
    assert amount.constant_boundary_mass_amount_kg==pytest.approx(a.mass_flow_kg_s*300)
    assert amount.constant_boundary_duration_s==300 and amount.constant_boundary_interval_end_time_s==310
    assert not amount.field_or_device_qualified


def basis_for(out,kind='volume_flow',targetp=120000.,targett=320.,targetz=.92):
    def state(name,p,t,z):return GasBasisState(id=name,pressure_pa_abs=p,temperature_K=t,
        water_vapor_mole_fraction=0,volume_basis='dry_gas',compressibility_Z_common_R=z,
        state_evidence_id='fixture',water_fraction_evidence_id='fixture',gas_phase_evidence_id='fixture',compressibility_evidence_id='fixture')
    context=RunContext(asset_id='line',scenario_id='opening',clock_id='clock',origin='synthetic',evidence=(EvidenceRef(
        id='fixture',citation='Authored constant ideal nozzle and explicit reference gas convention',origin='synthetic',sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest()),))
    q=GasBasisRequest(id='reference',asset_id='line',clock_id='clock',event_id='opening',time_s=10.,quantity_kind=kind,
        source_value=999.,dry_composition_id=out.gas_composition_id,dry_components=(GasComponent(fluid='Nitrogen',mole_fraction=1.),),
        composition_evidence_id='fixture',source=state('source',out.upstream_stagnation_pressure_Pa_abs,out.upstream_stagnation_temperature_K,1.),
        target=state('target',targetp,targett,targetz),Z_mode='supplied',reviews=dict.fromkeys(REVIEWS,True),
        review_evidence_ids=dict.fromkeys(REVIEWS,'fixture'),evidence_ids=('fixture',))
    return q,context


@pytest.mark.parametrize('kind',['volume_flow','volume'])
@pytest.mark.parametrize('targetp,targett,z',[(120000.,320.,.92),(85000.,290.,1.05)])
def test_reference_volume_and_species_moles_from_independent_weighed_parcel(kind,targetp,targett,z):
    out=observe(forward(constant_boundary_duration_s=300.,constant_boundaries_during_interval_reviewed=True))
    q,c=basis_for(out,kind,targetp,targett,z);saved=q.model_dump();record=opening_reference_gas_ledger(out,q,c)
    mass=out.mass_flow_kg_s*(300 if kind=='volume' else 1);moles=mass/.028
    assert record['status']=='computed'
    assert record['values']['target_value']==pytest.approx(moles*z*R_SI_J_MOL_K*targett/targetp,rel=3e-13)
    assert record['molar_ledger']['dry_species'][0]['source_dry_quantity']==pytest.approx(moles,rel=3e-13)
    assert record['molar_ledger']['source_total_molar_quantity']==pytest.approx(record['molar_ledger']['target_total_molar_quantity'],rel=3e-13)
    assert q.model_dump()==saved


@pytest.mark.parametrize('change',[{'clock_id':'other'},{'asset_id':'other'},{'event_id':'other'},{'time_s':11.},{'dry_composition_id':'other'}])
def test_reference_identity_guards(change):
    out=observe(forward());q,c=basis_for(out)
    with pytest.raises(ValueError,match='differs'):opening_reference_gas_ledger(out,q.model_copy(update=change),c)


def test_reference_unknown_amount_and_unlike_source_state_rejected():
    out=observe(forward());q,c=basis_for(out,'volume')
    with pytest.raises(ValueError,match='unknown'):opening_reference_gas_ledger(out,q,c)
    q,c=basis_for(out);bad=q.source.model_copy(update={'temperature_K':351.})
    with pytest.raises(ValueError,match='stagnation'):opening_reference_gas_ledger(out,q.model_copy(update={'source':bad}),c)
    bad=q.source.model_copy(update={'compressibility_Z_common_R':.9})
    with pytest.raises(ValueError,match='ideal dry'):opening_reference_gas_ledger(out,q.model_copy(update={'source':bad}),c)


@pytest.mark.parametrize('change',[{'upstream_stagnation_pressure_Pa_abs':None},{'upstream_stagnation_temperature_K':None},
    {'back_pressure_Pa_abs':None},{'gas':gas().model_copy(update={'molar_mass_kg_mol':None})},
    {'geometry':geometry().model_copy(update={'discharge_coefficient':None})},
    {'gas':gas().model_copy(update={'dry_calorically_perfect_gas_and_molar_mass_reviewed':False})}])
def test_unknown_inputs_and_unreviewed_model_are_not_zero(change):
    assert observe(forward(**change)).status=='needs_data'


def test_zero_sonic_inverse_and_incompatible_back_pressure():
    assert invert(inverse(target_mass_flow_kg_s=0)).status=='zero_flow_no_unique_sonic_pressure'
    assert invert(inverse(back_pressure_Pa_abs=1e8)).status=='not_sonic'
    assert invert(inverse(temperature_K=None)).status=='needs_data'


def test_gamma_coefficient_pressure_and_temperature_basis_contracts():
    with pytest.raises(ValidationError):gas().model_copy(update={'heat_capacity_ratio':1.}).__class__.model_validate(gas().model_copy(update={'heat_capacity_ratio':1.}).model_dump())
    with pytest.raises(ValidationError):forward(back_pressure_Pa_abs=-1000)
    with pytest.raises(ValidationError):inverse(temperature_definition='unspecified_flowing')
    with pytest.raises(ValidationError):Geometry(actual_throat_area_m2=.003,discharge_coefficient=2.)


def test_near_isothermal_gamma_is_not_silently_sent_to_ill_conditioned_kernel():
    with pytest.raises(ValueError,match='gamma too close'):
        observe(forward(gas=gas(1+1e-10)))
