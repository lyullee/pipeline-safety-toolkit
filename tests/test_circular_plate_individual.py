"""Independent axisymmetric virtual-work reference and pressure support equilibrium."""
import math
import pytest
from scipy.integrate import quad
from pipeline_integrity.circular_plate import ClampedCircularPlateRequest as Q,evaluate_clamped_circular_plate as solve,ClampedPlateStressCeilingRequest as I,invert_clamped_plate_stress_ceiling as invert

def make(**kw):return Q(**(dict(support_radius_m=.1,effective_thickness_m=.003,differential_pressure_Pa=1000.,elastic_modulus_Pa=2e11,poisson_ratio=.3,query_radii_m=(0.,.04,.1),maximum_reviewed_thickness_to_radius=.1,maximum_reviewed_center_deflection_to_thickness=.2,uniform_pressure_linear_elastic_small_deflection_clamp_reviewed=True)|kw))

@pytest.mark.parametrize('pressure,nu',[(1000.,0.),(1000.,.25),(1000.,.45),(-1000.,.3),(0.,.3)])
def test_independent_virtual_work_and_surface_curvature(pressure,nu):
 q=make(differential_pressure_Pa=pressure,poisson_ratio=nu);r=solve(q);assert r.status=='computed';v=r.values;a=.1;t=.003;E=2e11;D=E*t**3/(12*(1-nu**2))
 def phi(x):return (1-(x/a)**2)**2
 def dd(x):return -4/a**2+12*x*x/a**4
 def radial(x):return -4/a**2+4*x*x/a**4
 load=quad(lambda x:pressure*phi(x)*2*math.pi*x,0.,a,epsabs=1e-10,epsrel=1e-12)[0]
 stiffness=quad(lambda x:D*(dd(x)**2+radial(x)**2+2*nu*dd(x)*radial(x))*2*math.pi*x,0.,a,epsabs=1e-6,epsrel=1e-12)[0]
 displacement=load/stiffness
 assert v['center_displacement_along_load_m']==pytest.approx(displacement,rel=3e-13,abs=1e-20)
 for row in v['observations']:
  x=row['radius_m'];mr=-D*displacement*(dd(x)+nu*radial(x));mt=-D*displacement*(radial(x)+nu*dd(x))
  assert row['displacement_along_load_m']==pytest.approx(displacement*phi(x),rel=3e-13,abs=1e-20)
  assert row['radial_bending_moment_N']==pytest.approx(mr,rel=3e-13,abs=1e-13)
  assert row['circumferential_bending_moment_N']==pytest.approx(mt,rel=3e-13,abs=1e-13)
  assert row['selected_face_radial_bending_stress_Pa']==pytest.approx(6*mr/t**2,rel=3e-13,abs=1e-7)
 pressure_force=quad(lambda x:pressure*2*math.pi*x,0.,a)[0]
 assert v['total_support_reaction_along_load_N']==pytest.approx(-pressure_force,abs=1e-12)
 assert 2*math.pi*a*v['edge_radial_shear_along_load_N_m']==pytest.approx(-pressure_force,abs=1e-12)

@pytest.mark.parametrize('scale',[.25,3.])
def test_length_similarity_and_elastic_modulus(scale):
 q=make();a=solve(q).values
 qq=q.model_copy(update={'support_radius_m':.1*scale,'effective_thickness_m':.003*scale,'query_radii_m':tuple(x*scale for x in q.query_radii_m)})
 b=solve(qq).values
 assert b['center_displacement_along_load_m']==pytest.approx(a['center_displacement_along_load_m']*scale,rel=3e-13)
 assert b['maximum_surface_normal_bending_stress_magnitude_Pa']==pytest.approx(a['maximum_surface_normal_bending_stress_magnitude_Pa'],rel=3e-13)
 c=solve(q.model_copy(update={'elastic_modulus_Pa':4e11})).values
 assert c['center_displacement_along_load_m']==pytest.approx(a['center_displacement_along_load_m']/2,rel=3e-13)

@pytest.mark.parametrize('pressure',[1000.,-1000.])
def test_supplied_nominal_ceiling_inverse_then_response(pressure):
 q=I(support_radius_m=.1,differential_pressure_Pa=pressure,supplied_nominal_bending_stress_ceiling_Pa=3e6,additional_nonstructural_thickness_m=.002,clamped_uniform_load_and_nominal_stress_basis_reviewed=True)
 inv=invert(q).values;t=inv['algebraic_effective_thickness_m'];r=solve(make(effective_thickness_m=t,differential_pressure_Pa=pressure));assert r.status=='computed'
 assert r.values['maximum_surface_normal_bending_stress_magnitude_Pa']==pytest.approx(3e6,rel=3e-13)
 assert inv['total_with_supplied_additional_thickness_m']==pytest.approx(t+.002)

def test_zero_pressure_inverse_is_only_algebraic_zero():
 v=invert(I(support_radius_m=.1,differential_pressure_Pa=0.,supplied_nominal_bending_stress_ceiling_Pa=3e6,additional_nonstructural_thickness_m=.002,clamped_uniform_load_and_nominal_stress_basis_reviewed=True)).values
 assert v['algebraic_effective_thickness_m']==0 and v['total_with_supplied_additional_thickness_m']==.002

@pytest.mark.parametrize('field',['elastic_modulus_Pa','poisson_ratio','maximum_reviewed_thickness_to_radius','maximum_reviewed_center_deflection_to_thickness'])
def test_unknown_material_or_model_support_withheld(field):assert solve(make(**{field:None})).status=='needs_data'

@pytest.mark.parametrize('kw',[{'maximum_reviewed_thickness_to_radius':.01},{'maximum_reviewed_center_deflection_to_thickness':1e-8}])
def test_thick_or_large_deflection_outside_reviewed_support(kw):assert solve(make(**kw)).status=='out_of_scope'

@pytest.mark.parametrize('kw',[{'poisson_ratio':.5},{'effective_thickness_m':0.},{'query_radii_m':(.2,)}])
def test_invalid_material_geometry(kw):
 with pytest.raises(ValueError):solve(make(**kw))
