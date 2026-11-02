"""Radial force balance, J2 admissibility, closed-cap thrust and collapse work."""
import math
import numpy as np
import pytest
from scipy.integrate import solve_ivp,simpson,quad
from pipeline_integrity.perfect_plastic_cylinder import PerfectPlasticCylinderRequest,evaluate_perfect_plastic_cylinder

def calc(ri=.1,ro=.2,sy=300e6,**kw):
    data=dict(axial_condition='closed_ends_zero_axial_plastic_strain',inside_radius_m=ri,outside_radius_m=ro,yield_stress_pa=sy,
        sample_radii_m=tuple(float(x) for x in np.linspace(ri,ro,1001)),homogeneous_isotropic_associated_j2_fixed_geometry_reviewed=True)
    return evaluate_perfect_plastic_cylinder(PerfectPlasticCylinderRequest(**(data|kw)))

@pytest.mark.parametrize('ratio',[1.01,1.2,2.,4.])
def test_independent_radial_equilibrium_initial_value_solution_and_boundaries(ratio):
    ri=.1;ro=ri*ratio;sy=300e6
    result=calc(ri,ro,sy);radius=np.array([p.radius_m for p in result.stress_points])
    # J2 on the zero-axial-plastic-strain branch fixes the principal difference.
    difference=math.sqrt(4/3)*sy
    ode=solve_ivp(lambda r,y:[difference/r],(ro,ri),[0.],t_eval=radius[::-1],rtol=1e-11,atol=1e-3)
    assert ode.success
    assert np.allclose([p.radial_stress_pa for p in result.stress_points],ode.y[0][::-1],rtol=2e-9,atol=.05)
    assert result.stress_points[0].radial_stress_pa==pytest.approx(-result.full_wall_plastic_pressure_difference_pa,rel=1e-12)
    assert result.stress_points[-1].radial_stress_pa==0

@pytest.mark.parametrize('ratio',[1.01,1.5,3.])
def test_every_point_satisfies_von_mises_yield_and_closed_cap_axial_force(ratio):
    ri=.1;result=calc(ri,ri*ratio);radius=np.array([p.radius_m for p in result.stress_points])
    for p in result.stress_points:
        vm=math.hypot(p.radial_stress_pa-p.hoop_stress_pa,p.hoop_stress_pa-p.axial_stress_pa,p.axial_stress_pa-p.radial_stress_pa)/math.sqrt(2)
        assert vm==pytest.approx(300e6,rel=2e-15)
        assert p.axial_stress_pa==pytest.approx((p.radial_stress_pa+p.hoop_stress_pa)/2,rel=2e-15,abs=1e-7)
    cap_force=math.pi*ri**2*result.full_wall_plastic_pressure_difference_pa
    wall_force=simpson([2*math.pi*p.radius_m*p.axial_stress_pa for p in result.stress_points],x=radius)
    assert wall_force==pytest.approx(cap_force,rel=3e-12)

@pytest.mark.parametrize('ratio',[1.05,1.5,3.])
def test_admissible_incompressible_collapse_velocity_dissipation_matches_pressure_power(ratio):
    ri=.1;ro=ri*ratio;sy=300e6;result=calc(ri,ro,sy)
    velocity_constant=2e-8
    # v(r)=A/r gives eps_r=-A/r^2, eps_theta=A/r^2, eps_z=0.
    def dissipation(r):
        er=-velocity_constant/r**2;et=-er
        equivalent_rate=math.sqrt(2/3*(er*er+et*et))
        return 2*math.pi*r*sy*equivalent_rate
    internal=quad(dissipation,ri,ro,epsabs=1e-12,epsrel=1e-12)[0]
    external=2*math.pi*ri*(velocity_constant/ri)*result.full_wall_plastic_pressure_difference_pa
    assert internal==pytest.approx(external,rel=2e-13)

def test_decreasing_wall_ratios_converge_to_closed_end_thin_j2_limit():
    errors=[];ri=.1;sy=300e6
    for wall_ratio in (.1,.03,.01,.003):
        ro=ri/(1-wall_ratio);wall=ro-ri
        prediction=calc(ri,ro,sy).full_wall_plastic_pressure_difference_pa
        thin=2/math.sqrt(3)*sy*wall/ro
        errors.append(abs(prediction/thin-1))
    assert errors[0]>errors[1]>errors[2]>errors[3]
    assert errors[-1]<.002

@pytest.mark.parametrize('ratio',[1.001,1.1,2.,4.])
def test_full_wall_limit_is_at_least_elastic_lame_first_yield(ratio):
    ri=.1;ro=ri*ratio;sy=300e6
    # Closed-cap Lamé principal differences first reach J2 yield at the bore.
    elastic_first=sy/math.sqrt(3)*(1-(ri/ro)**2)
    assert calc(ri,ro,sy).full_wall_plastic_pressure_difference_pa>=elastic_first

def test_pressure_and_geometry_similarity():
    one=calc();material=calc(sy=600e6);scaled=calc(.3,.6)
    assert material.full_wall_plastic_pressure_difference_pa==pytest.approx(2*one.full_wall_plastic_pressure_difference_pa)
    assert scaled.full_wall_plastic_pressure_difference_pa==pytest.approx(one.full_wall_plastic_pressure_difference_pa)
    assert scaled.stress_points[500].hoop_stress_pa==pytest.approx(one.stress_points[500].hoop_stress_pa)

def test_missing_strength_and_unreviewed_assumptions_remain_unknown():
    assert calc(yield_stress_pa=None).status=='needs_data'
    assert calc(homogeneous_isotropic_associated_j2_fixed_geometry_reviewed=False).status=='needs_data'

@pytest.mark.parametrize('condition',['open_ends','imposed_axial_load'])
def test_other_axial_constraints_are_not_assigned_the_closed_cap_solution(condition):
    assert calc(axial_condition=condition).status=='out_of_scope'

def test_nonannular_or_outside_sample_geometry_is_rejected():
    with pytest.raises(ValueError):calc(.2,.1)
    with pytest.raises(ValueError):calc(sample_radii_m=(.05,))

def test_zero_exterior_reference_and_limitations_are_explicit():
    result=calc(sample_radii_m=())
    assert len(result.stress_points)==2
    assert result.stress_reference=='relative_to_uniform_exterior_hydrostatic_stress'
    assert result.elastic_first_yield_hardening_or_finite_deformation_burst_qualified is False
    assert result.external_pressure_buckling_or_fracture_qualified is False
