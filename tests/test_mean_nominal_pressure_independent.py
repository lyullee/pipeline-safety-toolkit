"""Mean-diameter nominal relation in independent SDR and customary-unit forms."""
import pytest

from pipeline_integrity.nominal_pipe_pressure import (
    NominalHoopStressInput,NominalThicknessInput,nominal_hoop_stress_Pa,nominal_wall_thickness_m,
)


@pytest.mark.parametrize('outside_in,wall_in,pressure_psi',[(8.,.4,140.),(4.5,.2,190.),(20.,.6,110.)])
def test_mean_nominal_stress_matches_independent_sdr_expression_in_customary_units(outside_in,wall_in,pressure_psi):
    psi=.45359237*9.80665/.0254**2
    result=nominal_hoop_stress_Pa(NominalHoopStressInput(differential_pressure_Pa=pressure_psi*psi,
        outside_diameter_m=outside_in*.0254,wall_thickness_m=wall_in*.0254,diameter_basis='mean_from_outside'))
    expected_psi=pressure_psi*((outside_in/wall_in)-1)/2
    assert result==pytest.approx(expected_psi*psi,rel=3e-15)


@pytest.mark.parametrize('sdr',[11.,17.,26.])
def test_mean_nominal_thickness_inverse_matches_independent_sdr_pressure_rating(sdr):
    diameter,limit=.4,12e6
    pressure=2*limit/(sdr-1)
    thickness=nominal_wall_thickness_m(NominalThicknessInput(differential_pressure_Pa=pressure,
        outside_diameter_m=diameter,supplied_nominal_stress_Pa=limit,diameter_basis='mean_from_outside'))
    assert thickness==pytest.approx(diameter/sdr,rel=3e-15)
    assert nominal_hoop_stress_Pa(NominalHoopStressInput(differential_pressure_Pa=pressure,
        outside_diameter_m=diameter,wall_thickness_m=thickness,diameter_basis='mean_from_outside'))==pytest.approx(limit,rel=3e-15)


def test_outside_default_and_explicit_mean_basis_keep_their_distinct_nominal_stresses():
    fields=dict(differential_pressure_Pa=2e6,outside_diameter_m=.4,wall_thickness_m=.02)
    outside=nominal_hoop_stress_Pa(NominalHoopStressInput(**fields))
    mean=nominal_hoop_stress_Pa(NominalHoopStressInput(**fields,diameter_basis='mean_from_outside'))
    assert outside-mean==pytest.approx(fields['differential_pressure_Pa']/2,rel=3e-15)


def test_zero_pressure_mean_inverse_is_an_algebraic_zero_without_manufacturing_claim():
    assert nominal_wall_thickness_m(NominalThicknessInput(differential_pressure_Pa=0.,outside_diameter_m=.4,
        supplied_nominal_stress_Pa=12e6,diameter_basis='mean_from_outside'))==0


@pytest.mark.parametrize('pressure',[24e6,30e6])
def test_mean_inverse_does_not_return_a_solid_or_negative_bore(pressure):
    with pytest.raises(ValueError):
        nominal_wall_thickness_m(NominalThicknessInput(differential_pressure_Pa=pressure,outside_diameter_m=.4,
            supplied_nominal_stress_Pa=12e6,diameter_basis='mean_from_outside'))


def test_mean_inverse_preserves_coherent_stress_and_geometry_scaling():
    base=nominal_wall_thickness_m(NominalThicknessInput(differential_pressure_Pa=2e6,outside_diameter_m=.4,
        supplied_nominal_stress_Pa=12e6,diameter_basis='mean_from_outside'))
    scaled=nominal_wall_thickness_m(NominalThicknessInput(differential_pressure_Pa=6e6,outside_diameter_m=1.4,
        supplied_nominal_stress_Pa=36e6,diameter_basis='mean_from_outside'))
    assert scaled==pytest.approx(3.5*base,rel=3e-15)


def test_unknown_diameter_convention_is_not_silently_selected():
    with pytest.raises(ValueError):
        NominalHoopStressInput(differential_pressure_Pa=2e6,outside_diameter_m=.4,wall_thickness_m=.02,
                              diameter_basis='unspecified')
