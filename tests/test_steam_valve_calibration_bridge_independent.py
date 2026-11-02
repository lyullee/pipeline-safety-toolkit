"""Conditional valve calibration bridge; independent native-Water PT reference."""
import math
import pytest
from CoolProp.CoolProp import PropsSI
from scipy.optimize import brentq,minimize_scalar
from pipeline_integrity.steam_nozzle import SteamNozzleRequest
from pipeline_integrity.steam_valve_calibration_bridge import (
    SteamValveAreaState,SteamValveObservationRequest,observe_calibrated_steam_valve,
)


class Curve:
    def __init__(self,bad=None):self.bad=bad;self.calls=0
    def area(self,cv,**state):
        self.calls+=1;p=state['stagnation_pressure_Pa_abs'];t=state['stagnation_temperature_K'];pb=state['back_pressure_Pa_abs']
        a=3e-6*(t/700)*(pb/p)**.1*(cv**1.25 if self.bad!='nonmonotone' else (10-cv)**2)
        return SteamValveAreaState(valve_id='other' if self.bad=='identity' else 'valve-one',Cv_definition_id='named-conventional-Cv-reference',
            calibration_id='caller-state-curve',revision='changed' if self.bad=='revision' and self.calls>1 else 'revision-one',
            Cv=cv,**dict(state,back_pressure_Pa_abs=pb+(1 if self.bad=='state' else 0)),effective_discharge_area_m2=a)


def request(p=1e6,t=700.,ratio=.3,**changes):
    nozzle=SteamNozzleRequest(stagnation_pressure_Pa_abs=p,stagnation_temperature_K=t,back_pressure_Pa_abs=p*ratio,
        pressure_support_lower_Pa_abs=p*.15,property_basis_id='pure-Water-HEOS-total-state',
        adiabatic_no_work_one_dimensional_total_inlet_and_single_phase_isentrope_reviewed=True,
        single_sonic_maximum_on_supported_path_reviewed=True)
    values=dict(mode='Cv_from_mass_flow',nozzle=nozzle,valve_id='valve-one',Cv_definition_id='named-conventional-Cv-reference',
        calibration_id='caller-state-curve',state_specific_valve_Cv_area_calibration_and_nozzle_basis_reviewed=True,
        calibration_monotonic_bracket_reviewed=True,target_mass_flow_kg_s=.01,Cv_bracket=(0.,20.))
    values.update(changes);return SteamValveObservationRequest(**values)


def independent_water_PT_flux(p0,t0,pb):
    # Independent entropy-constrained PT root, rather than the owner's PS
    # state path or sonic-sound-speed condition. Pressure-flux maximum is
    # independently optimized over a known dry domain.
    fluid='HEOS::Water';s0=PropsSI('Smass','P',p0,'T',t0,fluid);h0=PropsSI('Hmass','P',p0,'T',t0,fluid)
    def flux(p):
        saturation=PropsSI('T','P',p,'Q',1.,fluid)
        # The native PT interface intentionally refuses its saturation
        # ambiguity band. These selected superheated paths bracket entropy
        # well above that band; no saturation-state value is substituted.
        lower=saturation+1.
        assert PropsSI('Smass','P',p,'T',lower,fluid)<s0
        t=brentq(lambda temperature:PropsSI('Smass','P',p,'T',temperature,fluid)-s0,lower,t0,xtol=2e-10)
        h=PropsSI('Hmass','P',p,'T',t,fluid);rho=PropsSI('Dmass','P',p,'T',t,fluid)
        return rho*math.sqrt(2*(h0-h))
    optimum=minimize_scalar(lambda ratio:-flux(ratio*p0),bounds=(.2,.98),method='bounded',options={'xatol':1e-11})
    assert optimum.success
    throat=max(pb,optimum.x*p0)
    return flux(throat)


@pytest.mark.parametrize('p,t',[(5e5,650.),(2e6,800.)])
@pytest.mark.parametrize('ratio',[.9,.2])
def test_independent_native_water_flux_and_calibrated_Cv_forward_inverse(p,t,ratio):
    cv=7.2;flux=independent_water_PT_flux(p,t,p*ratio)
    physical_area=3e-6*(t/700)*ratio**.1*cv**1.25
    target=flux*physical_area
    q=request(p,t,ratio,target_mass_flow_kg_s=target)
    result=observe_calibrated_steam_valve(q,calibration_provider=Curve())
    assert result.status=='computed' and result.Cv==pytest.approx(cv,rel=2e-7)
    assert result.mass_flow_kg_s==pytest.approx(target,rel=2e-12)
    assert abs(result.mass_flow_residual_kg_s)<1e-10
    forward=observe_calibrated_steam_valve(request(p,t,ratio,mode='capacity_from_Cv',Cv=cv,target_mass_flow_kg_s=None,Cv_bracket=None),calibration_provider=Curve())
    assert forward.status=='computed' and forward.mass_flow_kg_s==pytest.approx(target,rel=2e-7)
    assert forward.Cv_definition_id==q.Cv_definition_id
    assert forward.nozzle.regime==('unchoked' if ratio==.9 else 'choked')
    assert not result.universal_Cv_area_or_legacy_steam_fit_inferred
    assert not result.device_calibration_or_field_qualified


def test_absent_or_unreviewed_device_calibration_remains_unknown():
    q=request()
    assert observe_calibrated_steam_valve(q).status=='needs_data'
    assert observe_calibrated_steam_valve(q.model_copy(update={'state_specific_valve_Cv_area_calibration_and_nozzle_basis_reviewed':False}),calibration_provider=Curve()).status=='needs_data'


@pytest.mark.parametrize('bad',['identity','revision','state'])
def test_calibration_identity_revision_and_state_guards(bad):
    with pytest.raises(ValueError):observe_calibrated_steam_valve(request(),calibration_provider=Curve(bad))


def test_nonmonotone_calibration_not_given_an_arbitrary_inverse_root():
    r=observe_calibrated_steam_valve(request(),calibration_provider=Curve('nonmonotone'))
    assert r.status=='out_of_scope' and r.Cv is None


def test_outside_calibration_bracket_no_extrapolated_Cv():
    q=request(target_mass_flow_kg_s=1e4)
    r=observe_calibrated_steam_valve(q,calibration_provider=Curve())
    assert r.status=='not_bracketed' and r.Cv is None


def test_zero_pressure_drive_has_no_unique_inverse():
    for target,status in [(0.,'not_identifiable'),(.1,'no_pressure_drive')]:
        r=observe_calibrated_steam_valve(request(ratio=1.,target_mass_flow_kg_s=target),calibration_provider=Curve())
        assert r.status==status and r.Cv is None


def test_wet_or_dry_saturated_inlet_not_promoted_by_a_Cv_curve():
    q=request();nozzle=q.nozzle.model_copy(update={'inlet_definition':'dry_saturated_total_P','stagnation_temperature_K':None})
    r=observe_calibrated_steam_valve(q.model_copy(update={'nozzle':nozzle}),calibration_provider=Curve())
    assert r.status=='out_of_scope' and r.Cv is None
