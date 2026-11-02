"""Independent engineering-unit expansion and temperature-curve references."""
import pytest
from pipeline_integrity.liquid_reference_volume import LiquidReferenceVolumeRequest as Request
from pipeline_integrity.liquid_reference_volume import evaluate_liquid_reference_volume as evaluate
from pipeline_integrity.thermal_expansion import ExpansionCurve,ExpansionKnot


TREF=(60-32)*5/9+273.15
def curve(a=.00108,b=0.,reference=TREF,low=None,high=None):
    low=reference-40 if low is None else low;high=reference+40 if high is None else high
    return ExpansionCurve(id="authored_volume_curve",material_id="supplied_liquid",evidence_id="authored_coefficients",
        knots=tuple(ExpansionKnot(temperature_K=t,tangent_expansion_per_K=a+b*(t-reference)) for t in (low,reference,high)),
        tangent_small_engineering_strain_basis_reviewed=True)


def calc(volume,temperature,**changes):
    args=dict(observed_volume_m3=volume,observed_temperature_K=temperature,reference_temperature_K=TREF,
              volumetric_expansion_curve=curve(),common_pressure_and_volumetric_engineering_basis_reviewed=True)
    args.update(changes)
    return evaluate(Request(**args))


@pytest.mark.parametrize("reference_volume,temp_f",[(1.2,40.),(5.,60.),(10.,95.)])
def test_kelvin_curve_matches_independent_fahrenheit_volume_coordinate(reference_volume,temp_f):
    observed=reference_volume*(1+.0006*(temp_f-60))
    result=calc(observed,(temp_f-32)*5/9+273.15)
    assert result.reference_volume_m3==pytest.approx(reference_volume,rel=3e-15)
    assert result.observed_minus_reference_volume_m3==pytest.approx(observed-reference_volume,abs=2e-15)


@pytest.mark.parametrize("delta",[-20.,20.,35.])
def test_temperature_dependent_volumetric_curve_matches_independent_integral(delta):
    a,b=.001,.00001
    strain=a*delta+b*delta**2/2
    expected_reference=3.7
    result=calc(expected_reference*(1+strain),TREF+delta,volumetric_expansion_curve=curve(a,b))
    assert result.volumetric_engineering_strain==pytest.approx(strain,rel=3e-14)
    assert result.reference_volume_m3==pytest.approx(expected_reference,rel=3e-15)


def test_observed_volume_scaling_preserves_reference_conversion_and_sign():
    a,b=calc(3.,TREF-15),calc(12.,TREF-15)
    assert b.reference_volume_m3==pytest.approx(4*a.reference_volume_m3)
    assert a.observed_minus_reference_volume_m3<0


def test_missing_curve_or_unreviewed_volumetric_basis_does_not_infer_axial_conversion():
    assert calc(1.,TREF,volumetric_expansion_curve=None).status=="needs_data"
    assert calc(1.,TREF,common_pressure_and_volumetric_engineering_basis_reviewed=False).reference_volume_m3 is None


def test_curve_is_not_extrapolated_beyond_its_declared_temperature_support():
    assert calc(1.,TREF+50).status=="out_of_scope"


def test_nonpositive_volume_ratio_is_withheld_instead_of_clipped():
    c=curve(a=.01,reference=300.,low=90.,high=310.)
    result=calc(1.,100.,reference_temperature_K=300.,volumetric_expansion_curve=c)
    assert result.status=="out_of_scope" and result.reference_volume_m3 is None
    assert result.volumetric_engineering_strain==-2.
