"""Independent contour work integral of the elastic near-tip displacement field."""
import math
import pytest
from scipy.integrate import quad
from pipeline_integrity.elastic_fracture_energy import ElasticFractureEnergyRequest as Request,evaluate_elastic_fracture_energy as evaluate

@pytest.mark.parametrize('plane',['plane_stress','plane_strain'])
@pytest.mark.parametrize('nu',[0.,.3,.45])
def test_j_contour_integral_is_radius_independent_and_matches_energy_coordinate(plane,nu):
    intensity=35e6;modulus=200e9;mu=modulus/(2*(1+nu))
    kappa=(3-nu)/(1+nu) if plane=='plane_stress' else 3-4*nu
    q=Request(mode_i_intensity_pa_sqrt_m=intensity,elastic_modulus_pa=modulus,poisson_ratio=nu,plane_state=plane,
        isotropic_mode_i_linear_elastic_plane_state_reviewed=True)
    result=evaluate(q)
    for radius in (.001,.1):
        def integrand(theta):
            c=math.cos(theta);s=math.sin(theta);ch=math.cos(theta/2);sh=math.sin(theta/2)
            scale=intensity/math.sqrt(2*math.pi*radius)
            xx=scale*ch*(1-sh*math.sin(3*theta/2));yy=scale*ch*(1+sh*math.sin(3*theta/2))
            xy=scale*ch*sh*math.cos(3*theta/2)
            if plane=='plane_stress':ex=(xx-nu*yy)/modulus;ey=(yy-nu*xx)/modulus
            else:ex=(1+nu)*((1-nu)*xx-nu*yy)/modulus;ey=(1+nu)*((1-nu)*yy-nu*xx)/modulus
            w=.5*(xx*ex+yy*ey+xy*xy/mu)
            displacement_scale=intensity/(2*mu)*math.sqrt(radius/(2*math.pi));factor=kappa-c
            ux=displacement_scale*ch*factor;uy=displacement_scale*sh*factor
            ux_theta=displacement_scale*(-.5*sh*factor+ch*s)
            uy_theta=displacement_scale*(.5*ch*factor+sh*s)
            ux_x=c*ux/(2*radius)-s*ux_theta/radius
            uy_x=c*uy/(2*radius)-s*uy_theta/radius
            tx=xx*c+xy*s;ty=xy*c+yy*s
            return (w*c-tx*ux_x-ty*uy_x)*radius
        reference=quad(integrand,-math.pi,math.pi,epsabs=1e-6,epsrel=1e-12)[0]
        assert result['elastic_energy_release_j_m2']==pytest.approx(reference,rel=2e-12)

def test_unknown_modulus_and_unreviewed_constraint_withheld():
    q=Request(mode_i_intensity_pa_sqrt_m=1e6,elastic_modulus_pa=None,poisson_ratio=.3,plane_state='plane_strain',isotropic_mode_i_linear_elastic_plane_state_reviewed=True)
    assert evaluate(q)['status']=='needs_data'
    assert evaluate(q.model_copy(update={'elastic_modulus_pa':200e9,'isotropic_mode_i_linear_elastic_plane_state_reviewed':False}))['status']=='needs_data'

def test_zero_intensity_zero_energy():
    q=Request(mode_i_intensity_pa_sqrt_m=0.,elastic_modulus_pa=200e9,poisson_ratio=.3,plane_state='plane_strain',isotropic_mode_i_linear_elastic_plane_state_reviewed=True)
    assert evaluate(q)['elastic_energy_release_j_m2']==0
