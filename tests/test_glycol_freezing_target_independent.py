"""Chemical-potential phase equilibrium and explicit charged-volume recipes."""
import math
import pytest
from pipeline_integrity.glycol_freezing_target import GlycolFreezingTargetRequest as Request,evaluate_glycol_freezing_target as evaluate


class IdealFreezingCurve:
    provider_identity='manufactured-ideal-solvent-chemical-potential'
    provider_version='constant-fusion-enthalpy'
    mw=.018;Tm=273.15;fusion=6000.;R=8.31446261815324
    mg={'ethylene_glycol':.062,'propylene_glycol':.076}
    def freezing_temperature_K(self,species,w):
        solvent=(1-w)/self.mw;solute=w/self.mg[species];activity=solvent/(solvent+solute)
        return 1/(1/self.Tm-self.R/self.fusion*math.log(activity))


def request(**kw):
    d=dict(glycol_species='ethylene_glycol',target_freezing_temperature_K=260.,mass_fraction_bracket=(0.,.6),
        requested_concentration_basis='mass',reviewed_curve_and_single_decreasing_branch=True,curve_evidence_id='reviewed-curve')
    d.update(kw);return Request(**d)


@pytest.mark.parametrize('species',['ethylene_glycol','propylene_glycol'])
@pytest.mark.parametrize('temperature',[250.,260.,270.])
def test_independent_solvent_chemical_potential_and_species_mole_inventory(species,temperature):
    p=IdealFreezingCurve();q=request(glycol_species=species,target_freezing_temperature_K=temperature);r=evaluate(q,curve_provider=p)
    activity=math.exp(-p.fusion/p.R*(1/temperature-1/p.Tm));ratio=1/activity-1
    mass_g=p.mg[species]*ratio;mass_water=p.mw;expected=mass_g/(mass_g+mass_water)
    assert r.status=='computed'
    assert r.required_glycol_mass_fraction==pytest.approx(expected,abs=2e-13)
    assert abs(r.target_residual_K)<1e-9
    assert not r.field_freeze_protection_qualified


def test_charged_volume_recipe_from_independently_weighed_stocks_not_mixture_additivity():
    q=request(requested_concentration_basis='charged_component_volume',glycol_stock_density_kg_m3=1120.,water_stock_density_kg_m3=997.,
        stock_reference_temperature_K=293.15,stock_reference_pressure_Pa_abs=101000.,same_state_pure_stock_density_basis_reviewed=True)
    r=evaluate(q,curve_provider=IdealFreezingCurve());w=r.required_glycol_mass_fraction
    glycol_mass=10*w;water_mass=10*(1-w);vg=glycol_mass/1120;vw=water_mass/997
    assert r.required_charged_glycol_volume_fraction==pytest.approx(vg/(vg+vw),rel=2e-14)
    assert not r.actual_bulk_volume_additivity_or_pressure_dependence_inferred


def test_missing_volume_basis_preserves_known_mass_and_lower_bound_requires_no_density():
    r=evaluate(request(requested_concentration_basis='charged_component_volume'),curve_provider=IdealFreezingCurve())
    assert r.status=='needs_data' and r.required_glycol_mass_fraction is not None and r.required_charged_glycol_volume_fraction is None
    zero=evaluate(request(target_freezing_temperature_K=280.,requested_concentration_basis='charged_component_volume'),curve_provider=IdealFreezingCurve())
    assert zero.status=='computed' and zero.required_glycol_mass_fraction==zero.required_charged_glycol_volume_fraction==0
    assert zero.target_state=='at_lower_bound'


@pytest.mark.parametrize('change',[{'glycol_species':None},{'target_freezing_temperature_K':None},{'reviewed_curve_and_single_decreasing_branch':False}])
def test_unknown_identity_target_or_curve_is_not_a_default_glycol(change):
    assert evaluate(request(**change),curve_provider=IdealFreezingCurve()).status=='needs_data'


def test_target_outside_branch_and_bad_monotonicity_are_not_extrapolated():
    assert evaluate(request(target_freezing_temperature_K=200.),curve_provider=IdealFreezingCurve()).status=='not_bracketed'
    class Bad(IdealFreezingCurve):
        def freezing_temperature_K(self,s,w):return 250+10*w
    assert evaluate(request(),curve_provider=Bad()).status=='out_of_scope'


def test_root_iteration_failure_is_explicit():
    assert evaluate(request(maximum_root_iterations=1),curve_provider=IdealFreezingCurve()).status=='not_converged'


@pytest.mark.parametrize('species',['ethylene_glycol','propylene_glycol'])
def test_native_property_branch_phase_target_and_support_guards(species):
    r=evaluate(request(glycol_species=species))
    assert r.status=='computed' and 0<r.required_glycol_mass_fraction<.6 and abs(r.target_residual_K)<1e-8
    assert 'CoolProp' in r.provider_identity
    outside=evaluate(request(glycol_species=species,mass_fraction_bracket=(0.,.8)))
    assert outside.status=='out_of_scope'
