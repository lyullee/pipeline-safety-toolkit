"""Independent phase-volume compression and acoustic similarity references."""
import math

import pytest

from pipeline_integrity.mixture_wave_compliance import MixtureWaveRequest,evaluate_mixture_wave_speed
from pipeline_integrity.models.hydraulics import WaveSpeed,wave_speed


def case(**updates):
    return MixtureWaveRequest(**{**dict(common_state_basis_id="authored_common_state",
        gas_volume_fraction=0.01,liquid_density_kg_m3=1000.0,liquid_bulk_modulus_Pa=2e9,
        gas_pressure_Pa_abs=300000.0,gas_density_kg_m3=3.0,gas_polytropic_exponent=1.4,
        pipe_elastic_modulus_Pa=2e11,inner_diameter_m=0.2,wall_thickness_m=0.005,
        wall_restraint_coefficient=1.0,homogeneous_frozen_phase_and_common_state_reviewed=True),**updates})


@pytest.mark.parametrize("alpha",[1e-4,0.01,0.25])
@pytest.mark.parametrize("exponent",[1.0,1.4])
def test_phase_volume_work_tangent_matches_combined_wave_compliance(alpha,exponent):
    q=case(gas_volume_fraction=alpha,gas_polytropic_exponent=exponent)
    result=evaluate_mixture_wave_speed(q)
    assert result.status=="ok",result.reason
    # Compress fixed phase masses, starting from unit total volume. Liquid's
    # tangent and the gas's finite reversible polytropic law are independent
    # of the evaluator's weighted-compliance calculation.
    def material_volume(dp):
        return ((1-alpha)*(1-dp/q.liquid_bulk_modulus_Pa)
                +alpha*(q.gas_pressure_Pa_abs/(q.gas_pressure_Pa_abs+dp))**(1/exponent))
    step=q.gas_pressure_Pa_abs*1e-4
    fluid_tangent=-(material_volume(step)-material_volume(-step))/(2*step)
    assert result.fluid_compliance_per_Pa==pytest.approx(fluid_tangent,rel=2e-7)
    # Prescribed thin-wall circumferential compliance gives extra stored volume.
    wall_tangent=q.inner_diameter_m/(q.wall_thickness_m*q.pipe_elastic_modulus_Pa)
    total_tangent=fluid_tangent+wall_tangent
    mass=(1-alpha)*q.liquid_density_kg_m3+alpha*q.gas_density_kg_m3
    # One-dimensional longitudinal mass/momentum compatibility: rho*c^2*C=1.
    assert mass*result.wave_speed_m_s**2*total_tangent==pytest.approx(1,rel=2e-7)
    assert result.wave_speed_m_s<result.rigid_wall_sound_speed_m_s


@pytest.mark.parametrize("exponent",[1.0,1.4,2.0])
def test_all_gas_rigid_wall_limit_is_polytropic_acoustic_speed(exponent):
    q=case(gas_volume_fraction=1.0,gas_polytropic_exponent=exponent,wall_restraint_coefficient=0.0)
    result=evaluate_mixture_wave_speed(q)
    assert result.status=="ok",result.reason
    expected=math.sqrt(exponent*q.gas_pressure_Pa_abs/q.gas_density_kg_m3)
    assert result.wave_speed_m_s==pytest.approx(expected)
    assert result.mixture_bulk_modulus_Pa==pytest.approx(exponent*q.gas_pressure_Pa_abs)


def test_zero_gas_exactly_reuses_the_existing_single_liquid_owner_without_gas_data():
    q=case(gas_volume_fraction=0.0,gas_pressure_Pa_abs=None,gas_density_kg_m3=None,gas_polytropic_exponent=None)
    result=evaluate_mixture_wave_speed(q)
    owner=wave_speed(WaveSpeed(density_kg_m3=q.liquid_density_kg_m3,
        fluid_bulk_modulus_pa=q.liquid_bulk_modulus_Pa,pipe_elastic_modulus_pa=q.pipe_elastic_modulus_Pa,
        inner_diameter_m=q.inner_diameter_m,wall_thickness_m=q.wall_thickness_m,
        restraint_coefficient=q.wall_restraint_coefficient))
    assert result.status=="ok"
    assert result.wave_speed_m_s==owner.values["wave_speed_m_s"]


@pytest.mark.parametrize("factor",[0.01,100.0])
def test_pressure_density_and_modulus_scale_preserves_acoustic_speed(factor):
    q=case()
    baseline=evaluate_mixture_wave_speed(q)
    scaled=q.model_copy(update={name:getattr(q,name)*factor for name in
        ("liquid_density_kg_m3","liquid_bulk_modulus_Pa","gas_density_kg_m3",
         "gas_pressure_Pa_abs","pipe_elastic_modulus_Pa")})
    result=evaluate_mixture_wave_speed(scaled)
    assert result.status=="ok",result.reason
    assert result.wave_speed_m_s==pytest.approx(baseline.wave_speed_m_s)
    assert result.mixture_bulk_modulus_Pa==pytest.approx(baseline.mixture_bulk_modulus_Pa*factor)


def test_geometric_scale_and_rigid_wall_bounds():
    q=case()
    baseline=evaluate_mixture_wave_speed(q)
    scaled=evaluate_mixture_wave_speed(q.model_copy(update={"inner_diameter_m":20.0,"wall_thickness_m":0.5}))
    rigid=evaluate_mixture_wave_speed(q.model_copy(update={"wall_restraint_coefficient":0.0}))
    assert scaled.wave_speed_m_s==pytest.approx(baseline.wave_speed_m_s)
    assert rigid.wave_speed_m_s==pytest.approx(baseline.rigid_wall_sound_speed_m_s)
    assert rigid.wave_speed_m_s>baseline.wave_speed_m_s


def test_density_and_compressibility_competition_has_an_interior_speed_minimum():
    q=case()
    dr=q.gas_density_kg_m3-q.liquid_density_kg_m3
    c0=1/q.liquid_bulk_modulus_Pa+q.inner_diameter_m/(q.wall_thickness_m*q.pipe_elastic_modulus_Pa)
    dc=1/(q.gas_polytropic_exponent*q.gas_pressure_Pa_abs)-1/q.liquid_bulk_modulus_Pa
    alpha=-(q.liquid_density_kg_m3*dc+dr*c0)/(2*dr*dc)
    assert 0.1<alpha<0.9
    speeds=[evaluate_mixture_wave_speed(q.model_copy(update={"gas_volume_fraction":a})).wave_speed_m_s
            for a in (alpha-0.05,alpha,alpha+0.05)]
    assert speeds[1]<min(speeds[0],speeds[2])


def test_unknown_gas_properties_are_not_replaced_with_zero_or_atmospheric_pressure():
    q=case()
    for field in ("gas_pressure_Pa_abs","gas_density_kg_m3","gas_polytropic_exponent"):
        result=evaluate_mixture_wave_speed(q.model_copy(update={field:None}))
        assert result.status=="needs_data" and result.wave_speed_m_s is None
    assert result.phase_change_or_slip_predicted is False
    assert result.material_calibration_claim is False


def test_input_support_and_applicability_reviews_are_required():
    q=case()
    for update in ({"gas_volume_fraction":-0.1},{"gas_volume_fraction":1.1},
                   {"gas_pressure_Pa_abs":0.0},{"gas_polytropic_exponent":0.0}):
        with pytest.raises(ValueError):
            MixtureWaveRequest.model_validate({**q.model_dump(),**update})
    with pytest.raises(ValueError,match="require review"):
        evaluate_mixture_wave_speed(q.model_copy(update={"homogeneous_frozen_phase_and_common_state_reviewed":False}))
