"""Analytic limits and independent high-precision constitutive references."""
from decimal import Decimal, localcontext
import math

import pytest
from scipy.special import lambertw

from pipeline_integrity.turbulent_constitutive_friction import (
    TurbulentResistanceRequest as Request, evaluate_turbulent_resistance as evaluate,
    WallInteractionInverseRequest as Inverse, required_wall_interaction_parameter as inverse,
)


def calc(model,**parameters):
    return evaluate(Request(model=model,turbulent_regime_coordinate_and_calibration_reviewed=True,**parameters))


@pytest.mark.parametrize("n",[.2,.5,1.,1.5,2.])
@pytest.mark.parametrize("re",[1e4,1e6])
def test_power_law_implicit_root_matches_analytic_lambert_or_exact_n_two(n,re):
    result=calc("power_law",reynolds_coordinate=re,flow_index=n)
    # Independent substitution y=1/sqrt(f) gives y+B*ln(y)=C.
    b=.88*(2-n)/n
    logk=(1-n/2)*math.log(2)+n*(2.83-.2/n**1.2)/.88
    c=.88/n*(logk+math.log(re)-(1-n/2)*math.log(8))-2.83
    y=c if n==2 else b*float(lambertw(math.exp(c/b)/b).real)
    assert result.status=="computed"
    assert result.darcy_friction_factor==pytest.approx(1/y**2,rel=2e-12)


def decimal_viscoplastic_factor(re,he):
    with localcontext() as context:
        context.prec=60
        re,he=Decimal(str(re)),Decimal(str(he))
        low=max(Decimal("0.000001"),8*he/re**2*(1+Decimal("1e-40")))
        high=Decimal(1)
        def residual(f):
            ratio=8*he/(f*re**2)
            return 1/f.sqrt()-(1-ratio)*(Decimal(".88")*(re*f.sqrt()).ln()-Decimal(".8"))-Decimal("2.76")*ratio
        assert residual(low)>0>residual(high)
        for _ in range(180):
            middle=(low+high)/2
            if residual(middle)>0:low=middle
            else:high=middle
        return float((low+high)/2)


@pytest.mark.parametrize("he",[0.,1e4,1e6,1e7])
def test_viscoplastic_root_matches_sixty_digit_direct_bisection(he):
    result=calc("viscoplastic",reynolds_coordinate=1e5,hedstrom_number=he)
    assert result.status=="computed"
    assert result.darcy_friction_factor==pytest.approx(decimal_viscoplastic_factor(1e5,he),rel=2e-12)
    assert 0<=result.yield_to_wall_shear_ratio<1


@pytest.mark.parametrize("re",[1e4,1e6])
def test_zero_yield_limit_matches_smooth_newtonian_log_law(re):
    result=calc("viscoplastic",reynolds_coordinate=re,hedstrom_number=0.)
    y=.88*float(lambertw(re/.88*math.exp(-.8/.88)).real)
    assert result.darcy_friction_factor==pytest.approx(1/y**2,rel=2e-12)


@pytest.mark.parametrize("k,kappa",[(35.,.36),(70.,.4),(120.,.44)])
def test_full_smooth_wall_relation_matches_analytic_lambert_solution(k,kappa):
    result=calc("wall_interaction",reynolds_coordinate=8e4,relative_roughness=0.,
                wall_interaction_parameter=k,mixing_length_constant=kappa)
    w=float(lambertw(k*kappa**2*8e4*math.exp(-137/60)).real)
    assert result.darcy_friction_factor==pytest.approx(8*kappa**2/w**2,rel=2e-12)


@pytest.mark.parametrize("roughness",[.0001,.002])
def test_high_reynolds_limit_matches_independent_fully_rough_expression(roughness):
    k,kappa,a=75.,.4,.31
    result=calc("wall_interaction",reynolds_coordinate=1e15,relative_roughness=roughness,
                wall_interaction_parameter=k,mixing_length_constant=kappa,roughness_wall_constant=a)
    expected=8*kappa**2/(math.log(k*kappa/(a*roughness))-137/60)**2
    assert result.darcy_friction_factor==pytest.approx(expected,rel=2e-8)


@pytest.mark.parametrize("re",[1e4,1e6])
def test_rounded_smooth_variant_keeps_its_own_coefficient_convention(re):
    k=57.
    result=calc("wall_interaction",reynolds_coordinate=re,wall_relation="smooth_rounded",wall_interaction_parameter=k)
    y=.88*float(lambertw(k*re/.88*math.exp(-3.745/.88)).real)
    assert result.darcy_friction_factor==pytest.approx(1/y**2,rel=2e-12)


@pytest.mark.parametrize("variant,roughness",[("mixing_length",0.),("mixing_length",.001),("smooth_rounded",None)])
def test_required_interaction_parameter_returns_target_friction_without_guessing_concentration(variant,roughness):
    target=.02
    required=inverse(Inverse(reynolds_coordinate=1e5,darcy_friction_factor=target,
        relative_roughness=roughness,wall_relation=variant,
        turbulent_regime_coordinate_and_calibration_reviewed=True))
    forward=calc("wall_interaction",reynolds_coordinate=1e5,relative_roughness=roughness,
                 wall_relation=variant,wall_interaction_parameter=required.wall_interaction_parameter)
    assert forward.darcy_friction_factor==pytest.approx(target,rel=2e-12)


def test_reviewed_parameter_monotonicities_preserve_expected_resistance_directions():
    yield_f=[calc("viscoplastic",reynolds_coordinate=1e5,hedstrom_number=he).darcy_friction_factor
             for he in (0.,1e5,1e6)]
    assert yield_f==sorted(yield_f) and len(set(yield_f))==3
    wall_f=[calc("wall_interaction",reynolds_coordinate=1e5,relative_roughness=0.,
                 wall_interaction_parameter=k).darcy_friction_factor for k in (30.,60.,120.)]
    assert wall_f==sorted(wall_f,reverse=True) and len(set(wall_f))==3


@pytest.mark.parametrize("parameters",[
    dict(model="power_law",flow_index=1.),dict(model="viscoplastic",hedstrom_number=0.),
    dict(model="wall_interaction",wall_interaction_parameter=50.,relative_roughness=0.)])
def test_missing_coordinate_and_unreviewed_calibration_are_not_substituted(parameters):
    missing=evaluate(Request(**parameters,turbulent_regime_coordinate_and_calibration_reviewed=True))
    unreviewed=evaluate(Request(**parameters,reynolds_coordinate=1e5))
    assert missing.status==unreviewed.status=="needs_data"
    assert missing.darcy_friction_factor is None


@pytest.mark.parametrize("n",[.1,2.1])
def test_power_index_outside_supported_family_is_not_silently_newtonian(n):
    assert calc("power_law",reynolds_coordinate=1e5,flow_index=n).status=="out_of_scope"


def test_search_and_wall_yield_failures_do_not_become_zero_resistance():
    bracket=calc("power_law",reynolds_coordinate=1e5,flow_index=1.,friction_search_bounds=(.5,1.))
    yielded=calc("viscoplastic",reynolds_coordinate=1e4,hedstrom_number=1e12)
    assert bracket.status=="no_bracket" and bracket.darcy_friction_factor is None
    assert yielded.status=="out_of_scope" and yielded.darcy_friction_factor is None
