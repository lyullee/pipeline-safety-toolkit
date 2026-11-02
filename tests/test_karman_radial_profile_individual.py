"""Independent inverse-gradient ODE, area moments and wall-law limits."""
from decimal import Decimal,localcontext
import math

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from pipeline_integrity.karman_radial_profile import KarmanRadialProfileRequest,evaluate_karman_radial_profile
from pipeline_integrity.turbulent_constitutive_friction import TurbulentResistanceRequest,evaluate_turbulent_resistance


def case(**updates):
    radius=0.1
    return KarmanRadialProfileRequest(**{**dict(common_state_basis_id="authored_constant_liquid",radius_m=radius,
        density_kg_m3=900.0,kinematic_viscosity_m2_s=1.5e-6,pressure_drop_per_length_Pa_m=5.0,
        mixing_constant=0.42,wall_interaction_parameter=25.0,roughness_wall_constant=0.35,roughness_m=0.0,
        radial_positions_m=tuple(radius*f for f in (0,0.001,0.1,0.5,0.9,0.999,1)),
        empirical_profile_and_wall_parameter_domain_reviewed=True),**updates})


def inverse_gradient_reference(q):
    # Directly integrate the stress-closure ODE for w=1/(du/dr) and accumulate
    # moments in physical radius. Neither the closed-form logarithmic profile
    # nor its s-coordinate quadrature is used by this reference.
    star=math.sqrt(q.pressure_drop_per_length_Pa_m*q.radius_m/(2*q.density_kg_m3))
    wall_w=-(q.kinematic_viscosity_m2_s+q.roughness_wall_constant*star*q.roughness_m)/(
        q.wall_interaction_parameter*star**2)
    def rhs(r,y):
        u,w=y[:2]
        weight=-2*r/q.radius_m**2
        return (1/w,q.mixing_constant/star*math.sqrt(q.radius_m/r),
                weight*u,weight*u*u,weight*u*u*u)
    solution=solve_ivp(rhs,(q.radius_m,q.radius_m*1e-6),(0,wall_w,0,0,0),
        method="DOP853",rtol=2e-11,atol=1e-12,max_step=q.radius_m/30,dense_output=True)
    assert solution.success
    return solution


@pytest.mark.parametrize("gradient",[0.5,5.0,50.0])
@pytest.mark.parametrize("roughness",[0.0,2e-5])
def test_profile_and_three_area_moments_match_independent_inverse_gradient_ode(gradient,roughness):
    q=case(pressure_drop_per_length_Pa_m=gradient,roughness_m=roughness)
    result=evaluate_karman_radial_profile(q)
    assert result.status=="computed",result.notes
    values=result.values
    reference=inverse_gradient_reference(q)
    for row in values["radial_profile"][1:]:
        expected=reference.sol(row["radius_m"])
        assert row["velocity_m_s"]==pytest.approx(expected[0],rel=2e-8,abs=2e-10)
        assert row["velocity_gradient_per_s"]==pytest.approx(1/expected[1],rel=2e-8)
    moments=reference.y[2:,-1]
    assert values["mean_velocity_m_s"]==pytest.approx(moments[0],rel=2e-8)
    assert values["mean_square_velocity_m2_s2"]==pytest.approx(moments[1],rel=2e-8)
    assert values["signed_mean_cube_velocity_m3_s3"]==pytest.approx(moments[2],rel=2e-8)
    area=math.pi*q.radius_m**2
    assert values["volume_flow_m3_s"]==pytest.approx(area*moments[0],rel=2e-8)
    assert 2*math.pi*q.radius_m*values["wall_shear_magnitude_Pa"]==pytest.approx(area*gradient)
    beta=values["mean_square_over_mean_squared"]
    alpha=values["kinetic_energy_flux_correction"]
    assert 1<beta<alpha and alpha>=beta**2


def test_exact_profile_approaches_existing_asymptotic_wall_friction_law_without_claiming_identity():
    differences=[]
    for viscosity in (1e-5,1e-6,1e-7):
        q=case(kinematic_viscosity_m2_s=viscosity)
        profile=evaluate_karman_radial_profile(q)
        assert profile.status=="computed",profile.notes
        values=profile.values
        native=evaluate_turbulent_resistance(TurbulentResistanceRequest(model="wall_interaction",
            reynolds_coordinate=values["reynolds_number"],relative_roughness=0.0,
            wall_interaction_parameter=q.wall_interaction_parameter,mixing_length_constant=q.mixing_constant,
            roughness_wall_constant=q.roughness_wall_constant,turbulent_regime_coordinate_and_calibration_reviewed=True))
        assert native.status=="computed"
        differences.append(abs(native.darcy_friction_factor/values["darcy_factor_from_exact_profile_mean"]-1))
    assert all(a>5*b>0 for a,b in zip(differences,differences[1:]))
    assert differences[-1]<1e-4


