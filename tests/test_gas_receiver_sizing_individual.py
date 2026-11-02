"""Independent ideal-state coordinate and real-EOS inventory sizing checks."""
import math
import pytest
from pipeline_integrity.gas_receiver_sizing import GasReceiverSizingRequest as Request,size_gas_receiver as solve
from pipeline_integrity.thermo import ThermodynamicState


def make(**update):
    q=Request(fluid='Air',high_pressure_Pa_abs=1e6,low_pressure_Pa_abs=4e5,high_temperature_K=300.,low_temperature_K=300.,
        demand_reference_pressure_Pa_abs=101325.,demand_reference_temperature_K=288.15,
        demand_reference_volume_flow_m3_s=.02,duration_per_demand_s=10.,demand_count=3,
        property_and_reference_basis_id='synthetic ideal-gas fixture',
        rigid_uniform_same_composition_and_endpoint_states_reviewed=True)
    return q.model_copy(update=update)


class Ideal:
    def __init__(self,R=287.05):self.R=R
    def resolve(self,request):
        p=request.inputs['P'];t=request.inputs['T']
        return ThermodynamicState(fluid=request.fluid,backend=request.backend,pressure_pa_abs=p,temperature_k=t,
            density_kg_m3=p/(self.R*t),enthalpy_j_kg=1000.*t,entropy_j_kg_k=0.,phase='gas',
            viscosity_pa_s=None,cp_j_kg_k=1000.,cv_j_kg_k=1000.-self.R,conductivity_w_m_k=None,
            speed_of_sound_m_s=math.sqrt(1.4*self.R*t),provider_version='synthetic fixture',provider_revision='test-only')


def ideal(q):return solve(q,property_provider=Ideal())


@pytest.mark.parametrize('high,low,temp,qflow',[(1e6,4e5,300.,.02),(3e5,1e5,280.,.001),
    (2e6,1e6,350.,.5),(1e7,9e6,320.,.01),(2e5,1.5e5,300.,0.)])
def test_independent_isothermal_pressure_ratio_sizing(high,low,temp,qflow):
    q=make(high_pressure_Pa_abs=high,low_pressure_Pa_abs=low,high_temperature_K=temp,low_temperature_K=temp,
        demand_reference_volume_flow_m3_s=qflow)
    r=ideal(q)
    expected=qflow*10*3*q.demand_reference_pressure_Pa_abs*temp/(q.demand_reference_temperature_K*(high-low))
    assert r.status=='computed' and r.required_receiver_volume_m3==pytest.approx(expected)
    assert r.initial_receiver_mass_kg-r.residual_receiver_mass_kg==pytest.approx(r.demand_mass_kg)
    assert r.mass_capacity_residual_kg==pytest.approx(0.,abs=1e-12)
    assert not r.geometry_or_pressure_rating_selected


def test_declared_different_endpoint_temperatures():
    q=make(high_temperature_K=330.,low_temperature_K=290.)
    r=ideal(q)
    expected=(.02*10*3)*(101325./288.15)/(1e6/330.-4e5/290.)
    assert r.required_receiver_volume_m3==pytest.approx(expected)


@pytest.mark.parametrize('field,scale',[('demand_reference_volume_flow_m3_s',7.),('duration_per_demand_s',.3),('demand_count',2)])
def test_demand_extensivity(field,scale):
    q=make();a=ideal(q);b=ideal(q.model_copy(update={field:getattr(q,field)*scale}))
    assert b.required_receiver_volume_m3==pytest.approx(a.required_receiver_volume_m3*scale)


def test_gas_reference_volume_basis_change_preserves_required_mass_and_vessel():
    q=make();a=ideal(q)
    target_pressure=2e5;target_temperature=320.
    target_rate=.02*(101325./288.15)/(target_pressure/target_temperature)
    b=ideal(q.model_copy(update=dict(demand_reference_pressure_Pa_abs=target_pressure,
        demand_reference_temperature_K=target_temperature,demand_reference_volume_flow_m3_s=target_rate,
        property_and_reference_basis_id='new explicit volume reference')))
    assert b.demand_mass_kg==pytest.approx(a.demand_mass_kg)
    assert b.required_receiver_volume_m3==pytest.approx(a.required_receiver_volume_m3)


