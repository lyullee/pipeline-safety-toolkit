"""Independent coefficient-form references for two supplied stress-life curves."""
import pytest

from pipeline_integrity.contracts import EvidenceRef,RunContext
from pipeline_integrity.stress_life_coordinate import StressLifeCoordinateRequest as Request
from pipeline_integrity.stress_life_coordinate import evaluate_stress_life_coordinate as evaluate


def make(**changes):
    context=RunContext(asset_id='curve_coordinate',scenario_id='supplied_power_law',clock_id='cycles',origin='synthetic',
        evidence=(EvidenceRef(id='curve',citation='Authored reference-point stress-life calibration',origin='synthetic'),))
    values=dict(asset_id=context.asset_id,location_id='component',clock_id=context.clock_id,time_s=0.,
        coordinate_semantics='effective_amplitude',query_stress_coordinate_pa=200e6,
        reference_stress_coordinate_pa=1e9,reference_life_cycles=1.,positive_inverse_log_slope=5.,
        calibration_basis_id='authored_constant_amplitude_curve',curve_evidence_id='curve',curve_and_cycle_coordinate_reviewed=True)
    values.update(changes)
    return Request(**values),context


@pytest.mark.parametrize('coefficient,amplitude,intensification',[(1e9,200e6,1.),(.9e9,150e6,1.5),(.5e9,100e6,2.)])
def test_effective_component_amplitude_matches_supplied_coefficient_form(coefficient,amplitude,intensification):
    q,c=make(query_stress_coordinate_pa=amplitude,reference_stress_coordinate_pa=coefficient,
        supplied_query_stress_multiplier=intensification)
    result=evaluate(q,c)
    assert result.status=='computed'
    assert result.curve_life_cycles==pytest.approx((coefficient/(intensification*amplitude))**5,rel=3e-14)
    assert result.native_material_receipt['native']['payload']['inputs']['stress_range_Pa']==2*intensification*amplitude
    assert result.cycles_are_whole_cycles is True


@pytest.mark.parametrize('maximum_stress',[8e6,20e6,40e6])
def test_maximum_stress_curve_matches_independent_cycles_times_stress_power_form(maximum_stress):
    source_exponent=.204
    reference_stress,reference_cycles=20e6,5000.
    coefficient=reference_cycles*reference_stress**(1/source_exponent)
    q,c=make(coordinate_semantics='maximum_stress_same_cycle_basis',query_stress_coordinate_pa=maximum_stress,
        reference_stress_coordinate_pa=reference_stress,reference_life_cycles=reference_cycles,
        positive_inverse_log_slope=1/source_exponent,calibration_basis_id='authored_maximum_stress_cycle_basis')
    result=evaluate(q,c)
    assert result.status=='computed'
    assert result.curve_life_cycles==pytest.approx(coefficient/maximum_stress**(1/source_exponent),rel=3e-14)
    assert result.native_range_is_virtual_curve_coordinate is True
    assert result.field_or_material_fatigue_qualification is False


def test_amplitude_and_full_range_change_coordinates_without_doubling_cycle_count():
    q,c=make();amplitude=evaluate(q,c)
    q,c=make(coordinate_semantics='full_range',query_stress_coordinate_pa=400e6,reference_stress_coordinate_pa=2e9)
    full=evaluate(q,c)
    assert amplitude.curve_life_cycles==full.curve_life_cycles
    assert full.native_range_is_virtual_curve_coordinate is False


def test_coherent_stress_unit_scale_leaves_cycle_life_unchanged():
    q,c=make();a=evaluate(q,c)
    q,c=make(query_stress_coordinate_pa=200e3,reference_stress_coordinate_pa=1e6);b=evaluate(q,c)
    assert a.curve_life_cycles==pytest.approx(b.curve_life_cycles,rel=3e-14)


@pytest.mark.parametrize('field',['reference_life_cycles','positive_inverse_log_slope'])
def test_unknown_curve_information_is_not_replaced_with_a_material_default(field):
    q,c=make(**{field:None});result=evaluate(q,c)
    assert result.status=='needs_data'
    assert result.curve_life_cycles is None


def test_maximum_stress_curve_without_a_cycle_basis_is_withheld():
    q,c=make(coordinate_semantics='maximum_stress_same_cycle_basis',calibration_basis_id=None)
    assert evaluate(q,c).status=='needs_data'
