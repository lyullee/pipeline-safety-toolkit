"""Independent caloric mixing, phase-state and adiabatic return checks."""
import math
import pytest
from scipy.integrate import quad
from scipy.optimize import brentq
from CoolProp.CoolProp import PropsSI
from pipeline_integrity.contracts import EvidenceRef,RunContext
from pipeline_integrity.compressor_observations import GasRecycleInletRequest,reconstruct_gas_recycle_inlet
from pipeline_integrity.thermo import ThermodynamicState


def context(**changes):
    d=dict(asset_id='compressor',scenario_id='observed-return',clock_id='steady',origin='synthetic',
        evidence=(EvidenceRef(id='basis',citation='Independent synthetic caloric basis',origin='synthetic'),))
    d.update(changes);return RunContext(**d)


def request(**changes):
    d=dict(id='recycle',asset_id='compressor',clock_id='steady',time_s=0,fluid='Nitrogen',
        fresh_mass_flow_kg_s=3.,return_mass_flow_kg_s=.2,inlet_pressure_Pa_abs=2e5,
        fresh_temperature_K=300.,return_pressure_Pa_abs=7e5,return_temperature_K=440.,
        hydraulic_source_digest='a'*64,energy_reference_id='common-caloric-basis',evidence_id='basis',
        same_composition_steady_adiabatic_throttle_mix_negligible_KE_PE_reviewed=True)
    d.update(changes);return GasRecycleInletRequest(**d)


class CaloricGas:
    def __init__(self,offset=0.,slope=0.,wrong=None):self.offset,self.slope,self.wrong=offset,slope,wrong
    def resolve(self,q):
        p=q.inputs['P'];h=q.inputs.get('Hmass')
        if h is None:t=q.inputs['T'];h=self.offset+1000*t+.5*self.slope*t*t
        else:
            energy=h-self.offset
            t=energy/1000 if self.slope==0 else 2*energy/(1000+math.sqrt(1e6+2*self.slope*energy))
        return ThermodynamicState(fluid='Other' if self.wrong=='fluid' else q.fluid,backend=q.backend,
            pressure_pa_abs=p+100 if self.wrong=='pressure' else p,
            temperature_k=t,enthalpy_j_kg=h+1 if self.wrong=='enthalpy' else h,
            entropy_j_kg_k=0,density_kg_m3=p/(300*t),phase='liquid' if self.wrong=='phase' else 'gas',
            cp_j_kg_k=1000+self.slope*t,cv_j_kg_k=700+self.slope*t,viscosity_pa_s=None,
            conductivity_w_m_k=None,speed_of_sound_m_s=None,provider_version='synthetic',provider_revision='1')


@pytest.mark.parametrize('return_mass,slope',[(0.,0.),(.2,0.),(8.,0.),(.4,1.7)])
def test_independent_integrated_caloric_mixing(return_mass,slope):
    q=request(return_mass_flow_kg_s=return_mass)
    # Solve heat redistribution between parcels independently of the network matrix.
    expected=brentq(lambda t:q.fresh_mass_flow_kg_s*quad(lambda z:1000+slope*z,300,t)[0]
        -return_mass*quad(lambda z:1000+slope*z,t,440)[0],299.,441.)
    r=reconstruct_gas_recycle_inlet(q,context(),property_provider=CaloricGas(slope=slope))
    assert r.status=='computed'
    assert r.mixed_inlet_state.temperature_k==pytest.approx(expected,rel=2e-13)
    assert r.total_impeller_mass_flow_kg_s==3+return_mass
    assert abs(r.mixing_energy_residual_W)<1e-7
    if return_mass:
        assert r.return_state_after_throttle.enthalpy_j_kg==r.return_state_before_throttle.enthalpy_j_kg
        assert r.return_state_after_throttle.pressure_pa_abs==2e5
    else:assert r.return_state_before_throttle is r.return_state_after_throttle is None


@pytest.mark.parametrize('offset',[-4e7,8e6])
def test_common_enthalpy_reference_invariance(offset):
    a=reconstruct_gas_recycle_inlet(request(),context(),property_provider=CaloricGas(slope=1.))
    b=reconstruct_gas_recycle_inlet(request(),context(),property_provider=CaloricGas(offset=offset,slope=1.))
    assert a.mixed_inlet_state.temperature_k==pytest.approx(b.mixed_inlet_state.temperature_k,rel=1e-12)


@pytest.mark.parametrize('scale',[.002,23.])
def test_extensive_flow_scaling(scale):
    a=reconstruct_gas_recycle_inlet(request(),context(),property_provider=CaloricGas())
    b=reconstruct_gas_recycle_inlet(request(fresh_mass_flow_kg_s=3*scale,return_mass_flow_kg_s=.2*scale),context(),property_provider=CaloricGas())
    assert b.mixed_inlet_state.temperature_k==pytest.approx(a.mixed_inlet_state.temperature_k)


@pytest.mark.parametrize('change',[dict(return_mass_flow_kg_s=None),dict(return_temperature_K=None),
    dict(return_pressure_Pa_abs=None),dict(same_composition_steady_adiabatic_throttle_mix_negligible_KE_PE_reviewed=False)])
def test_unknown_active_stream_is_not_cold_or_zero(change):
    class Never:
        def resolve(self,q):raise AssertionError('unknown boundary must not query EOS')
    r=reconstruct_gas_recycle_inlet(request(**change),context(),property_provider=Never())
    assert r.status=='needs_data' and r.mixed_inlet_state is None


def test_zero_return_requires_no_return_state():
    r=reconstruct_gas_recycle_inlet(request(return_mass_flow_kg_s=0,return_pressure_Pa_abs=None,
        return_temperature_K=None),context(),property_provider=CaloricGas())
    assert r.status=='computed' and r.mixed_inlet_state.temperature_k==pytest.approx(300.)


def test_passive_return_cannot_raise_pressure():
    r=reconstruct_gas_recycle_inlet(request(return_pressure_Pa_abs=1e5),context(),property_provider=CaloricGas())
    assert r.status=='out_of_scope'


@pytest.mark.parametrize('wrong',['phase','fluid','pressure','enthalpy'])
def test_property_identity_phase_and_PH_coordinates(wrong):
    with pytest.raises(ValueError):reconstruct_gas_recycle_inlet(request(),context(),property_provider=CaloricGas(wrong=wrong))


@pytest.mark.parametrize('changes',[dict(clock_id='other'),dict(asset_id='other'),dict(evidence=())])
def test_context_and_evidence_not_relabelled(changes):
    with pytest.raises(ValueError):reconstruct_gas_recycle_inlet(request(),context(**changes),property_provider=CaloricGas())


def test_actual_nitrogen_integrated_cp_energy_budget():
    q=request();r=reconstruct_gas_recycle_inlet(q,context())
    tm=r.mixed_inlet_state.temperature_k
    # Separate cp integrals about a common reference on the actual inlet isobar.
    tref=250.
    delta=lambda t:quad(lambda z:PropsSI('Cpmass','P',2e5,'T',z,'Nitrogen'),tref,t,epsabs=1e-5)[0]
    h_ref=PropsSI('Hmass','P',2e5,'T',tref,'Nitrogen')
    return_relative=r.return_state_before_throttle.enthalpy_j_kg-h_ref
    assert (3.*delta(300.)+.2*return_relative)==pytest.approx(3.2*delta(tm),rel=2e-10)
    assert 300.<tm<440.
