"""Pressure-dependent line mass and independently solved wall Hooke constraints."""
import math
import numpy as np
import pytest
from pipeline_integrity.models.hydraulics import WaveSpeed, wave_speed

@pytest.mark.parametrize('nu',[0.,.3,.45])
@pytest.mark.parametrize('modulus,wall',[(70e9,.003),(200e9,.01)])
def test_celerity_matches_independent_pressure_derivative_of_stored_line_mass(nu,modulus,wall):
    rho=997.;bulk=2.2e9;diameter=.3;area=math.pi*diameter**2/4
    def line_mass(dp):
        hoop_stress=dp*diameter/(2*wall)
        # Solve circumferential strain and axial stress from plane-strain
        # compatibility and the independently balanced hoop traction.
        strain,axial=np.linalg.solve([[modulus,nu],[0.,1.]],[hoop_stress,nu*hoop_stress])
        assert abs((axial-nu*hoop_stress)/modulus)<1e-20
        return rho*(1+dp/bulk)*area*(1+strain)**2
    h=100.
    derivative=(line_mass(-2*h)-8*line_mass(-h)+8*line_mass(h)-line_mass(2*h))/(12*h)
    independent=math.sqrt(area/derivative)
    q=WaveSpeed(density_kg_m3=rho,fluid_bulk_modulus_pa=bulk,pipe_elastic_modulus_pa=modulus,
        inner_diameter_m=diameter,wall_thickness_m=wall,restraint_coefficient=1-nu**2)
    result=wave_speed(q).values
    assert result['wave_speed_m_s']==pytest.approx(independent,rel=2e-8)
    assert result['wave_speed_m_s']<result['rigid_wall_sound_speed_m_s']
