"""Independent nominal cap equilibrium and curved-fibre elastic work references."""
import math
import pytest
from scipy.integrate import quad
from pipeline_integrity.pressure_bending_budget import PressureBendingBudgetRequest as Request,evaluate_pressure_bending_budget as evaluate


def request(**kw):
 d=dict(id='authored',inside_pressure_pa_abs=4.1e6,exterior_pressure_pa_abs=1e5,actual_outside_diameter_m=.3,
  actual_wall_thickness_m=.009,pressure_diameter_basis='outside',young_modulus_pa=190e9,
  supplied_longitudinal_tensile_stress_ceiling_pa=220e6,signed_prescribed_curvature_per_m=.001,
  supplied_allowable_and_membrane_free_closed_caps_elastic_bending_reviewed=True,stress_ceiling_evidence_id='caller-authored')
 d.update(kw);return Request(**d)

@pytest.mark.parametrize('basis,diameter,wall',[('outside',.3,.009),('mean_from_outside',.2,.006),('outside',.6,.012)])
def test_declared_nominal_cap_force_balance(basis,diameter,wall):
 q=request(pressure_diameter_basis=basis,actual_outside_diameter_m=diameter,actual_wall_thickness_m=wall)
 v=evaluate(q)['values'];reference=diameter if basis=='outside' else diameter-wall
 cap_force=quad(lambda r:2*math.pi*r*(q.inside_pressure_pa_abs-q.exterior_pressure_pa_abs),0,reference/2)[0]
 nominal_barrel_area=math.pi*reference*wall
 assert v['nominal_closed_cap_axial_stress_pa']*nominal_barrel_area==pytest.approx(cap_force)

@pytest.mark.parametrize('curvature',[-.003,0.,.002])
def test_actual_fibre_arc_strain_and_annular_resultants(curvature):
 q=request(signed_prescribed_curvature_per_m=curvature);v=evaluate(q)['values'];ro=q.actual_outside_diameter_m/2;ri=ro-q.actual_wall_thickness_m
 sigma0=v['nominal_closed_cap_axial_stress_pa'];sigma_plus=v['positive_coordinate_fibre_longitudinal_stress_pa'];sigma_minus=v['negative_coordinate_fibre_longitudinal_stress_pa']
 if curvature:
  radius=1/abs(curvature);angle=.04;y=math.copysign(ro,curvature)
  arc_strain=((radius+ro)*angle-radius*angle)/(radius*angle)
  assert sigma0+q.young_modulus_pa*arc_strain==pytest.approx(max(sigma_plus,sigma_minus))
 # Integrate the returned linear fibre field over the actual annulus.
 slope=(sigma_plus-sigma_minus)/(2*ro)
 normal=quad(lambda r:quad(lambda theta:(sigma0+slope*r*math.cos(theta))*r,0,math.tau)[0],ri,ro)[0]
 # Uniform axial stress has exactly zero centroidal moment by odd symmetry.
 moment=quad(lambda r:quad(lambda theta:slope*r**3*math.cos(theta)**2,0,math.tau)[0],ri,ro)[0]
 assert normal==pytest.approx(sigma0*math.pi*(ro*ro-ri*ri),rel=2e-12)
 assert moment==pytest.approx(q.young_modulus_pa*curvature*math.pi/4*(ro**4-ri**4),abs=1e-7,rel=2e-12)

@pytest.mark.parametrize('basis',['outside','mean_from_outside'])
def test_inverse_ceiling_exact_on_both_curvature_signs(basis):
 q=request(pressure_diameter_basis=basis,signed_prescribed_curvature_per_m=None);v=evaluate(q)['values'];k=v['maximum_absolute_curvature_per_m']
 for sign in (-1,1):
  x=evaluate(request(pressure_diameter_basis=basis,signed_prescribed_curvature_per_m=sign*k))['values']
  assert x['maximum_longitudinal_tensile_stress_pa']==pytest.approx(q.supplied_longitudinal_tensile_stress_ceiling_pa)
  assert abs(x['tensile_ceiling_residual_pa'])<1e-6
 assert v['minimum_absolute_bend_radius_m']*k==pytest.approx(1)

@pytest.mark.parametrize('pressure_shift',[2e5,9e6])
def test_common_absolute_pressure_datum(pressure_shift):
 q=request();a=evaluate(q)['values'];b=evaluate(request(inside_pressure_pa_abs=q.inside_pressure_pa_abs+pressure_shift,exterior_pressure_pa_abs=q.exterior_pressure_pa_abs+pressure_shift))['values']
 assert a==b


def test_original_nominal_geometry_kept_during_closed_cap_thin_limit():
 errors=[]
 for t_over_d in (.03,.01,.003,.001):
  q=request(actual_wall_thickness_m=.3*t_over_d);v=evaluate(q)['values'];ro=.15;ri=ro-q.actual_wall_thickness_m
  exact=(q.inside_pressure_pa_abs-q.exterior_pressure_pa_abs)*ri*ri/(ro*ro-ri*ri)
  errors.append(abs(v['nominal_closed_cap_axial_stress_pa']/exact-1))
 assert errors[0]>.05
 assert all(a>b>0 for a,b in zip(errors,errors[1:]))
 assert errors[-1]<.004


def test_exhausted_tensile_budget_is_not_finite_radius():
 q=request();stress=evaluate(q)['values']['nominal_closed_cap_axial_stress_pa']
 assert evaluate(request(supplied_longitudinal_tensile_stress_ceiling_pa=stress))['values']['inverse_status']=='straight_only'
 assert evaluate(request(supplied_longitudinal_tensile_stress_ceiling_pa=.9*stress))['values']['inverse_status']=='no_curvature_satisfies_tensile_ceiling'


def test_dimension_scaling_same_stress_radius_scales():
 q=request();a=evaluate(q)['values'];b=evaluate(request(actual_outside_diameter_m=.6,actual_wall_thickness_m=.018,signed_prescribed_curvature_per_m=.0005))['values']
 assert b['nominal_closed_cap_axial_stress_pa']==pytest.approx(a['nominal_closed_cap_axial_stress_pa'])
 assert b['maximum_longitudinal_tensile_stress_pa']==pytest.approx(a['maximum_longitudinal_tensile_stress_pa'])
 assert b['minimum_absolute_bend_radius_m']==pytest.approx(2*a['minimum_absolute_bend_radius_m'])


def test_zero_pressure_pure_bending_and_tensile_only_meaning():
 q=request(inside_pressure_pa_abs=1e5,exterior_pressure_pa_abs=1e5,signed_prescribed_curvature_per_m=-.001);v=evaluate(q)['values']
 assert v['nominal_closed_cap_axial_stress_pa']==0
 assert v['positive_coordinate_fibre_longitudinal_stress_pa']==-v['negative_coordinate_fibre_longitudinal_stress_pa']
 assert not v['compression_or_multiaxial_capacity_assessed']


def test_unreviewed_and_external_dominated_preserve_withhold():
 assert evaluate(request(supplied_allowable_and_membrane_free_closed_caps_elastic_bending_reviewed=False))['status']=='needs_data'
 assert evaluate(request(inside_pressure_pa_abs=1e5,exterior_pressure_pa_abs=2e5))['status']=='out_of_scope'

