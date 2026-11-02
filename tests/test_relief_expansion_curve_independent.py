"""Independent adiabatic density/velocity reference for a supplied Y curve.

The manufactured curve is a declared calibration, not a universal device law.
"""
import math
import pytest
from pipeline_integrity.relief_line_capacity import CalibratedReliefLineRequest,ReliefLossTerm
from pipeline_integrity.relief_expansion_curve import ReliefExpansionPoint,ReliefCurveRequest,observe_curve_driven_relief_capacity


class Curve:
    def __init__(self,bad=None):self.bad=bad;self.calls=0
    def point(self,x,**state):
        self.calls+=1;g=state['heat_capacity_ratio'];r=1-x
        # This explicitly supplied fixture has ideal isentropic expansion and
        # a measured effective discharge reduction1/sqrt(K). That convention
        # is NOT inferred for arbitrary KR or embedded in the production API.
        y=1. if x==0 else math.sqrt(g/(g-1)*math.exp(2/g*math.log(r))*(-math.expm1((g-1)/g*math.log(r)))/x)
        if self.bad=='no_peak':y=1.
        if self.bad=='multiple':y*=.65+.3*math.cos(25*x)
        if self.bad=='drift':y*=1-self.calls*1e-6
        changed=dict(state)
        if self.bad=='state':changed['inlet_temperature_K']+=1
        if self.bad=='K':changed['total_K']+=.1
        if self.bad=='gamma':changed['heat_capacity_ratio']+=.1
        return ReliefExpansionPoint(calibration_id='given-Y-curve',revision='changed' if self.bad=='revision' and self.calls>1 else 'one',
            coefficient_definition_id='given-common-inlet-head-convention',pressure_drop_fraction=x,net_expansion_factor=y,**changed)


def request(gamma=1.4,drop=.8,K=2.,**kw):
    p=1e6;t=600.;gas_R=287.
    s=CalibratedReliefLineRequest(inlet_pressure_Pa_abs=p,back_pressure_Pa_abs=p*(1-drop),inlet_temperature_K=t,
        inlet_density_kg_m3=p/(gas_R*t),heat_capacity_ratio=gamma,common_flow_area_m2=.001,inlet_state_definition_id='given-reservoir-total-state',
        loss_terms=(ReliefLossTerm(id='pipe',K=.4*K,coefficient_basis_id='given-pipe-head-data',applicability_reviewed=True),
            ReliefLossTerm(id='device',K=.6*K,coefficient_basis_id='given-device-head-data',applicability_reviewed=True)),
        same_bore_common_K_reference_and_steady_inlet_state_reviewed=True)
    data=dict(system=s,calibration_id='given-Y-curve',coefficient_definition_id='given-common-inlet-head-convention',
        pressure_drop_fraction_support=(0.,.98),reviewed_curve_state_convention_and_single_sonic_capacity_maximum=True)
    data.update(kw);return ReliefCurveRequest(**data)


@pytest.mark.parametrize('gamma',[1.1,1.4,1.67])
@pytest.mark.parametrize('drop',[.05,.9])
def test_independent_adiabatic_density_velocity_mass_and_sonic_capacity_reference(gamma,drop):
    q=request(gamma,drop);r=observe_curve_driven_relief_capacity(q,expansion_provider=Curve());assert r.status=='computed'
    s=q.system;critical_ratio=(2/(gamma+1))**(gamma/(gamma-1));ratio=max(1-drop,critical_ratio)
    cp=gamma*287/(gamma-1);T=s.inlet_temperature_K*ratio**((gamma-1)/gamma)
    rho=s.inlet_density_kg_m3*ratio**(1/gamma)
    speed=math.sqrt(2*cp*(s.inlet_temperature_K-T))
    expected=s.common_flow_area_m2*rho*speed/math.sqrt(sum(t.K for t in s.loss_terms))
    assert r.capacity.mass_flow_kg_s==pytest.approx(expected,rel=2e-9)
    assert r.critical_pressure_drop_fraction==pytest.approx(1-critical_ratio,abs=3e-8)
    assert r.capacity.regime==('subcritical' if drop<1-critical_ratio else 'limited_by_supplied_sonic_criterion')
    assert not r.universal_expansion_KR_Cd_or_Fanno_mapping_inferred
    assert not r.actual_curve_or_device_calibration_qualified


def test_choked_cap_is_continuous_and_deeper_back_pressure_does_not_increase_flow():
    g=1.4;xcrit=1-(2/(g+1))**(g/(g-1));results=[]
    for x in [xcrit-1e-6,xcrit+1e-6,.95]:
        results.append(observe_curve_driven_relief_capacity(request(g,x),expansion_provider=Curve()).capacity.mass_flow_kg_s)
    assert results[0]==pytest.approx(results[1],rel=2e-10)
    assert results[1]==pytest.approx(results[2],rel=2e-14)


def test_low_Mach_small_drop_limit_and_no_hidden_K_Cd_conversion():
    errors=[]
    for drop in [.01,.001,.0001]:
        q=request(drop=drop,K=4.)
        r=observe_curve_driven_relief_capacity(q,expansion_provider=Curve())
        incompressible=q.system.common_flow_area_m2*math.sqrt(2*q.system.inlet_density_kg_m3*q.system.inlet_pressure_Pa_abs*drop/4)
        errors.append(abs(r.capacity.mass_flow_kg_s/incompressible-1))
    assert errors[1]<.11*errors[0] and errors[2]<.11*errors[1]


