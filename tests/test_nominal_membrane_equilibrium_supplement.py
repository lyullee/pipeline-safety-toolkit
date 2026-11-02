"""Nominal half-cylinder traction equilibrium with explicitly selected diameter."""
import math

import pytest
from scipy.integrate import quad

from pipeline_integrity.nominal_pipe_pressure import (
    NominalHoopStressInput, NominalThicknessInput,
    nominal_hoop_stress_Pa, nominal_wall_thickness_m,
)


@pytest.mark.parametrize("basis",['outside','mean_from_outside'])
@pytest.mark.parametrize("pressure,diameter,wall",[(3e6,.5,.007),(0.,.3,.003),(7e5,.8,.02)])
def test_nominal_half_cylinder_pressure_traction_and_wall_strip_resultant(basis,pressure,diameter,wall):
    reference=diameter if basis=='outside' else diameter-wall
    radius=reference/2
    pressure_resultant=quad(lambda angle:pressure*math.cos(angle)*radius,-math.pi/2,math.pi/2)[0]
    stress=nominal_hoop_stress_Pa(NominalHoopStressInput(
        differential_pressure_Pa=pressure,outside_diameter_m=diameter,
        wall_thickness_m=wall,diameter_basis=basis))
    wall_resultant=quad(lambda y:stress,0,wall)[0]*2
    assert wall_resultant==pytest.approx(pressure_resultant,rel=2e-14,abs=1e-10)
    if pressure:
        inverse=nominal_wall_thickness_m(NominalThicknessInput(
            differential_pressure_Pa=pressure,outside_diameter_m=diameter,
            supplied_nominal_stress_Pa=stress,diameter_basis=basis))
        assert inverse==pytest.approx(wall,rel=2e-14)
    # These are explicitly nominal wall strips at the declared reference
    # diameter, not a finite-wall radial-stress solution or rated allowable.
