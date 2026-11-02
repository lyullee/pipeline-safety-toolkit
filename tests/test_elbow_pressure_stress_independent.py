"""Membrane-equilibrium ODE, weighted force budgets and geometric limits."""
import math
import numpy as np
import pytest
from scipy.integrate import solve_ivp,simpson
from pipeline_integrity.elbow_pressure_stress import ElbowPressureStressRequest,evaluate_elbow_pressure_stress

def calc(**kw):
    return evaluate_elbow_pressure_stress(ElbowPressureStressRequest(**(dict(pressure_difference_pa=2e6,outside_diameter_m=.2,
        wall_m=.002,centreline_bend_radius_m=.3,circumferential_angles_rad=tuple(float(x) for x in np.linspace(-math.pi/2,3*math.pi/2,81)),
        maximum_reviewed_wall_inside_radius_ratio=.1,uniform_wall_thin_curved_membrane_basis_reviewed=True)|kw)))

@pytest.mark.parametrize('bend_radius',[.15,.3,1.])
def test_field_solves_independent_meridional_equilibrium_ode(bend_radius):
    result=calc(centreline_bend_radius_m=bend_radius)
    angles=np.linspace(-math.pi/2,3*math.pi/2,81);r=.098;s0=100e6
    # Curved-strip equilibrium: derivative of (R+r*sin(theta))*S
    # equals the constant-longitudinal membrane contribution S0*r*cos/2.
    def rhs(theta,y):return [r*math.cos(theta)/(bend_radius+r*math.sin(theta))*(s0/2-y[0])]
    initial=s0*(bend_radius-r/2)/(bend_radius-r)
    ode=solve_ivp(rhs,(float(angles[0]),float(angles[-1])),[initial],t_eval=angles,rtol=2e-11,atol=1e-4)
    assert ode.success
    assert np.allclose(result.nominal_hoop_stress_pa,ode.y[0],rtol=2e-9,atol=.05)

def test_weighted_circumferential_force_budget_and_extreme_locations():
    angles=np.linspace(-math.pi/2,3*math.pi/2,1001)
    result=calc(circumferential_angles_rad=tuple(float(x) for x in angles))
    weighted=(.3+.098*np.sin(angles))*np.asarray(result.nominal_hoop_stress_pa)
    assert simpson(weighted,x=angles)==pytest.approx(2*math.pi*.3*100e6,rel=1e-12)
    assert max(result.nominal_hoop_stress_pa)==pytest.approx(result.intrados_nominal_hoop_pa)
    assert min(result.nominal_hoop_stress_pa)==pytest.approx(result.extrados_nominal_hoop_pa)

def test_side_angles_match_existing_straight_od_nominal_coordinate():
    result=calc(circumferential_angles_rad=(0.,math.pi))
    assert result.nominal_hoop_stress_pa==pytest.approx((100e6,100e6))

def test_curvature_correction_converges_to_straight_pipe():
    errors=[max(abs(f-1) for f in calc(centreline_bend_radius_m=r).curvature_correction_factors) for r in (1.,10.,100.,1000.)]
    assert errors[0]>errors[1]>errors[2]>errors[3]
    assert errors[-1]<5e-5

def test_hybrid_nominal_coordinate_converges_to_consistent_thin_torus_reference():
    errors=[]
    for t in (.004,.002,.001,.0005):
        result=calc(wall_m=t,circumferential_angles_rad=(-math.pi/2,))
        a=(.2-t)/2
        physical_torus=2e6*a/(2*t)*(2*.3-a)/(.3-a)
        errors.append(abs(result.nominal_hoop_stress_pa[0]/physical_torus-1))
    assert errors[0]>errors[1]>errors[2]>errors[3]
    assert errors[-1]<.003

@pytest.mark.parametrize('factor',[.25,3.])
def test_pressure_and_geometric_similarity(factor):
    one=calc();two=calc(pressure_difference_pa=2e6*factor)
    assert two.nominal_hoop_stress_pa==pytest.approx(tuple(s*factor for s in one.nominal_hoop_stress_pa))
    scaled=calc(outside_diameter_m=.2*factor,wall_m=.002*factor,centreline_bend_radius_m=.3*factor)
    assert scaled.nominal_hoop_stress_pa==pytest.approx(one.nominal_hoop_stress_pa)

def test_zero_pressure_produces_zero_nominal_field():
    assert set(calc(pressure_difference_pa=0.).nominal_hoop_stress_pa)=={0.}

def test_unreviewed_geometry_does_not_return_a_stress():
    assert calc(uniform_wall_thin_curved_membrane_basis_reviewed=False).status=='needs_data'

def test_thick_geometry_is_withheld_by_declared_support():
    assert calc(wall_m=.02).status=='out_of_scope'

def test_nonphysical_self_intersecting_bend_is_rejected():
    with pytest.raises(ValueError):calc(centreline_bend_radius_m=.1)