@pytest.mark.parametrize('bad',['state','K','gamma','revision'])
def test_curve_point_state_K_gamma_revision_mismatch_not_reused(bad):
    with pytest.raises(ValueError):observe_curve_driven_relief_capacity(request(),expansion_provider=Curve(bad))


def test_absent_curve_or_review_remains_unknown():
    q=request()
    assert observe_curve_driven_relief_capacity(q).status=='needs_data'
    assert observe_curve_driven_relief_capacity(q.model_copy(update={'reviewed_curve_state_convention_and_single_sonic_capacity_maximum':False}),expansion_provider=Curve()).status=='needs_data'


def test_unbracketed_or_multiple_critical_curve_is_not_invented():
    assert observe_curve_driven_relief_capacity(request(),expansion_provider=Curve('no_peak')).status=='insufficient_support'
    assert observe_curve_driven_relief_capacity(request(),expansion_provider=Curve('multiple')).status=='out_of_scope'


def test_zero_drive_and_zero_K_have_distinct_physical_meanings():
    q=request(drop=0.);r=observe_curve_driven_relief_capacity(q)
    assert r.status=='computed' and r.capacity.mass_flow_kg_s==0
    assert observe_curve_driven_relief_capacity(request(K=0.),expansion_provider=Curve()).status=='out_of_scope'


def test_curve_search_nonconvergence_preserved():
    q=request(maximum_search_iterations=1)
    assert observe_curve_driven_relief_capacity(q,expansion_provider=Curve()).status=='not_converged'


def test_actual_precritical_pressure_drop_outside_data_support_withheld():
    q=request(drop=.01,pressure_drop_fraction_support=(.1,.98))
    assert observe_curve_driven_relief_capacity(q,expansion_provider=Curve()).status=='insufficient_support'


def test_revision_unchanged_but_drifting_calibration_detected_at_repeated_point():
    with pytest.raises(ValueError,match='drifted'):observe_curve_driven_relief_capacity(request(),expansion_provider=Curve('drift'))


def test_reference_volume_and_declared_derating_use_same_capacity_owner():
    q=request();s=q.system.model_copy(update={'reference_density_kg_m3':2.,'reference_volume_basis_id':'explicit-gas-reference-PT',
        'supplied_capacity_factor':.8,'capacity_factor_basis_id':'caller-reviewed-device-derating'})
    r=observe_curve_driven_relief_capacity(q.model_copy(update={'system':s}),expansion_provider=Curve())
    assert r.status=='computed'
    assert r.capacity.reference_volume_flow_m3_s==pytest.approx(r.capacity.mass_flow_kg_s/2)
    assert r.capacity.supplied_factor_adjusted_mass_flow_kg_s==pytest.approx(.8*r.capacity.mass_flow_kg_s)


class CriticalBoundaryCurve(Curve):
    def point(self,x,**state):
        g=state['heat_capacity_ratio'];limit=1-(2/(g+1))**(g/(g-1))
        if x>limit+1e-13:raise ValueError('This calibration has no postcritical continuation.')
        return super().point(x,**state)
    def critical_limit(self,**state):
        g=state['heat_capacity_ratio'];limit=1-(2/(g+1))**(g/(g-1))
        return self.point(limit,**state)


@pytest.mark.parametrize('drop',[.05,.9])
def test_model_critical_lookup_needs_no_curve_beyond_sonic_boundary(drop):
    g=1.4;limit=1-(2/(g+1))**(g/(g-1))
    q=request(g,drop,critical_selection='provided_model_critical_limit',pressure_drop_fraction_support=(0.,limit))
    curve=CriticalBoundaryCurve();result=observe_curve_driven_relief_capacity(q,expansion_provider=curve)
    assert result.status=='computed' and result.critical_selection=='provided_model_critical_limit'
    assert result.critical_pressure_drop_fraction==limit
    # One supplied limit point and the actual/capped point only: no guessed
    # data outside the source calibration's subcritical/sonic support.
    assert curve.calls==2
    ratio=max(1-drop,1-limit);s=q.system;rho=s.inlet_density_kg_m3*ratio**(1/g)
    T=s.inlet_temperature_K*ratio**((g-1)/g);speed=math.sqrt(2*g*287/(g-1)*(s.inlet_temperature_K-T))
    assert result.capacity.mass_flow_kg_s==pytest.approx(.001*rho*speed/math.sqrt(2),rel=2e-14)


def test_selected_critical_lookup_method_without_limit_data_preserves_unknown():
    q=request(critical_selection='provided_model_critical_limit')
    assert observe_curve_driven_relief_capacity(q,expansion_provider=Curve()).status=='needs_data'


def test_critical_lookup_outside_declared_support_not_extrapolated():
    q=request(critical_selection='provided_model_critical_limit',pressure_drop_fraction_support=(0.,.2))
    assert observe_curve_driven_relief_capacity(q,expansion_provider=CriticalBoundaryCurve()).status=='insufficient_support'


def test_critical_limit_and_curve_value_must_agree_at_identical_boundary():
    class Inconsistent(CriticalBoundaryCurve):
        def critical_limit(self,**state):
            p=super().critical_limit(**state)
            return p.model_copy(update={'net_expansion_factor':p.net_expansion_factor*.9})
    q=request(critical_selection='provided_model_critical_limit')
    with pytest.raises(ValueError,match='disagree'):observe_curve_driven_relief_capacity(q,expansion_provider=Inconsistent())
