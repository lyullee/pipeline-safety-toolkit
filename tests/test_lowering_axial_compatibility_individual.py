import math
import pytest
from scipy.integrate import quad,solve_ivp
from scipy.optimize import brentq
from pipeline_integrity.lowering_axial_compatibility import CircularProfileArc,PrescribedMaterialLine,LoweringAxialCompatibilityRequest,evaluate_lowering_axial_compatibility


def line(length):return PrescribedMaterialLine(straight_lengths_m=(length,))
def request(**changes):
    return LoweringAxialCompatibilityRequest(**dict(dict(id='authored-material-line',initial_stress_free_material_line=line(100.),
        final_prescribed_material_line=line(100.02),young_modulus_pa=204e9,maximum_reviewed_absolute_engineering_strain=.002,
        uniform_linear_elastic_same_material_no_feed_or_slip_reviewed=True,thermal_and_pressure_state_unchanged_reviewed=True),**changes))


@pytest.mark.parametrize('radius,sweep',[(800.,.04),(900.,-.03),(1700.,.02)])
def test_circular_profile_length_uses_independent_parametric_curve_integral(radius,sweep):
    arc=CircularProfileArc(radius_m=radius,signed_sweep_rad=sweep)
    profile=PrescribedMaterialLine(straight_lengths_m=(12.,8.),circular_arcs=(arc,arc))
    actual=quad(lambda t:math.hypot(-radius*sweep*math.sin(sweep*t),radius*sweep*math.cos(sweep*t)),0,1)[0]
    assert profile.length_m()==pytest.approx(20+2*actual,rel=2e-14)


@pytest.mark.parametrize('strain',[-.001,-.0002,0.,.0002,.001])
def test_independent_axial_bar_shooting_equilibrium_work_and_compatibility(strain):
    q=request(final_prescribed_material_line=line(100*(1+strain)))
    r=evaluate_lowering_axial_compatibility(q);v=r['values'];area=.005;length=100.;target=q.final_prescribed_material_line.length_m()-length
    def solve(force):
        return solve_ivp(lambda x,y:[force/(q.young_modulus_pa*area)],(0,length),[0.],rtol=1e-12,atol=1e-14).y[0,-1]
    force=brentq(lambda f:solve(f)-target,-2e6,2e6,xtol=1e-8)
    assert v['signed_additional_axial_stress_pa']==pytest.approx(force/area,rel=2e-12,abs=2e-6)
    energy=quad(lambda x:force**2/(2*q.young_modulus_pa*area),0,length)[0]
    assert v['additional_elastic_energy_per_reference_volume_j_m3']*area*length==pytest.approx(energy,rel=3e-12,abs=1e-10)
    assert force*target==pytest.approx(2*energy,rel=3e-12,abs=1e-10)


def test_authored_smooth_four_arc_profile_preserves_initial_horizontal_length():
    radius=1100.;sweep=.035;straight=16.
    initial=line(4*radius*math.sin(sweep)+straight)
    final=PrescribedMaterialLine(straight_lengths_m=(straight,),circular_arcs=tuple(CircularProfileArc(radius_m=radius,signed_sweep_rad=s*sweep) for s in [1,-1,-1,1]))
    q=request(initial_stress_free_material_line=initial,final_prescribed_material_line=final)
    v=evaluate_lowering_axial_compatibility(q)['values']
    quadrature=4*quad(lambda t:math.hypot(radius*sweep*math.cos(t*sweep),radius*sweep*math.sin(t*sweep)),0,1)[0]+straight
    assert v['signed_length_change_m']==pytest.approx(quadrature-initial.length_m(),abs=1e-12)


def test_initial_convex_material_length_is_not_replaced_with_chord():
    initial=PrescribedMaterialLine(circular_arcs=(CircularProfileArc(radius_m=1000.,signed_sweep_rad=.1),))
    q=request(initial_stress_free_material_line=initial,final_prescribed_material_line=line(99.95))
    assert evaluate_lowering_axial_compatibility(q)['values']['signed_additional_axial_stress_pa']<0


def test_existing_stress_is_separate_from_additional_elastic_work():
    plain=evaluate_lowering_axial_compatibility(request())['values']
    supplied=evaluate_lowering_axial_compatibility(request(signed_initial_uniform_axial_stress_pa=-20e6))['values']
    assert supplied['signed_superposed_uniform_axial_stress_pa']==plain['signed_additional_axial_stress_pa']-20e6
    assert supplied['additional_elastic_energy_per_reference_volume_j_m3']==plain['additional_elastic_energy_per_reference_volume_j_m3']


@pytest.mark.parametrize('field',['uniform_linear_elastic_same_material_no_feed_or_slip_reviewed','thermal_and_pressure_state_unchanged_reviewed'])
def test_unknown_slip_feed_or_thermal_pressure_change_does_not_fake_pure_compatibility(field):
    assert evaluate_lowering_axial_compatibility(request(**{field:False}))['status']=='needs_data'


def test_large_extension_outside_supplied_linear_support_is_withheld():
    r=evaluate_lowering_axial_compatibility(request(final_prescribed_material_line=line(101.)))
    assert r['status']=='out_of_scope' and 'values' not in r


@pytest.mark.parametrize('changes',[dict(young_modulus_pa=0),dict(maximum_reviewed_absolute_engineering_strain=0),dict(final_prescribed_material_line=PrescribedMaterialLine()),dict(signed_initial_uniform_axial_stress_pa=float('inf'))])
def test_invalid_or_missing_reference_geometry_is_not_defaulted(changes):
    with pytest.raises(ValueError):evaluate_lowering_axial_compatibility(request(**changes))
