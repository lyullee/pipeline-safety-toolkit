"""Wave-evolution inverse and independent isentropic EOS derivative checks."""
import math
import pytest
from scipy.integrate import solve_ivp
from CoolProp.CoolProp import PropsSI
from pipeline_integrity.acoustic_density import AcousticDensityRequest,observe_acoustic_density
from pipeline_integrity.paired_transit import PairedTransitRequest,invert_paired_transit


def request(**changes):
    d=dict(isentropic_bulk_modulus_Pa=1.2e5,acoustic_speed_m_s=340.,modulus_basis='isentropic',
        speed_basis='unbounded_fluid',bulk_state_id='same-fluid-state',speed_state_id='same-fluid-state',
        same_state_composition_single_phase_nonrelaxing_acoustics_reviewed=True)
    d.update(changes);return AcousticDensityRequest(**d)


@pytest.mark.parametrize('density,bulk,wavelength',[(1.2,1.5e5,.8),(998.,2.2e9,3.),(16.,4e6,.03)])
def test_linear_mass_momentum_mode_period_inverts_known_density(density,bulk,wavelength):
    k=2*math.pi/wavelength
    def rhs(t,y):return [-density*k*y[1],bulk*k*y[0]/density**2]
    def quarter_cycle(t,y):return y[0]
    quarter_cycle.direction=-1;quarter_cycle.terminal=True
    sol=solve_ivp(rhs,(0,1.),[1.,0.],events=quarter_cycle,
        method='DOP853',rtol=2e-12,atol=1e-13,max_step=1e-4)
    assert sol.success and len(sol.t_events[0])==1
    observed_c=wavelength/(4*sol.t_events[0][0])
    r=observe_acoustic_density(request(isentropic_bulk_modulus_Pa=bulk,acoustic_speed_m_s=observed_c))
    assert r.status=='computed' and r.density_kg_m3==pytest.approx(density,rel=2e-10)


@pytest.mark.parametrize('fluid,p,t',[('Water',2e6,300.),('Nitrogen',1e6,350.),('Methane',5e6,290.)])
def test_actual_single_phase_EOS_isentropic_derivative(fluid,p,t):
    density=PropsSI('Dmass','P',p,'T',t,fluid)
    derivative=PropsSI('d(Dmass)/d(P)|Smass','P',p,'T',t,fluid)
    bulk=density/derivative
    c=PropsSI('speed_of_sound','P',p,'T',t,fluid)
    r=observe_acoustic_density(request(isentropic_bulk_modulus_Pa=bulk,acoustic_speed_m_s=c))
    assert r.status=='computed' and r.density_kg_m3==pytest.approx(density,rel=2e-11)


def test_true_medium_speed_from_oblique_transit_not_effective_beam_speed():
    c=340.;u=20.;length=.4;projection=.2;beta=projection/length
    beam=math.sqrt(c*c-u*u*(1-beta*beta))
    transit=invert_paired_transit(PairedTransitRequest(clock_id='same-clock',path_length_m=length,
        signed_axial_projection_m=projection,forward_observed_time_s=length/(beam+u*beta),
        reverse_observed_time_s=length/(beam-u*beta),forward_fixed_delay_s=0.,reverse_fixed_delay_s=0.,
        uniform_axial_flow_isotropic_medium_and_stationary_paths_reviewed=True))
    assert transit.status=='computed' and transit.uniform_medium_sound_speed_m_s==pytest.approx(c)
    r=observe_acoustic_density(request(acoustic_speed_m_s=transit.uniform_medium_sound_speed_m_s,
        isentropic_bulk_modulus_Pa=1.2*c*c))
    assert r.density_kg_m3==pytest.approx(1.2)
    withheld=observe_acoustic_density(request(acoustic_speed_m_s=transit.effective_beam_speed_m_s,speed_basis='effective_beam'))
    assert withheld.status=='out_of_scope' and withheld.density_kg_m3 is None


@pytest.mark.parametrize('scale',[.01,17.])
def test_common_modulus_and_speed_scaling(scale):
    a=observe_acoustic_density(request())
    b=observe_acoustic_density(request(isentropic_bulk_modulus_Pa=1.2e5*scale*scale,acoustic_speed_m_s=340*scale))
    assert b.density_kg_m3==pytest.approx(a.density_kg_m3)


@pytest.mark.parametrize('change',[dict(isentropic_bulk_modulus_Pa=None),dict(acoustic_speed_m_s=None),
    dict(bulk_state_id=None),dict(speed_state_id=None),dict(modulus_basis='unknown'),dict(speed_basis='unknown'),
    dict(same_state_composition_single_phase_nonrelaxing_acoustics_reviewed=False)])
def test_unknown_property_or_state_not_inferred(change):
    r=observe_acoustic_density(request(**change));assert r.status=='needs_data' and r.density_kg_m3 is None


@pytest.mark.parametrize('change',[dict(modulus_basis='isothermal'),dict(speed_basis='elastic_pipe_wave')])
def test_other_constitutive_or_wave_basis_withheld(change):
    r=observe_acoustic_density(request(**change));assert r.status=='out_of_scope'


def test_different_states_not_relabelled():
    with pytest.raises(ValueError):observe_acoustic_density(request(speed_state_id='other-fluid-state'))


@pytest.mark.parametrize('change',[dict(acoustic_speed_m_s=0),dict(isentropic_bulk_modulus_Pa=-1)])
def test_nonpositive_quantities_rejected(change):
    with pytest.raises(ValueError):request(**change)
