"""Manufactured phase-equilibrium and conserved component reference tests."""
import math
import pytest
from pipeline_integrity.black_oil_phase_observer import BlackOilPartitionRequest, observe_black_oil_partition

R=8.31446261815324


def state(pressure=2e5,multiplier=2.,oil=1.):
    # Nonvolatile solvent + ideal dilute-solute chemical-potential closure:
    # gas fugacity P equals Henry pressure H*x in the saturated liquid.
    H=2e7;T=310.;p0=1e5;T0=300.;Mo=.2;Mg=.016;rho_o=800.
    n_o=oil*rho_o/Mo;x=pressure/H;n_sat=n_o*x/(1-x)
    n_g=n_sat*multiplier;n_d=min(n_g,n_sat);v0=R*T0/p0
    rs_sat=n_sat*v0/oil;rs=n_d*v0/oil
    # Solute partial molar volume is supplied, rather than inferred from Bg.
    partial_volume=3e-5;bo=(oil+n_d*partial_volume)/oil
    q=BlackOilPartitionRequest(pressure_Pa_abs=pressure,temperature_K=T,stock_oil_volume_m3=oil,
        total_gas_reference_volume_m3=n_g*v0,oil_reference_density_kg_m3=rho_o,gas_reference_density_kg_m3=Mg/v0,
        oil_reference_temperature_K=T0,oil_reference_pressure_Pa_abs=p0,gas_reference_temperature_K=T0,
        gas_reference_pressure_Pa_abs=p0,reference_basis_id='specified-stock-states',material_PVT_id='ideal-Henry-and-partial-volume',
        saturated_gas_ratio_ref_m3_per_stock_oil_m3=rs_sat,oil_formation_volume_factor=bo,
        oil_factor_at_dissolved_gas_ratio=rs,gas_formation_volume_factor=(R*T/pressure)/v0,
        reviewed_nonvolatile_oil_single_gas_equilibrium_closure=True,
        formation_factors_and_saturation_at_requested_PT_and_reference_basis_reviewed=True)
    return q,dict(n_o=n_o,n_g=n_g,n_d=n_d,Mo=Mo,Mg=Mg,partial_volume=partial_volume,T=T,H=H)


@pytest.mark.parametrize('pressure',[1e5,5e5,2e6])
@pytest.mark.parametrize('multiplier',[.2,1.,3.])
def test_independent_henry_chemical_potential_component_mass_and_phase_volume(pressure,multiplier):
    q,d=state(pressure,multiplier);r=observe_black_oil_partition(q);assert r.status=='computed'
    n_free=d['n_g']-d['n_d']
    oil_volume=q.stock_oil_volume_m3+d['n_d']*d['partial_volume']
    gas_volume=n_free*R*d['T']/pressure
    assert r.oil_phase_volume_m3==pytest.approx(oil_volume)
    assert r.gas_phase_volume_m3==pytest.approx(gas_volume,abs=1e-12)
    assert r.oil_phase_mass_kg==pytest.approx(d['n_o']*d['Mo']+d['n_d']*d['Mg'])
    assert r.gas_phase_mass_kg==pytest.approx(n_free*d['Mg'],abs=1e-12)
    assert r.total_mass_kg==pytest.approx(d['n_o']*d['Mo']+d['n_g']*d['Mg'])
    assert r.oil_phase_density_kg_m3*oil_volume+r.gas_phase_density_kg_m3*gas_volume==pytest.approx(r.total_mass_kg)
    x=d['n_d']/(d['n_o']+d['n_d'])
    if multiplier>=1:assert math.log(x*d['H']/pressure)==pytest.approx(0.,abs=1e-14)
    else:assert x*d['H']<pressure and r.free_gas_reference_volume_m3==0
    assert r.component_mass_residual_kg==pytest.approx(0.,abs=1e-10)
    assert r.gas_reference_inventory_residual_m3==pytest.approx(0.,abs=1e-12)


