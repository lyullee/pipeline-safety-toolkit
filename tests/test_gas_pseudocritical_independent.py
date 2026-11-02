"""Pseudo-property estimation arithmetic, unit bases and explicit missing data."""
from decimal import Decimal,localcontext
import pytest
from pipeline_integrity.gas_pseudocritical import CriticalMixtureComponent as C,PseudocriticalRequest as R,estimate_gas_pseudocritical as evaluate


def kay(**kw):
    args=dict(method='kay_mole_average',components=(C(id='a',mole_fraction=.25,critical_temperature_K=100.,critical_pressure_Pa_abs=2e6),
        C(id='b',mole_fraction=.75,critical_temperature_K=200.,critical_pressure_Pa_abs=4e6)),approximation_and_component_basis_reviewed=True)
    args.update(kw);return R(**args)


def gravity(**kw):
    args=dict(method='gravity_linear_estimate',relative_gas_gravity_air=.73,reviewed_gas_gravity_support=(.5,1.2),approximation_and_component_basis_reviewed=True)
    args.update(kw);return R(**args)


def test_kay_authored_mole_weighted_component_reference():
    r=evaluate(kay());assert r.adjusted_pseudo_temperature_K==175
    assert r.adjusted_pseudo_pressure_Pa_abs==3.5e6
    assert r.sour_temperature_shift_K==0
    assert not r.actual_mixture_critical_point_established and not r.equation_of_state_or_phase_established


def test_kay_pure_limit_and_permutation():
    q=kay();r=evaluate(q)
    assert evaluate(q.model_copy(update={'components':q.components[::-1]}))==r
    cs=(q.components[0].model_copy(update={'mole_fraction':1.}),q.components[1].model_copy(update={'mole_fraction':0.}))
    a=evaluate(kay(components=cs));assert a.adjusted_pseudo_temperature_K==100 and a.adjusted_pseudo_pressure_Pa_abs==2e6


def test_gravity_linear_fit_si_unit_conversion_independent_force_area_basis():
    q=gravity();r=evaluate(q)
    # lbf/in² converted independently from defined force and square-inch area.
    psi_pa=4.4482216152605/.0254**2
    trankine=170.491+307.344*.73;ppsi=709.604-58.718*.73
    assert r.adjusted_pseudo_temperature_K==pytest.approx(trankine/1.8,rel=2e-15)
    assert r.adjusted_pseudo_pressure_Pa_abs==pytest.approx(ppsi*psi_pa,rel=1e-12)


def test_sour_shift_high_precision_reference_and_pressure_ratio():
    q=kay(carbon_dioxide_mole_fraction=.125,hydrogen_sulfide_mole_fraction=.0625,sour_correction_applicability_reviewed=True)
    r=evaluate(q)
    with localcontext() as context:
        context.prec=50
        a=Decimal(3)/16;b=Decimal(1)/16
        eps=(120*(a**Decimal('.9')-a**Decimal('1.6'))+15*(b.sqrt()-b**4))*5/9
        adjusted=Decimal(175)-eps
        pressure=Decimal(3500000)*adjusted/(Decimal(175)+b*(1-b)*eps)
    assert r.sour_temperature_shift_K==pytest.approx(float(eps),rel=2e-15)
    assert r.adjusted_pseudo_temperature_K==pytest.approx(float(adjusted),rel=2e-15)
    assert r.adjusted_pseudo_pressure_Pa_abs==pytest.approx(float(pressure),rel=2e-15)
    assert r.adjusted_pseudo_temperature_K<r.uncorrected_pseudo_temperature_K


def test_sweet_limit_of_explicit_sour_correction():
    a=evaluate(kay());b=evaluate(kay(carbon_dioxide_mole_fraction=0.,hydrogen_sulfide_mole_fraction=0.,sour_correction_applicability_reviewed=True))
    assert a==b


def test_missing_basis_or_unreviewed_approximation_not_composition_inferred():
    assert evaluate(gravity(relative_gas_gravity_air=None)).status=='needs_data'
    assert evaluate(gravity(reviewed_gas_gravity_support=None)).status=='needs_data'
    assert evaluate(kay(components=())).status=='needs_data'
    assert evaluate(kay(approximation_and_component_basis_reviewed=False)).status=='needs_data'
    assert evaluate(kay(carbon_dioxide_mole_fraction=.1,hydrogen_sulfide_mole_fraction=0.)).status=='needs_data'


def test_outside_gravity_support_or_nonpositive_fit_is_rejected():
    assert evaluate(gravity(relative_gas_gravity_air=1.3)).status=='out_of_scope'
    assert evaluate(gravity(relative_gas_gravity_air=20.,reviewed_gas_gravity_support=(19.,21.))).status=='out_of_scope'


@pytest.mark.parametrize('changes',[{'relative_gas_gravity_air':.7},{'carbon_dioxide_mole_fraction':.1},
    {'carbon_dioxide_mole_fraction':.8,'hydrogen_sulfide_mole_fraction':.3},
    {'components':(C(id='a',mole_fraction=.4,critical_temperature_K=100.,critical_pressure_Pa_abs=1e6),)}])
def test_invalid_mixed_or_incomplete_bases(changes):
    with pytest.raises(ValueError):kay(**changes)
