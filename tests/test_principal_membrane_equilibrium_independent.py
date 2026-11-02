"""Independent membrane patch traction integrals and signed normal balance."""
import math
import pytest
from scipy.integrate import quad
from pipeline_integrity.principal_membrane_equilibrium import PrincipalMembraneRequest as Request,evaluate_principal_membrane as evaluate


def request(**kw):
 d=dict(id='authored',pressure_on_negative_normal_side_pa_abs=140000.,pressure_on_positive_normal_side_pa_abs=100000.,
  principal_curvature_1_per_m=.5,principal_curvature_2_per_m=.5,mode='equal_resultants',
  equilibrium_absolute_tolerance_pa=1e-8,geometry_load_and_membrane_normal_convention_reviewed=True,
  additional_constraint_evidence_id='caller-declared-local-constraint')
 d.update(kw);return Request(**d)

@pytest.mark.parametrize('radius,angle',[(2.,.2),(5.,.7)])
def test_sphere_cap_pressure_and_boundary_traction(radius,angle):
 q=request(principal_curvature_1_per_m=1/radius,principal_curvature_2_per_m=1/radius,supplied_uniform_membrane_thickness_m=.004)
 v=evaluate(q)['values'];dp=q.pressure_on_negative_normal_side_pa_abs-q.pressure_on_positive_normal_side_pa_abs
 pressure_force=quad(lambda theta:dp*2*math.pi*radius**2*math.sin(theta)*math.cos(theta),0,angle)[0]
 boundary_normal=2*math.pi*radius*math.sin(angle)*v['resultant_1_n_m']*math.sin(angle)
 assert boundary_normal==pytest.approx(pressure_force,rel=2e-13)
 assert v['uniform_membrane_stress_1_pa']==pytest.approx(dp*radius/(2*.004))

@pytest.mark.parametrize('radius,angle',[(.8,.3),(3.,.6)])
def test_cylinder_pressure_patch_and_hoop_traction(radius,angle):
 length=.7;q=request(principal_curvature_1_per_m=0.,principal_curvature_2_per_m=1/radius,mode='solve_resultant_2',supplied_resultant_1_n_m=-900.)
 v=evaluate(q)['values'];dp=q.pressure_on_negative_normal_side_pa_abs-q.pressure_on_positive_normal_side_pa_abs
 pressure_force=quad(lambda theta:dp*radius*length*math.cos(theta),-angle,angle)[0]
 assert 2*v['resultant_2_n_m']*length*math.sin(angle)==pytest.approx(pressure_force)
 assert v['resultant_1_n_m']==-900. # Longitudinal value is extra input, not inferred from normal balance.

@pytest.mark.parametrize('k1,k2,n1,n2',[(.4,.7,30000.,50000.),(.8,-.3,50000.,-20000.),(-.6,-.2,-40000.,-50000.)])
def test_infinitesimal_anisotropic_surface_edge_traction_convergence(k1,k2,n1,n2):
 # Local graph z=-(k1*x²+k2*y²)/2: outward normal curvatures at origin are k1,k2.
 # Finite edge tangents are integrated; only the shrinking-patch limit is claimed.
 dp=k1*n1+k2*n2;q=request(pressure_on_negative_normal_side_pa_abs=100000.+dp,principal_curvature_1_per_m=k1,principal_curvature_2_per_m=k2,mode='observe',supplied_resultant_1_n_m=n1,supplied_resultant_2_n_m=n2)
 v=evaluate(q)['values'];errors=[]
 for a in (.04,.02,.01,.005,.0025):
  edge_y=quad(lambda y:math.sqrt(1+(k2*y)**2),-a,a)[0]
  edge_x=quad(lambda x:math.sqrt(1+(k1*x)**2),-a,a)[0]
  inward=2*n1*k1*a/math.sqrt(1+(k1*a)**2)*edge_y+2*n2*k2*a/math.sqrt(1+(k2*a)**2)*edge_x
  errors.append(abs(inward/(4*a*a)-v['balanced_pressure_pa']))
 assert all(a>b for a,b in zip(errors,errors[1:]))
 assert errors[-1]/max(abs(dp),1.)<2e-6
 assert abs(v['normal_force_residual_pa'])<1e-8

@pytest.mark.parametrize('mode',['solve_resultant_1','solve_resultant_2'])
def test_anisotropic_single_unknown_inverse(mode):
 k1=.7;k2=-.4;n1=17000.;n2=-23000.;dp=k1*n1+k2*n2
 d=dict(mode=mode,principal_curvature_1_per_m=k1,principal_curvature_2_per_m=k2,pressure_on_negative_normal_side_pa_abs=100000.+dp)
 d['supplied_resultant_2_n_m' if mode=='solve_resultant_1' else 'supplied_resultant_1_n_m']=n2 if mode=='solve_resultant_1' else n1
 v=evaluate(request(**d))['values'];assert v['resultant_1_n_m']==pytest.approx(n1);assert v['resultant_2_n_m']==pytest.approx(n2)


def test_normal_reversal_preserves_physical_tension():
 a=evaluate(request())['values'];b=evaluate(request(pressure_on_negative_normal_side_pa_abs=100000.,pressure_on_positive_normal_side_pa_abs=140000.,principal_curvature_1_per_m=-.5,principal_curvature_2_per_m=-.5))['values']
 assert a['resultant_1_n_m']==b['resultant_1_n_m'];assert a['balanced_pressure_pa']==-b['balanced_pressure_pa']


def test_thickness_changes_stress_coordinate_not_resultant():
 a=evaluate(request(supplied_uniform_membrane_thickness_m=.002))['values'];b=evaluate(request(supplied_uniform_membrane_thickness_m=.004))['values']
 assert a['resultant_1_n_m']==b['resultant_1_n_m'];assert a['uniform_membrane_stress_1_pa']==2*b['uniform_membrane_stress_1_pa']


def test_equal_resultant_requires_extra_constraint_evidence():
 assert evaluate(request(additional_constraint_evidence_id=None))['status']=='needs_data'

@pytest.mark.parametrize('mode,k1,k2,p,status',[
 ('equal_resultants',0.,0.,100000.,'underdetermined'),('equal_resultants',0.,0.,140000.,'inconsistent'),
 ('equal_resultants',.5,-.5,100000.,'underdetermined'),('solve_resultant_1',0.,.5,140000.,'inconsistent')])
def test_zero_coefficient_no_arbitrary_unique_solution(mode,k1,k2,p,status):
 d=dict(mode=mode,principal_curvature_1_per_m=k1,principal_curvature_2_per_m=k2,pressure_on_negative_normal_side_pa_abs=p)
 if mode=='solve_resultant_1':d['supplied_resultant_2_n_m']=0.
 assert evaluate(request(**d))['status']==status


def test_observed_imbalance_is_exposed_not_forced_to_pass():
 v=evaluate(request(mode='observe',supplied_resultant_1_n_m=1.,supplied_resultant_2_n_m=1.))['values']
 assert not v['equilibrium_within_supplied_tolerance'];assert v['normal_force_residual_pa']==-39999.


def test_missing_second_relation_and_no_ignored_supplied_unknown():
 assert evaluate(request(mode='observe'))['status']=='underdetermined'
 with pytest.raises(ValueError):request(supplied_resultant_1_n_m=1.)
