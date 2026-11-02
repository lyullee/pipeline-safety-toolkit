"""Independent isenthalpic differential path and ideal caloric limits."""
import pytest
from scipy.integrate import solve_ivp
from CoolProp.CoolProp import PropsSI
from test_compressor_recycle_individual import CaloricGas
from pipeline_integrity.gas_thermal_observation import GasPressureReductionRequest,observe_gas_pressure_reduction


def request(**changes):
    d=dict(fluid='Nitrogen',inlet_pressure_Pa_abs=1e6,outlet_pressure_Pa_abs=1e5,
        inlet_temperature_K=300.,property_basis_id='declared-gas-state',
        same_composition_steady_adiabatic_no_work_negligible_KE_PE_and_single_phase_path_reviewed=True)
    d.update(changes);return GasPressureReductionRequest(**d)


@pytest.mark.parametrize('slope,offset',[(0.,0.),(1.7,0.),(.8,-2e7)])
def test_ideal_pressure_independent_caloric_enthalpy_has_no_temperature_change(slope,offset):
    r=observe_gas_pressure_reduction(request(),property_provider=CaloricGas(slope=slope,offset=offset))
    assert r.status=='computed'
    assert r.signed_temperature_change_K==pytest.approx(0.,abs=1e-10)
    assert r.specific_enthalpy_residual_J_kg==pytest.approx(0.,abs=1e-7)


@pytest.mark.parametrize('fluid,pin,expected_drop_sign',[('Nitrogen',1e6,1),('Methane',3e6,1),('Hydrogen',3e6,-1)])
def test_actual_EOS_matches_independent_isenthalpic_differential_path(fluid,pin,expected_drop_sign):
    q=request(fluid=fluid,inlet_pressure_Pa_abs=pin)
    def rhs(p,y):
        t=float(y[0]);rho=PropsSI('Dmass','P',p,'T',t,fluid)
        rhot=PropsSI('d(Dmass)/d(T)|P','P',p,'T',t,fluid)
        cp=PropsSI('Cpmass','P',p,'T',t,fluid)
        # From dh=Cp*dT + (v-T*dv/dT|P)*dP, independently of P/H flash.
        return [(-t*rhot/rho**2-1/rho)/cp]
    solution=solve_ivp(rhs,(pin,1e5),[300.],method='DOP853',rtol=2e-11,atol=1e-9,max_step=(pin-1e5)/20)
    assert solution.success
    r=observe_gas_pressure_reduction(q)
    assert r.status=='computed'
    assert r.outlet_state.temperature_k==pytest.approx(solution.y[0,-1],rel=2e-9,abs=2e-6)
    assert r.temperature_drop_K*expected_drop_sign>0
    assert abs(r.specific_enthalpy_residual_J_kg)<1e-3


def test_zero_pressure_change_has_zero_drop_without_a_cooling_floor():
    r=observe_gas_pressure_reduction(request(outlet_pressure_Pa_abs=1e6))
    assert r.status=='computed' and r.temperature_drop_K==pytest.approx(0.,abs=1e-7)


@pytest.mark.parametrize('change',[dict(inlet_pressure_Pa_abs=None),dict(outlet_pressure_Pa_abs=None),
    dict(inlet_temperature_K=None),dict(same_composition_steady_adiabatic_no_work_negligible_KE_PE_and_single_phase_path_reviewed=False)])
def test_unknown_state_or_energy_boundary_does_not_query_EOS(change):
    class Never:
        def resolve(self,q):raise AssertionError('unknown state must not query properties')
    r=observe_gas_pressure_reduction(request(**change),property_provider=Never())
    assert r.status=='needs_data' and r.outlet_state is None


@pytest.mark.parametrize('phase',['liquid','twophase','supercritical_liquid'])
def test_condensed_or_two_phase_state_not_used_as_gas(phase):
    class OtherPhase(CaloricGas):
        def resolve(self,q):return super().resolve(q).model_copy(update={'phase':phase})
    r=observe_gas_pressure_reduction(request(),property_provider=OtherPhase())
    assert r.status=='out_of_scope'


@pytest.mark.parametrize('wrong',['fluid','pressure','enthalpy'])
def test_prescribed_property_identity_not_changed(wrong):
    with pytest.raises(ValueError):observe_gas_pressure_reduction(request(),property_provider=CaloricGas(wrong=wrong))


def test_provider_revision_cannot_change_common_enthalpy_reference():
    class Changing(CaloricGas):
        def __init__(self):super().__init__();self.calls=0
        def resolve(self,q):
            self.calls+=1
            return super().resolve(q).model_copy(update={'provider_revision':str(self.calls)})
    with pytest.raises(ValueError):observe_gas_pressure_reduction(request(),property_provider=Changing())


@pytest.mark.parametrize('change',[dict(outlet_pressure_Pa_abs=2e6),dict(outlet_pressure_Pa_abs=0),dict(inlet_temperature_K=-1)])
def test_invalid_direction_or_absolute_state(change):
    with pytest.raises(ValueError):request(**change)
