"""Selected constant-density hydrostatic observation, no hydrotest code default."""
import pytest
from pipeline_integrity.models.hydraulics import Hydrostatic,hydrostatic


@pytest.mark.parametrize('density,height',[(998.,-300.),(998.,0.),(998.,300.),(830.,100.)])
def test_hydrostatic_pressure_specific_energy_invariant(density,height):
    p0=5e6;g=9.80665
    q=Hydrostatic(inlet_pressure_pa_abs=p0,density_kg_m3=density,outlet_minus_inlet_elevation_m=height)
    r=hydrostatic(q);assert r.status=='computed'
    pressure=r.values['outlet_pressure_pa_abs']
    assert pressure/density+g*height==pytest.approx(p0/density,abs=2e-12)
    reverse=hydrostatic(Hydrostatic(inlet_pressure_pa_abs=pressure,density_kg_m3=density,outlet_minus_inlet_elevation_m=-height))
    assert reverse.values['outlet_pressure_pa_abs']==pytest.approx(p0,abs=1e-9)


def test_piecewise_elevation_profile_has_same_pressure_at_same_height():
    heights=(0.,100.,-50.,300.,0.);p0=5e6;rho=998.
    pressures=[p0]
    for previous,current in zip(heights,heights[1:]):
        pressures.append(hydrostatic(Hydrostatic(inlet_pressure_pa_abs=pressures[-1],density_kg_m3=rho,
            outlet_minus_inlet_elevation_m=current-previous)).values['outlet_pressure_pa_abs'])
    assert pressures[-1]==pytest.approx(p0,abs=1e-9)
    assert [p/rho+9.80665*h for p,h in zip(pressures,heights)]==pytest.approx([p0/rho]*len(heights),abs=2e-12)


def test_nonpositive_absolute_pressure_is_not_clipped():
    q=Hydrostatic(inlet_pressure_pa_abs=1e5,density_kg_m3=1000.,outlet_minus_inlet_elevation_m=100.)
    r=hydrostatic(q);assert r.status=='out_of_scope' and r.values=={}
