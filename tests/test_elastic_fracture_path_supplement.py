"""Independent noncircular traction-work contours around an elastic crack tip."""
import math

import pytest
from scipy.integrate import quad

from pipeline_integrity.elastic_fracture_energy import (
    ElasticFractureEnergyRequest, evaluate_elastic_fracture_energy,
)


@pytest.mark.parametrize('plane', ['plane_stress', 'plane_strain'])
@pytest.mark.parametrize('nu', [-.3, .25, .49])
@pytest.mark.parametrize('aspect,tstress', [(.2, -40e6), (4., 65e6)])
def test_elliptical_work_contour_and_nonsingular_stress_invariance(plane, nu, aspect, tstress):
    # A uniform nonsingular longitudinal stress is traction-free on both crack
    # faces. It changes local elastic energy without changing tip release work.
    k, young = 27e6, 180e9
    mu = young/(2*(1+nu))
    kappa = (3-nu)/(1+nu) if plane == 'plane_stress' else 3-4*nu
    request = ElasticFractureEnergyRequest(
        mode_i_intensity_pa_sqrt_m=k, elastic_modulus_pa=young,
        poisson_ratio=nu, plane_state=plane,
        isotropic_mode_i_linear_elastic_plane_state_reviewed=True,
    )
    predicted = evaluate_elastic_fracture_energy(request)['elastic_energy_release_j_m2']
    references = []
    for a in [.003, .03]:
        b = a*aspect

        def contour(phi, component):
            x, y = a*math.cos(phi), b*math.sin(phi)
            dx, dy = -a*math.sin(phi), b*math.cos(phi)
            radius, theta = math.hypot(x, y), math.atan2(y, x)
            c, s = math.cos(theta), math.sin(theta)
            ch, sh = math.cos(theta/2), math.sin(theta/2)
            stress_scale = k/math.sqrt(2*math.pi*radius)
            xx = stress_scale*ch*(1-sh*math.sin(1.5*theta))+tstress
            yy = stress_scale*ch*(1+sh*math.sin(1.5*theta))
            xy = stress_scale*ch*sh*math.cos(1.5*theta)
            if plane == 'plane_stress':
                ex, ey = (xx-nu*yy)/young, (yy-nu*xx)/young
                uniform_ux_x = tstress/young
            else:
                ex = (1+nu)*((1-nu)*xx-nu*yy)/young
                ey = (1+nu)*((1-nu)*yy-nu*xx)/young
                uniform_ux_x = (1-nu*nu)*tstress/young
            energy = .5*(xx*ex+yy*ey+xy*xy/mu)
            factor = kappa-c
            scale = k/(2*mu)*math.sqrt(radius/(2*math.pi))
            ux, uy = scale*ch*factor, scale*sh*factor
            ux_theta = scale*(-.5*sh*factor+ch*s)
            uy_theta = scale*(.5*ch*factor+sh*s)
            ux_x = c*ux/(2*radius)-s*ux_theta/radius+uniform_ux_x
            uy_x = c*uy/(2*radius)-s*uy_theta/radius
            tx_ds, ty_ds = xx*dy-xy*dx, xy*dy-yy*dx
            return (energy*dy-tx_ds*ux_x-ty_ds*uy_x, tx_ds, ty_ds)[component]

        # Split at crack-face crossing; two distinct quadrature budgets test
        # integration resolution without changing any physical tolerance.
        values = []
        for tolerance in [1e-7, 1e-10]:
            values.append(sum(quad(lambda p: contour(p, 0), lo, hi,
                                   epsabs=tolerance, epsrel=tolerance)[0]
                              for lo, hi in [(-math.pi, 0), (0, math.pi)]))
        assert values[1] == pytest.approx(values[0], rel=2e-8)
        assert values[1] == pytest.approx(predicted, rel=2e-9)
        references.append(values[1])
        traction_scale = k*math.sqrt(a)+abs(tstress)*a
        for component in [1, 2]:
            force = quad(lambda p: contour(p, component), -math.pi, math.pi,
                         epsabs=1e-6, epsrel=1e-11)[0]
            assert abs(force) <= 2e-10*traction_scale
    assert references[0] == pytest.approx(references[1], rel=2e-10)