def test_sign_and_geometric_scaling_preserve_dimensionless_shape_and_distinct_moments():
    q=case()
    baseline=evaluate_karman_radial_profile(q).values
    reverse=evaluate_karman_radial_profile(q.model_copy(update={"flow_direction":-1})).values
    assert reverse["volume_flow_m3_s"]==pytest.approx(-baseline["volume_flow_m3_s"])
    assert reverse["signed_mean_cube_velocity_m3_s3"]==pytest.approx(-baseline["signed_mean_cube_velocity_m3_s3"])
    assert reverse["mean_square_over_mean_squared"]==pytest.approx(baseline["mean_square_over_mean_squared"])
    factor=4.0
    scaled=q.model_copy(update={"radius_m":q.radius_m*factor,"roughness_m":q.roughness_m*factor,
        "kinematic_viscosity_m2_s":q.kinematic_viscosity_m2_s*factor,
        "pressure_drop_per_length_Pa_m":q.pressure_drop_per_length_Pa_m/factor,
        "radial_positions_m":tuple(r*factor for r in q.radial_positions_m)})
    values=evaluate_karman_radial_profile(scaled).values
    assert values["mean_velocity_m_s"]==pytest.approx(baseline["mean_velocity_m_s"])
    assert values["reynolds_number"]==pytest.approx(baseline["reynolds_number"])
    assert values["volume_flow_m3_s"]==pytest.approx(baseline["volume_flow_m3_s"]*factor**2)


def test_large_offset_arithmetic_has_the_triangular_profile_limit_not_a_fake_parabola():
    q=case(wall_interaction_parameter=1e-10)
    result=evaluate_karman_radial_profile(q)
    assert result.status=="computed",result.notes
    values=result.values
    assert values["wall_coordinate_offset"]>1e5
    assert values["mean_velocity_m_s"]/values["centerline_velocity_m_s"]==pytest.approx(1/3,rel=1e-5)
    assert values["mean_square_over_mean_squared"]==pytest.approx(1.5,rel=1e-5)
    assert values["kinetic_energy_flux_correction"]==pytest.approx(2.7,rel=1e-5)
    assert values["regular_axis_boundary_satisfied"] is False


def test_next_float_inside_wall_retains_small_positive_velocity_and_axis_limit_is_explicit():
    q=case()
    r=math.nextafter(q.radius_m,0.0)
    q=q.model_copy(update={"radial_positions_m":(0.0,r,q.radius_m)})
    result=evaluate_karman_radial_profile(q)
    assert result.status=="computed",result.notes
    values=result.values
    with localcontext() as context:
        context.prec=80
        radius=Decimal.from_float(q.radius_m)
        s=(Decimal.from_float(r)/radius).sqrt()
        delta=Decimal.from_float(values["wall_coordinate_offset"])
        shape=s-1+(1+delta)*((1+delta-s)/delta).ln()
        expected=float(Decimal.from_float(values["friction_velocity_m_s"])/Decimal.from_float(q.mixing_constant)*shape)
    assert values["radial_profile"][1]["velocity_m_s"]>0
    assert values["radial_profile"][1]["velocity_m_s"]==pytest.approx(expected,rel=2e-12,abs=0)
    assert values["radial_profile"][-1]["velocity_m_s"]==0
    assert values["radial_profile"][0]["velocity_gradient_per_s"]!=0
    assert values["radial_profile"][0]["velocity_curvature_per_m_s"] is None
    assert values["molecular_viscous_wall_resolved"] is False


def test_profile_finite_differences_and_stress_constitutive_balance_agree_away_from_axis():
    q=case()
    h=q.radius_m*1e-5
    for radius in (q.radius_m*0.2,q.radius_m*0.5,q.radius_m*0.8):
        result=evaluate_karman_radial_profile(q.model_copy(update={"radial_positions_m":(radius-h,radius,radius+h)}))
        assert result.status=="computed",result.notes
        left,center,right=result.values["radial_profile"]
        first=(right["velocity_m_s"]-left["velocity_m_s"])/(2*h)
        second=(right["velocity_m_s"]-2*center["velocity_m_s"]+left["velocity_m_s"])/h**2
        assert center["velocity_gradient_per_s"]==pytest.approx(first,rel=1e-7)
        assert center["velocity_curvature_per_m_s"]==pytest.approx(second,rel=2e-4)
        effective=center["modeled_effective_eddy_viscosity_m2_s"]
        constitutive=q.mixing_constant**2*abs(first)**3/second**2
        assert effective==pytest.approx(constitutive,rel=5e-4)
        assert q.density_kg_m3*effective*first==pytest.approx(center["shear_stress_Pa"],rel=1e-7)


def test_supplied_wall_gradient_condition_and_no_slip_hold():
    q=case(roughness_m=2e-5)
    result=evaluate_karman_radial_profile(q)
    assert result.status=="computed",result.notes
    star=result.values["friction_velocity_m_s"]
    wall=result.values["radial_profile"][-1]
    expected=-q.wall_interaction_parameter*star**2/(
        q.kinematic_viscosity_m2_s+q.roughness_wall_constant*star*q.roughness_m)
    assert wall["velocity_m_s"]==0
    assert wall["velocity_gradient_per_s"]==pytest.approx(expected)


def test_radius_and_review_contracts_do_not_silently_select_a_different_profile():
    q=case()
    for update in ({"radial_positions_m":(0.0,2*q.radius_m)},
                   {"radial_positions_m":(0.1,0.0)},{"flow_direction":0},
                   {"mixing_constant":0.0}):
        with pytest.raises(ValueError):KarmanRadialProfileRequest.model_validate({**q.model_dump(),**update})
    with pytest.raises(ValueError,match="require review"):
        evaluate_karman_radial_profile(q.model_copy(update={"empirical_profile_and_wall_parameter_domain_reviewed":False}))
