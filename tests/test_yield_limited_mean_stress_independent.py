from pathlib import Path
import os
import numpy as np
import matintegrity
import pytest
from pipeline_integrity.contracts import EvidenceRef,RunContext
from pipeline_integrity.yield_limited_mean_stress import YieldLimitedMeanStressRequest,evaluate_yield_limited_mean_stress

CTX=RunContext(asset_id='elastic-anchor',scenario_id='fixed-life-peak-limit',clock_id='whole-cycles',origin='synthetic',
    evidence=(EvidenceRef(id='supplied-anchor',citation='Authored elastic stress/life anchor and reviewed Goodman normalization; no experimental fit claimed',origin='synthetic'),))


def request(**changes):
    return YieldLimitedMeanStressRequest(**dict(dict(asset_id=CTX.asset_id,location_id='reviewed-section',clock_id=CTX.clock_id,
        supplied_fully_reversed_failure_amplitude_pa=180e6,supplied_whole_cycle_anchor=12345.,supplied_yield_stress_pa=350e6,
        supplied_ultimate_strength_pa=620e6,supplied_tensile_peak_limit_pa=350e6,calibration_evidence_id='supplied-anchor',
        calibration_basis_id='actual-elastic-fixed-life',actual_elastic_amplitude_fixed_life_goodman_and_peak_limit_reviewed=True),**changes))


def reference(q):
    matrix=np.array([[1/q.supplied_fully_reversed_failure_amplitude_pa,1/q.supplied_ultimate_strength_pa],[1.,1.]])
    return np.linalg.solve(matrix,[1.,q.supplied_tensile_peak_limit_pa])


def test_actual_selected_dependency_version():
    assert Path(matintegrity.__file__).resolve().is_relative_to(Path(os.environ['PIPELINE_SELECTED_MATERIAL_SOURCE_ROOT']).resolve())


@pytest.mark.parametrize('baseline',[90e6,210e6,300e6])
@pytest.mark.parametrize('peak',[315e6,350e6])
def test_existing_owner_inverse_matches_independent_two_constraint_intersection(baseline,peak):
    q=request(supplied_fully_reversed_failure_amplitude_pa=baseline,supplied_tensile_peak_limit_pa=peak)
    r=evaluate_yield_limited_mean_stress(q,CTX);a,m=reference(q)
    assert r['status']=='computed'
    assert r['corrected_fixed_life_failure_amplitude_pa']==pytest.approx(a,rel=3e-12)
    assert r['tensile_mean_at_peak_limit_pa']==pytest.approx(m,rel=3e-12)
    assert r['corrected_fixed_life_failure_amplitude_pa']+r['tensile_mean_at_peak_limit_pa']==peak
    assert abs(r['constitutive_coordinate_residual_pa'])<.005
    assert r['whole_cycle_anchor']==12345. and not r['physical_stress_evolution_or_fatigue_life_predicted']


def test_retained_one_sided_peak_sequence_converges_to_elastic_yield_boundary():
    q=request();target=reference(q)[0];errors=[]
    for fraction in [.1,.01,.001,.0001]:
        r=evaluate_yield_limited_mean_stress(request(supplied_tensile_peak_limit_pa=350e6*(1-fraction)),CTX)
        errors.append(abs(r['corrected_fixed_life_failure_amplitude_pa']-target))
        assert not r['elastic_limit_boundary']
    assert all(b<a for a,b in zip(errors,errors[1:]))
    # Propagate the declared root's absolute-plus-relative stress budget into
    # a ratio of differences; the finest difference is not an exact root.
    coordinate_budget=3e-12*q.supplied_fully_reversed_failure_amplitude_pa
    ratio_budget=2*coordinate_budget/errors[0]
    assert errors[-1]/errors[0]==pytest.approx(.001,rel=0.,abs=ratio_budget)
    assert evaluate_yield_limited_mean_stress(q,CTX)['elastic_limit_boundary']


def test_common_stress_scaling_preserves_normalized_point_and_declared_life():
    first=evaluate_yield_limited_mean_stress(request(),CTX)
    second=evaluate_yield_limited_mean_stress(request(supplied_fully_reversed_failure_amplitude_pa=360e6,
        supplied_yield_stress_pa=700e6,supplied_ultimate_strength_pa=1240e6,supplied_tensile_peak_limit_pa=700e6),CTX)
    assert second['corrected_fixed_life_failure_amplitude_pa']==pytest.approx(2*first['corrected_fixed_life_failure_amplitude_pa'])
    assert second['whole_cycle_anchor']==first['whole_cycle_anchor']


def test_zero_mean_end_point_is_not_zero_cyclic_amplitude():
    r=evaluate_yield_limited_mean_stress(request(supplied_tensile_peak_limit_pa=180e6),CTX)
    assert r['corrected_fixed_life_failure_amplitude_pa']==180e6
    assert r['tensile_mean_at_peak_limit_pa']==0.


@pytest.mark.parametrize('changes',[dict(supplied_fully_reversed_failure_amplitude_pa=350e6),dict(supplied_fully_reversed_failure_amplitude_pa=420e6),dict(supplied_tensile_peak_limit_pa=351e6)])
def test_above_yield_and_unreviewed_mean_insensitivity_are_not_invented(changes):
    r=evaluate_yield_limited_mean_stress(request(**changes),CTX)
    assert r['status']=='out_of_scope' and 'corrected_fixed_life_failure_amplitude_pa' not in r


def test_peak_below_fixed_life_curve_has_no_nonnegative_mean_intersection():
    assert evaluate_yield_limited_mean_stress(request(supplied_tensile_peak_limit_pa=80e6),CTX)['status']=='no_failure_envelope_intersection_at_nonnegative_mean'


@pytest.mark.parametrize('changes',[dict(supplied_whole_cycle_anchor=None),dict(calibration_basis_id=None),dict(actual_elastic_amplitude_fixed_life_goodman_and_peak_limit_reviewed=False)])
def test_missing_anchor_or_calibration_is_not_defaulted(changes):
    assert evaluate_yield_limited_mean_stress(request(**changes),CTX)['status']=='needs_data'


def test_invalid_strength_and_foreign_context_are_rejected():
    with pytest.raises(ValueError):evaluate_yield_limited_mean_stress(request(supplied_ultimate_strength_pa=300e6),CTX)
    with pytest.raises(ValueError):evaluate_yield_limited_mean_stress(request(asset_id='other'),CTX)
