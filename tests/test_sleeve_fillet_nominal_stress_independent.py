"""Thin-joint force balance, pure-shear invariants and finite annulus convergence."""
import math
import pytest
from scipy.integrate import quad
from pipeline_integrity.contracts import RunContext,EvidenceRef
from pipeline_integrity.adapters import MaterialClient
from pipeline_integrity.sleeve_fillet_nominal_stress import SleeveFilletStressRequest as Request,evaluate_sleeve_fillet_nominal_stress as evaluate
C=RunContext(asset_id='joint',scenario_id='uniform-shear-coordinate',clock_id='static',origin='synthetic',evidence=(EvidenceRef(id='authored',citation='Authored unit factors for uniform shear reference only',origin='synthetic',license_id='MIT'),))
def request(force=1000.,gap=.002,width=.005):
    return Request(header_outside_diameter_m=.3,radial_header_to_sleeve_outer_surface_distance_m=width,
        radial_header_to_sleeve_inner_surface_gap_m=gap,longitudinal_joint_transfer_force_n=force,
        supplied_peak_shear_to_nominal_factor=1.,supplied_peak_principal_to_nominal_factor=1.,maximum_reviewed_effective_width_to_diameter=.05,
        peak_factor_geometry_material_support_basis_id='authored-uniform-shear-not-actual-fillet',thin_joint_geometry_transfer_force_and_peak_factor_support_reviewed=True)

@pytest.mark.parametrize('force',[-1000.,0.,1000.])
def test_unit_factors_match_independent_uniform_shear_invariants_and_circumferential_force(force):
    q=request(force);r=evaluate(q);assert r['status']=='computed'
    nominal=r['signed_nominal_shear_coordinate_pa'];radius=q.header_outside_diameter_m/2;width=r['effective_joint_width_m']
    force_integral=quad(lambda theta:nominal*radius*width,0,2*math.pi)[0]
    assert force_integral==pytest.approx(force,abs=1e-10)
    native=MaterialClient().evaluate('mechanics.plane_stress',dict(stress_x_Pa=0.,stress_y_Pa=0.,shear_xy_Pa=nominal),C,location_id='nominal-reference')
    assert native.status=='computed'
    values=native.payload['values']
    assert r['provider_scaled_peak_principal_magnitude_pa']==pytest.approx(values['principal_max_Pa'])
    assert r['provider_scaled_peak_shear_magnitude_pa']==pytest.approx((values['principal_max_Pa']-values['principal_min_Pa'])/2)
    assert r['peak_factor_calibration_local_stress_field_or_acceptance_qualified'] is False

@pytest.mark.parametrize('gap',[0.,.0025])
def test_clearance_loss_preserves_force_and_supports_explicit_factor_provider(gap):
    q=request(gap=gap).model_copy(update={'supplied_peak_shear_to_nominal_factor':1.3,'supplied_peak_principal_to_nominal_factor':1.8})
    r=evaluate(q);remaining=q.radial_header_to_sleeve_outer_surface_distance_m-gap
    assert r['thin_joint_reference_transfer_area_m2']*r['signed_nominal_shear_coordinate_pa']==pytest.approx(q.longitudinal_joint_transfer_force_n)
    assert r['provider_scaled_peak_principal_magnitude_pa']/r['provider_scaled_peak_shear_magnitude_pa']==pytest.approx(1.8/1.3)

def test_nominal_area_approaches_exact_finite_annulus_in_thin_limit():
    errors=[]
    for ratio in (.01,.001,.0001):
        q=request(gap=0.,width=.3*ratio);r=evaluate(q);radius=.15;width=r['effective_joint_width_m']
        area=quad(lambda radial:2*math.pi*radial,radius,radius+width)[0]
        exact=q.longitudinal_joint_transfer_force_n/area
        errors.append(abs(r['signed_nominal_shear_coordinate_pa']/exact-1))
    assert errors[2]<errors[1]<errors[0] and errors[-1]<.00011

def test_missing_peak_provider_is_not_defaulted():
    assert evaluate(request().model_copy(update={'supplied_peak_principal_to_nominal_factor':None}))['status']=='needs_data'

def test_clearance_cannot_remove_all_effective_width():
    with pytest.raises(ValueError,match='positive effective'):evaluate(request(gap=.005))

def test_declared_thin_geometry_support_is_retained():
    assert evaluate(request(width=.03,gap=0.))['status']=='out_of_scope'
