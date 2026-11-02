"""Independent pressure-area and circumferential-strip load integration."""
import math

import pytest
from scipy.integrate import quad

from pipeline_integrity.gasket_bolt_demand import GasketBoltDemandRequest as Request
from pipeline_integrity.gasket_bolt_demand import evaluate_gasket_bolt_demand as evaluate

BASE=dict(pressure_difference_pa=1.2e6,reaction_diameter_m=.14,effective_width_m=.007,
    operating_gasket_factor=2.3,seating_stress_pa=9e6,effective_geometry_and_gasket_data_reviewed=True)


def calc(**changes):return evaluate(Request(**(BASE|changes)))


@pytest.mark.parametrize('updates',[{},dict(pressure_difference_pa=3e6,reaction_diameter_m=.3),
    dict(pressure_difference_pa=.3e6,effective_width_m=.003,operating_gasket_factor=.8,seating_stress_pa=12e6)])
def test_three_load_components_match_separate_surface_and_strip_integrals(updates):
    q=BASE|updates;diameter=q['reaction_diameter_m'];width=q['effective_width_m'];p=q['pressure_difference_pa']
    end=quad(lambda r:2*math.pi*r*p,0,diameter/2,epsabs=1e-8,epsrel=1e-12)[0]
    seal=quad(lambda theta:2*width*(diameter/2)*q['operating_gasket_factor']*p,
              0,2*math.pi,epsabs=1e-8,epsrel=1e-12)[0]
    seat=quad(lambda theta:width*(diameter/2)*q['seating_stress_pa'],
              0,2*math.pi,epsabs=1e-8,epsrel=1e-12)[0]
    result=calc(**updates)
    assert result.pressure_end_force_n==pytest.approx(end,rel=3e-14)
    assert result.operating_seal_force_n==pytest.approx(seal,rel=3e-14)
    assert result.seating_demand_n==pytest.approx(seat,rel=3e-14)
    assert result.operating_demand_n==pytest.approx(end+seal,rel=3e-14)
    assert result.governing_demand_n==pytest.approx(max(end+seal,seat),rel=3e-14)


@pytest.mark.parametrize('updates,case',[(dict(seating_stress_pa=0.),'operating'),
    (dict(pressure_difference_pa=0.),'seating'),
    (dict(pressure_difference_pa=0.,seating_stress_pa=0.),'equal')])
def test_governing_case_is_explicit_including_zero_and_equal_limits(updates,case):
    result=calc(**updates)
    assert result.governing_case==case


@pytest.mark.parametrize('scale',[.5,2.])
def test_pressure_scaling_does_not_change_assembly_seating_demand(scale):
    base=calc();out=calc(pressure_difference_pa=BASE['pressure_difference_pa']*scale)
    assert out.operating_demand_n==pytest.approx(base.operating_demand_n*scale)
    assert out.seating_demand_n==base.seating_demand_n


@pytest.mark.parametrize('scale',[.5,3.])
def test_common_geometry_scale_changes_all_loads_with_area(scale):
    base=calc();out=calc(reaction_diameter_m=BASE['reaction_diameter_m']*scale,
                         effective_width_m=BASE['effective_width_m']*scale)
    for field in ('pressure_end_force_n','operating_seal_force_n','operating_demand_n','seating_demand_n','governing_demand_n'):
        assert getattr(out,field)==pytest.approx(getattr(base,field)*scale**2,rel=3e-15)


def test_zero_operating_factor_removes_seal_term_without_removing_pressure_end_force():
    out=calc(operating_gasket_factor=0.)
    assert out.operating_seal_force_n==0
    assert out.operating_demand_n==out.pressure_end_force_n>0


@pytest.mark.parametrize('field',['pressure_difference_pa','effective_width_m','operating_gasket_factor','seating_stress_pa'])
def test_unknown_required_input_never_becomes_zero_demand(field):
    out=calc(**{field:None})
    assert out.status=='needs_data'
    assert out.governing_demand_n is None


@pytest.mark.parametrize('updates',[dict(pressure_difference_pa=-1.),dict(reaction_diameter_m=0.),
    dict(effective_width_m=0.),dict(seating_stress_pa=-1.)])
def test_invalid_pressure_geometry_or_material_parameter_is_rejected(updates):
    with pytest.raises(ValueError):calc(**updates)