def test_ideal_gas_constant_cancels_only_with_same_composition_reference():
    q=make();a=ideal(q);b=solve(q,property_provider=Ideal(R=400.))
    assert b.required_receiver_volume_m3==pytest.approx(a.required_receiver_volume_m3)
    assert b.demand_mass_kg==pytest.approx(a.demand_mass_kg*287.05/400.)


@pytest.mark.parametrize('field',['duration_per_demand_s','demand_count'])
def test_zero_demand(field):
    r=ideal(make(**{field:0}))
    assert r.status=='computed' and r.required_receiver_volume_m3==0.


@pytest.mark.parametrize('field',['duration_per_demand_s','demand_reference_volume_flow_m3_s'])
def test_unknown_demand_not_replaced_by_zero(field):
    r=ideal(make(**{field:None}));assert r.status=='needs_data' and r.required_receiver_volume_m3 is None


def test_unreviewed_state_basis_withheld():
    r=ideal(make(rigid_uniform_same_composition_and_endpoint_states_reviewed=False))
    assert r.status=='needs_data'


def test_pressure_drop_without_positive_density_capacity_withheld():
    r=ideal(make(low_temperature_K=80.))
    assert r.status=='out_of_scope' and r.required_receiver_volume_m3 is None


@pytest.mark.parametrize('field,value',[('low_pressure_Pa_abs',1e6),('high_pressure_Pa_abs',0.),
    ('high_temperature_K',math.nan),('duration_per_demand_s',-1.),('demand_count',-1)])
def test_invalid_contract_revalidated(field,value):
    with pytest.raises(ValueError):ideal(make(**{field:value}))


def test_liquid_phase_rejected():
    class Liquid(Ideal):
        def resolve(self,request):return super().resolve(request).model_copy(update={'phase':'liquid'})
    with pytest.raises(ValueError):solve(make(),property_provider=Liquid())


def test_changed_pressure_coordinate_rejected():
    class Wrong(Ideal):
        def resolve(self,request):return super().resolve(request).model_copy(update={'pressure_pa_abs':2*request.inputs['P']})
    with pytest.raises(ValueError):solve(make(),property_provider=Wrong())


def test_drifting_reference_density_rejected():
    class Drift(Ideal):
        def __init__(self):super().__init__();self.references=0
        def resolve(self,request):
            s=super().resolve(request)
            if request.inputs['P']==101325.:
                self.references+=1
                return s.model_copy(update={'density_kg_m3':s.density_kg_m3*self.references})
            return s
    with pytest.raises(ValueError,match='reference density changed'):solve(make(),property_provider=Drift())


@pytest.mark.parametrize('high,low,thi,tlo',[(5e5,2e5,300.,300.),(2e6,5e5,320.,290.),(1e7,1e6,350.,300.)])
def test_actual_air_eos_endpoint_inventory_budget(high,low,thi,tlo):
    from CoolProp.CoolProp import PropsSI
    q=make(high_pressure_Pa_abs=high,low_pressure_Pa_abs=low,high_temperature_K=thi,low_temperature_K=tlo,
        property_and_reference_basis_id='HEOS Air endpoint inventory')
    r=solve(q)
    rhoh=PropsSI('Dmass','P',high,'T',thi,'HEOS::Air');rhol=PropsSI('Dmass','P',low,'T',tlo,'HEOS::Air')
    rhor=PropsSI('Dmass','P',101325.,'T',288.15,'HEOS::Air')
    assert r.status=='computed' and r.required_receiver_volume_m3==pytest.approx(.6*rhor/(rhoh-rhol),rel=2e-12)
