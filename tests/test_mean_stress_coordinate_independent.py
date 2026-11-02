"""Independent failure-envelope intersections, ratio scaling and declared limits."""
import pytest
from scipy.optimize import brentq
from pipeline_integrity.contracts import EvidenceRef,RunContext
from pipeline_integrity.mean_stress_coordinate import MeanStressCoordinateRequest,evaluate_mean_stress_coordinate

def calc(correction='goodman',**kw):
    c=RunContext(asset_id='cycle',scenario_id='elastic_mean',clock_id='whole_cycles',origin='synthetic',
        evidence=(EvidenceRef(id='calibration',citation='Authored elastic tensile-mean coordinate reference',origin='synthetic'),))
    data=dict(asset_id=c.asset_id,location_id='section',clock_id=c.clock_id,time_s=0.,correction=correction,
        alternating_amplitude_pa=80e6,tensile_mean_stress_pa=100e6,yield_stress_pa=300e6,ultimate_strength_pa=500e6,
        maximum_supported_equivalent_amplitude_pa=250e6,calibration_basis_id='authored-curve-coordinate',
        calibration_evidence_id='calibration',elastic_tensile_mean_and_correction_calibration_reviewed=True)
    return evaluate_mean_stress_coordinate(MeanStressCoordinateRequest(**(data|kw)),c)

@pytest.mark.parametrize('kind',['goodman','soderberg','gerber'])
@pytest.mark.parametrize('mean',[0.,50e6,150e6])
def test_equivalent_coordinate_intersects_independent_failure_envelope(kind,mean):
    result=calc(kind,tensile_mean_stress_pa=mean)
    scale=300e6 if kind=='soderberg' else 500e6
    power=2 if kind=='gerber' else 1
    # Solve for the fully reversed curve intercept, independently of the native
    # virtual-coordinate transformation, from the supplied failure envelope.
    if mean==0:reference=80e6
    else:reference=brentq(lambda reversed_amplitude:80e6/reversed_amplitude+(mean/scale)**power-1.,80e6,250e6,xtol=1e-5)
    assert result.status=='computed'
    assert result.equivalent_fully_reversed_amplitude_pa==pytest.approx(reference,rel=3e-14)
    assert result.native_mean_is_virtual_coordinate==(kind=='gerber')
    assert result.native_strength_is_virtual_normalization==(kind=='soderberg')

def test_soderberg_goodman_gerber_relative_penalties_for_elastic_tensile_cycles():
    corrected=[calc(k).equivalent_fully_reversed_amplitude_pa for k in ('soderberg','goodman','gerber')]
    assert corrected[0]>corrected[1]>corrected[2]>80e6

@pytest.mark.parametrize('kind',['goodman','soderberg','gerber'])
def test_common_stress_scale_preserves_normalized_curve_coordinate(kind):
    base=calc(kind)
    scaled=calc(kind,alternating_amplitude_pa=160e6,tensile_mean_stress_pa=200e6,yield_stress_pa=600e6,
        ultimate_strength_pa=1000e6,maximum_supported_equivalent_amplitude_pa=500e6)
    assert scaled.equivalent_fully_reversed_amplitude_pa==pytest.approx(2*base.equivalent_fully_reversed_amplitude_pa)

def test_zero_amplitude_is_retained_without_inventing_a_life():
    result=calc(alternating_amplitude_pa=0.)
    assert result.equivalent_fully_reversed_amplitude_pa==0
    assert result.physical_stress_or_fatigue_life_predicted is False

def test_negative_mean_does_not_receive_an_unreviewed_compressive_benefit():
    assert calc(tensile_mean_stress_pa=-1e6).status=='out_of_scope'

def test_yielding_tensile_peak_does_not_use_the_elastic_mean_correction():
    assert calc(tensile_mean_stress_pa=220e6).status=='out_of_scope'

def test_unknown_material_or_curve_support_is_not_filled():
    assert calc(ultimate_strength_pa=None).status=='needs_data'
    assert calc(maximum_supported_equivalent_amplitude_pa=None).status=='needs_data'

def test_supplied_curve_support_limits_the_returned_coordinate():
    assert calc(maximum_supported_equivalent_amplitude_pa=90e6).status=='out_of_scope'

def test_inconsistent_supplied_strengths_are_rejected():
    with pytest.raises(ValueError):calc(ultimate_strength_pa=200e6)