def test_reference_gas_volume_relabel_preserves_physical_inventory_and_phase_state():
    q,_=state();a=observe_black_oil_partition(q);scale=2.7
    b=observe_black_oil_partition(q.model_copy(update={
        'gas_reference_pressure_Pa_abs':q.gas_reference_pressure_Pa_abs/scale,
        'reference_basis_id':'new-reference-pressure','total_gas_reference_volume_m3':q.total_gas_reference_volume_m3*scale,
        'gas_reference_density_kg_m3':q.gas_reference_density_kg_m3/scale,
        'saturated_gas_ratio_ref_m3_per_stock_oil_m3':q.saturated_gas_ratio_ref_m3_per_stock_oil_m3*scale,
        'oil_factor_at_dissolved_gas_ratio':q.oil_factor_at_dissolved_gas_ratio*scale,
        'gas_formation_volume_factor':q.gas_formation_volume_factor/scale}))
    for name in ['oil_phase_volume_m3','gas_phase_volume_m3','oil_phase_mass_kg','gas_phase_mass_kg','total_mass_kg','oil_phase_density_kg_m3','gas_phase_density_kg_m3','oil_volume_fraction']:
        assert getattr(b,name)==pytest.approx(getattr(a,name))


def test_inventory_scale_preserves_phase_densities_and_volume_fractions():
    q,_=state();a=observe_black_oil_partition(q)
    b=observe_black_oil_partition(q.model_copy(update={'stock_oil_volume_m3':q.stock_oil_volume_m3*20,'total_gas_reference_volume_m3':q.total_gas_reference_volume_m3*20}))
    assert b.total_mass_kg==pytest.approx(a.total_mass_kg*20)
    assert b.oil_volume_fraction==pytest.approx(a.oil_volume_fraction)


def test_bubblepoint_threshold_both_sides_and_boundary():
    q,_=state(multiplier=1.);boundary=q.saturated_gas_ratio_ref_m3_per_stock_oil_m3*q.stock_oil_volume_m3
    for scale,expected in [(.999,'undersaturated_liquid'),(1.,'bubble_boundary'),(1.001,'oil_and_free_gas')]:
        gas=boundary*scale;rs=min(gas/q.stock_oil_volume_m3,q.saturated_gas_ratio_ref_m3_per_stock_oil_m3)
        r=observe_black_oil_partition(q.model_copy(update={'total_gas_reference_volume_m3':gas,'oil_factor_at_dissolved_gas_ratio':rs}))
        assert r.status=='computed' and r.phase_state==expected


def test_no_gas_and_zero_solubility_boundaries():
    q,_=state(multiplier=0.);r=observe_black_oil_partition(q)
    assert r.status=='computed' and r.oil_volume_fraction==1. and r.gas_phase_mass_kg==0
    q,_=state();r=observe_black_oil_partition(q.model_copy(update={'saturated_gas_ratio_ref_m3_per_stock_oil_m3':0.,'oil_factor_at_dissolved_gas_ratio':0.,'oil_formation_volume_factor':1.}))
    assert r.dissolved_gas_reference_volume_m3==0 and r.free_gas_reference_volume_m3==q.total_gas_reference_volume_m3


@pytest.mark.parametrize('field',['pressure_Pa_abs','temperature_K','oil_reference_density_kg_m3','gas_reference_temperature_K','reference_basis_id','material_PVT_id','saturated_gas_ratio_ref_m3_per_stock_oil_m3','oil_formation_volume_factor','gas_formation_volume_factor'])
def test_missing_closure_or_reference_state_never_inferred_from_GOR(field):
    q,_=state();assert observe_black_oil_partition(q.model_copy(update={field:None})).status=='needs_data'


@pytest.mark.parametrize('field',['reviewed_nonvolatile_oil_single_gas_equilibrium_closure','formation_factors_and_saturation_at_requested_PT_and_reference_basis_reviewed'])
def test_unreviewed_material_closure_withheld(field):
    q,_=state();assert observe_black_oil_partition(q.model_copy(update={field:False})).status=='needs_data'


def test_wrong_saturated_formation_factor_in_undersaturated_oil_not_silently_accepted():
    q,_=state(multiplier=.2)
    r=observe_black_oil_partition(q.model_copy(update={'oil_factor_at_dissolved_gas_ratio':q.saturated_gas_ratio_ref_m3_per_stock_oil_m3}))
    assert r.status=='needs_data' and r.actual_dissolved_gas_ratio is not None


def test_gas_only_inventory_outside_oil_host_model():
    q,_=state();assert observe_black_oil_partition(q.model_copy(update={'stock_oil_volume_m3':0.})).status=='out_of_scope'


@pytest.mark.parametrize('kw',[dict(pressure_Pa_abs=-1.),dict(oil_formation_volume_factor=0.),dict(total_gas_reference_volume_m3=-1.),dict(gas_reference_density_kg_m3=math.inf)])
def test_invalid_physical_quantities_rejected(kw):
    q,_=state()
    with pytest.raises(ValueError):observe_black_oil_partition(q.model_copy(update=kw))
