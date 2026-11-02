"""Independent pressure-work references for local coordinate reductions."""
import math
import pytest
from scipy.integrate import solve_ivp, quad
from pipeline_integrity.liquid_resistance_coordinates import (
    LiquidResistanceCoordinate as C, LiquidResistanceReductionRequest as R,
    reduce_liquid_resistance_coordinates as evaluate,
)


def kval(name,k,d):
    return C(component_id=name,kind='velocity_head_K',coefficient=k,actual_velocity_reference_diameter_m=d,applicable_flow_sign='both',basis_id='actual mean velocity',coefficient_and_regime_reviewed=True)


def cvval(name,cv,rho):
    return C(component_id=name,kind='water_Cv',coefficient=cv,cv_reference_water_density_kg_m3=rho,applicable_flow_sign='both',basis_id='US gallon per minute, one psi water',coefficient_and_regime_reviewed=True)


def request(parts,**kw):
    return R(components=parts,common_flow_and_single_liquid_regime_reviewed=True,**kw)


@pytest.mark.parametrize('q',[-.023,-.001,0,.001,.023])
def test_distinct_reference_velocities_pressure_ode_and_energy(q):
    rho=872.;ks=[.7,1.9,4.2];ds=[.09,.14,.065];lengths=[.3,1.7,.8]
    parts=tuple(kval(str(i),k,d) for i,(k,d) in enumerate(zip(ks,ds)))
    result=evaluate(request(parts,actual_density_kg_m3=rho,actual_volume_flow_m3_s=q,output_velocity_reference_diameter_m=.11))
    assert result.state=='computed'
    pressures=[]
    for k,d,L in zip(ks,ds,lengths):
        # Distributed drag balances pressure force; resolve an authored
        # sinusoidal drag-density field with the specified total work.
        A=quad(lambda r:2*math.pi*r,0,d/2,epsabs=1e-15)[0]
        velocity=q/A
        density=lambda x:k/L*(1+.6*math.cos(2*math.pi*x/L))
        sol=solve_ivp(lambda x,p:[-rho*velocity*abs(velocity)*density(x)/2],(0,L),[2e5],rtol=1e-12,atol=1e-7,max_step=L/160)
        pressures.append(2e5-sol.y[0,-1])
    assert result.component_inlet_minus_outlet_pressure_pa==pytest.approx(pressures,abs=2e-7,rel=2e-10)
    assert result.inlet_minus_outlet_pressure_pa==pytest.approx(sum(pressures),abs=4e-7,rel=2e-10)
    assert result.irreversible_power_w==pytest.approx(sum(p*q for p in pressures),abs=2e-8)
    area=math.pi*.11**2/4
    assert result.inlet_minus_outlet_pressure_pa==pytest.approx(result.equivalent_velocity_head_K*rho*(q/area)*abs(q/area)/2)


@pytest.mark.parametrize('reference_density',[983.2,998.7,1000.])
def test_water_cv_definition_series_pressure_and_reference_reexpression(reference_density):
    cvs=[2.3,7.9,4.7];gpm_to_m3s=231*(.0254**3)/60
    psi_to_pa=4.4482216152605/(.0254**2)
    qgpm=3.9;actualrho=821.;q=qgpm*gpm_to_m3s
    parts=tuple(cvval(str(i),cv,reference_density) for i,cv in enumerate(cvs))
    result=evaluate(request(parts,actual_volume_flow_m3_s=q,actual_density_kg_m3=actualrho,output_cv_reference_water_density_kg_m3=reference_density,output_velocity_reference_diameter_m=.035))
    # One-psi-water calibration is the independently authored definition;
    # pressure series addition is applied before recovering aggregate Cv.
    pressures=[(qgpm/cv)**2*(actualrho/reference_density)*psi_to_pa for cv in cvs]
    assert result.inlet_minus_outlet_pressure_pa==pytest.approx(sum(pressures),rel=2e-14)
    cv_from_pressure=qgpm*math.sqrt(actualrho/reference_density/(sum(pressures)/psi_to_pa))
    assert result.equivalent_water_Cv==pytest.approx(cv_from_pressure,rel=2e-14)
    other=evaluate(request(parts,output_cv_reference_water_density_kg_m3=1004.))
    assert other.equivalent_water_Cv==pytest.approx(result.equivalent_water_Cv*math.sqrt(reference_density/1004.))


