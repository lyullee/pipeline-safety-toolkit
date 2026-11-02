"""Independent shear-field reference of an explicit averaging approximation."""
import math
import pytest
from pipeline_integrity.oil_water_viscosity_estimate import OilWaterViscosityRequest, estimate_oil_water_viscosity


def request(**kw):
    values=dict(oil_volume_m3=.7,water_volume_m3=.3,oil_dynamic_viscosity_Pa_s=.023,
        water_dynamic_viscosity_Pa_s=.0012,temperature_K=303.15,pressure_Pa_abs=2e5,
        component_properties_at_same_state_reviewed=True,volume_average_approximation_selected=True,
        rheology_evidence_id='declared-volume-estimate',requested_shear_rate_s_inv=3.)
    values.update(kw);return OilWaterViscosityRequest(**values)


@pytest.mark.parametrize('oil_fraction',[0.,.1,.37,.8,1.])
@pytest.mark.parametrize('plate_speed',[-.4,0.,.7])
def test_independent_parallel_stripe_couette_force_work_and_dissipation(oil_fraction,plate_speed):
    # Two side-by-side stripes occupy a plate gap H, with interfaces parallel
    # to velocity and its gradient. u=U*y/H satisfies each Newtonian Stokes
    # domain, no-slip walls, velocity continuity and zero interface-normal flux.
    H=.04; plate_area=.8; total_volume=H*plate_area
    q=request(oil_volume_m3=oil_fraction*total_volume,water_volume_m3=(1-oil_fraction)*total_volume,
        requested_shear_rate_s_inv=plate_speed/H)
    r=estimate_oil_water_viscosity(q);assert r.status=='computed'
    force=0.;dissipation=0.
    # Independent local stress/strain integration, not the estimate's kernel.
    for area,mu in [(oil_fraction*plate_area,.023),((1-oil_fraction)*plate_area,.0012)]:
        stress=mu*(plate_speed-0.)/H
        force+=stress*area
        dissipation+=mu*((plate_speed-0.)/H)**2*(area*H)
    assert r.estimated_shear_stress_Pa*plate_area==pytest.approx(force,rel=2e-15,abs=1e-15)
    assert r.estimated_dissipation_W_m3*total_volume==pytest.approx(dissipation,rel=2e-15,abs=1e-15)
    assert force*plate_speed==pytest.approx(dissipation,rel=2e-15,abs=1e-15)
    assert not r.emulsion_morphology_or_three_phase_pressure_loss_qualified


def test_volume_scale_phase_relabel_and_identical_viscosity_limits():
    q=request();a=estimate_oil_water_viscosity(q)
    scaled=estimate_oil_water_viscosity(q.model_copy(update={'oil_volume_m3':q.oil_volume_m3*1e6,'water_volume_m3':q.water_volume_m3*1e6}))
    assert scaled.estimated_dynamic_viscosity_Pa_s==pytest.approx(a.estimated_dynamic_viscosity_Pa_s)
    swapped=estimate_oil_water_viscosity(request(oil_volume_m3=.3,water_volume_m3=.7,oil_dynamic_viscosity_Pa_s=.0012,water_dynamic_viscosity_Pa_s=.023))
    assert swapped.estimated_dynamic_viscosity_Pa_s==pytest.approx(a.estimated_dynamic_viscosity_Pa_s)
    equal=estimate_oil_water_viscosity(request(oil_dynamic_viscosity_Pa_s=.002,water_dynamic_viscosity_Pa_s=.002))
    assert equal.estimated_dynamic_viscosity_Pa_s==pytest.approx(.002)


def test_convex_dynamic_not_density_or_kinematic_mix():
    r=estimate_oil_water_viscosity(request(oil_dynamic_viscosity_Pa_s=1.,water_dynamic_viscosity_Pa_s=.001))
    assert .001<r.estimated_dynamic_viscosity_Pa_s<1.
    # Couette stress in a 1m gap for 1m/s motion equals dynamic viscosity.
    assert r.estimated_dynamic_viscosity_Pa_s==pytest.approx(.7003)


@pytest.mark.parametrize('kw',[dict(component_properties_at_same_state_reviewed=False),
    dict(volume_average_approximation_selected=False),dict(temperature_K=None),dict(pressure_Pa_abs=None),
    dict(rheology_evidence_id=None),dict(oil_dynamic_viscosity_Pa_s=None),dict(water_volume_m3=None)])
def test_unknown_required_data_or_selection_is_not_emulsion_prediction(kw):
    assert estimate_oil_water_viscosity(request(**kw)).status=='needs_data'


@pytest.mark.parametrize('oil_present',[True,False])
def test_absent_component_property_not_inferred(oil_present):
    q=request(oil_volume_m3=1. if oil_present else 0.,water_volume_m3=0. if oil_present else 1.,
        oil_dynamic_viscosity_Pa_s=.023 if oil_present else None,
        water_dynamic_viscosity_Pa_s=None if oil_present else .0012)
    r=estimate_oil_water_viscosity(q)
    assert r.status=='computed'
    assert r.estimated_dynamic_viscosity_Pa_s==(.023 if oil_present else .0012)


def test_zero_volume_and_no_shear_observation():
    assert estimate_oil_water_viscosity(request(oil_volume_m3=0.,water_volume_m3=0.)).status=='out_of_scope'
    r=estimate_oil_water_viscosity(request(requested_shear_rate_s_inv=None))
    assert r.status=='computed' and r.estimated_shear_stress_Pa is None and r.estimated_dissipation_W_m3 is None


@pytest.mark.parametrize('kw',[dict(oil_volume_m3=-1.),dict(oil_dynamic_viscosity_Pa_s=0.),dict(temperature_K=0.),dict(requested_shear_rate_s_inv=math.inf)])
def test_invalid_support_rejected(kw):
    with pytest.raises(ValueError):request(**kw)
