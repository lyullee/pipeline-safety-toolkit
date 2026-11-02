"""Free thermal section kinematics close exact affine eigenstrain profiles."""
import pytest
from matintegrity.models.thermal_transient import ThroughWallThermalStress,through_wall_thermal_stress

E=200e9;ALPHA=12e-6

def solve(positions,temperatures,reference=310.0):
    n=len(positions)
    request=ThroughWallThermalStress(
      profile_id='synthetic-affine-wall',position_m=positions,temperature_K=temperatures,
      reference_temperature_K=reference,secant_thermal_expansion_1_K=[ALPHA]*n,
      elastic_modulus_Pa=[E]*n,poisson_ratio=[0.3]*n,elastic_limit_Pa=[300e6]*n,
      plane_condition='uniaxial_plane_stress',control='resultant_controlled',
      membrane_force_N_m=0.0,bending_moment_N=0.0,plane_sections_reviewed=True,
      linear_elastic_response_reviewed=True,property_profiles_reviewed=True,
      applied_resultants_or_kinematics_reviewed=True,
    )
    return through_wall_thermal_stress(request).values

def test_free_affine_temperature_profile_is_stress_free_with_exact_thermal_curvature():
    values=solve([0.,0.01,0.02],[300.,310.,320.])
    assert values['curvature_1_m']==pytest.approx(ALPHA*1000.,rel=2e-12)
    assert values['midplane_total_strain']==pytest.approx(0.,abs=1e-14)
    assert max(abs(s) for s in values['stress_Pa'])<1e-3
    assert abs(values['membrane_force_residual_N_m'])<1e-5
    assert abs(values['bending_moment_residual_N'])<1e-7

def test_uniform_temperature_change_has_free_expansion_and_no_thermal_stress():
    values=solve([0.,0.005,0.02],[350.,350.,350.],reference=300.)
    assert values['midplane_total_strain']==pytest.approx(ALPHA*50.,rel=2e-12)
    assert values['curvature_1_m']==pytest.approx(0.,abs=1e-14)
    assert max(abs(s) for s in values['stress_Pa'])<1e-3

def test_reversing_linear_gradient_reverses_curvature_without_stress():
    values=solve([0.,0.01,0.02],[320.,310.,300.])
    assert values['curvature_1_m']==pytest.approx(-ALPHA*1000.,rel=2e-12)
    assert max(abs(s) for s in values['stress_Pa'])<1e-3