def test_mixed_cv_k_and_actual_basis_roundtrip_without_assumed_water_density():
    first=evaluate(request((kval('a',2.1,.081),),output_cv_reference_water_density_kg_m3=991.4))
    q=.0017
    k=evaluate(request((kval('a',2.1,.081),kval('b',.31,.052)),actual_volume_flow_m3_s=q,actual_density_kg_m3=790.,output_velocity_reference_diameter_m=.12))
    mixed=evaluate(request((cvval('a',first.equivalent_water_Cv,991.4),kval('b',.31,.052)),actual_volume_flow_m3_s=q,actual_density_kg_m3=790.,output_velocity_reference_diameter_m=.12))
    assert mixed.equivalent_velocity_head_K==pytest.approx(k.equivalent_velocity_head_K,rel=3e-15)
    assert mixed.inlet_minus_outlet_pressure_pa==pytest.approx(k.inlet_minus_outlet_pressure_pa,rel=3e-15)


def test_diameter_reference_transform_preserves_work_and_single_component_limit():
    a=evaluate(request((kval('a',3.,.1),),output_velocity_reference_diameter_m=.1))
    b=evaluate(request((kval('a',3.,.1),),output_velocity_reference_diameter_m=.2))
    assert a.equivalent_velocity_head_K==pytest.approx(3.)
    assert b.equivalent_velocity_head_K==pytest.approx(48.)


def test_same_bore_sum_and_reordering_are_coordinate_invariant():
    parts=(kval('a',2.,.1),kval('b',3.,.1),kval('c',4.,.1))
    a=evaluate(request(parts,output_velocity_reference_diameter_m=.1))
    b=evaluate(request(tuple(reversed(parts)),output_velocity_reference_diameter_m=.1))
    assert a.equivalent_velocity_head_K==pytest.approx(9.)
    assert a.equivalent_mass_scaled_conductance_m2==b.equivalent_mass_scaled_conductance_m2


@pytest.mark.parametrize('missing',['coefficient','basis_id','actual_velocity_reference_diameter_m'])
def test_unknown_k_coordinates_are_withheld(missing):
    part=kval('a',1.,.1).model_copy(update={missing:None})
    assert evaluate(request((part,))).state=='needs_data'


def test_unknown_cv_water_reference_is_not_replaced_with_process_density():
    part=cvval('a',3.,990.).model_copy(update={'cv_reference_water_density_kg_m3':None})
    assert evaluate(request((part,),actual_density_kg_m3=800.)).state=='needs_data'


def test_review_flow_direction_and_actual_state_guards():
    part=kval('a',1.,.1)
    assert evaluate(R(components=(part,))).state=='needs_data'
    assert evaluate(request((part,),actual_volume_flow_m3_s=.001)).state=='needs_data'
    directional=part.model_copy(update={'applicable_flow_sign':'positive'})
    assert evaluate(request((directional,),actual_volume_flow_m3_s=-.001,actual_density_kg_m3=900.)).state=='out_of_scope'


@pytest.mark.parametrize('change',[{'coefficient':0},{'coefficient':-1},{'coefficient':float('inf')},{'actual_velocity_reference_diameter_m':0}])
def test_invalid_coefficients_are_not_silently_clipped(change):
    with pytest.raises(ValueError):C.model_validate({**kval('a',1.,.1).model_dump(),**change})


def test_duplicate_or_conflicting_coordinates_are_rejected():
    with pytest.raises(ValueError):request((kval('a',1.,.1),kval('a',2.,.2)))
    with pytest.raises(ValueError):cvval('a',3.,990.).model_copy(update={'actual_velocity_reference_diameter_m':.1}).model_validate({**cvval('a',3.,990.).model_dump(),'actual_velocity_reference_diameter_m':.1})


def test_caller_contract_replays_without_losing_reference_coordinates():
    original=request((kval('a',2.,.1),cvval('b',3.,997.)),output_velocity_reference_diameter_m=.2)
    replay=R.model_validate_json(original.model_dump_json())
    assert replay==original
    assert evaluate(replay)==evaluate(original)
