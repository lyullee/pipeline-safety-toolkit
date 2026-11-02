"""Existing typed material-job API versus independent thinning/event references."""
from pathlib import Path
import os

import matintegrity
import pytest
from scipy.integrate import solve_ivp

from pipeline_integrity.contracts import EvidenceRef,RunContext
from pipeline_integrity.material_exchange import prepare_material_job,evaluate_material_job

CONTEXT=RunContext(asset_id='authored-wall',scenario_id='constant-loss-threshold',clock_id='elapsed-si-seconds',origin='synthetic',
    evidence=(EvidenceRef(id='supplied-rate-and-threshold',citation='Authored constant SI loss rate and supplied positive remaining-wall threshold; no failure-capacity inference',origin='synthetic'),))


def job(current=.012,required=.004,rate=2e-12,horizon=1e8):
    return prepare_material_job('corrosion.uniform_forecast',dict(current_thickness_m=current,
        required_thickness_m=required,rate_m_s=rate,horizon_s=horizon),CONTEXT,
        location_id='declared-wall',evidence_ids=('supplied-rate-and-threshold',))


def run(current=.012,required=.004,rate=2e-12,horizon=1e8):
    receipt=evaluate_material_job(job(current,required,rate,horizon),CONTEXT)
    receipt.check_integrity()
    return receipt,receipt.native.payload['values']


def test_selected_immutable_owner_path_is_actual_import():
    assert Path(matintegrity.__file__).resolve().is_relative_to(Path(os.environ['PIPELINE_SELECTED_MATERIAL_SOURCE_ROOT']).resolve())


@pytest.mark.parametrize('current,required,rate,horizon',[(.012,.004,2e-12,1e8),(.020,.008,5e-12,1e8),
    (.007,.001,1e-11,1e7),(.010,.009,4e-13,1e8)])
def test_existing_public_job_time_and_forecast_match_independent_event_ode(current,required,rate,horizon):
    receipt,v=run(current,required,rate,horizon)
    assert receipt.native.status=='computed'
    rough_horizon=(current-required)/rate*1.25
    def threshold(t,state):return state[0]-required
    threshold.terminal=True;threshold.direction=-1
    sol=solve_ivp(lambda t,state:[-rate],(0.,rough_horizon),[current],events=threshold,
        method='DOP853',rtol=2e-12,atol=1e-15,dense_output=True)
    assert sol.success and len(sol.t_events[0])==1
    assert v['time_to_required_s']==pytest.approx(sol.t_events[0][0],rel=2e-11)
    assert v['predicted_thickness_m']==pytest.approx(float(sol.sol(horizon)[0]),abs=2e-14)
    assert v['forecast_margin_m']==pytest.approx(v['predicted_thickness_m']-required,abs=1e-17)
    assert receipt.job.output_units['time_to_required_s']=='s'
    assert receipt.job.probability_semantics=={}
    assert not receipt.field_or_design_acceptance


@pytest.mark.parametrize('current,required,expected',[(.012,.004,None),(.004,.004,0.),(.003,.004,0.)])
def test_zero_rate_no_finite_future_crossing_and_already_reached_threshold_are_distinct(current,required,expected):
    _,v=run(current,required,0.,1e10)
    assert v['time_to_required_s']==expected
    assert v['predicted_thickness_m']==current
    assert v['thickness_margin_m']==pytest.approx(current-required)


def test_exact_threshold_time_remains_positive_wall_not_failure_probability():
    current,required,rate=.012,.004,2e-12
    elapsed=(current-required)/rate;_,v=run(current,required,rate,elapsed)
    assert v['predicted_thickness_m']==pytest.approx(required,abs=2e-18)
    assert abs(v['forecast_margin_m'])<2e-18


def test_exhausted_wall_forecast_is_withheld_but_earlier_threshold_time_is_retained():
    receipt,v=run(.010,.002,1e-6,10000.)
    assert receipt.native.status=='out_of_scope'
    assert v['predicted_thickness_m'] is None and v['forecast_margin_m'] is None
    assert v['time_to_required_s']==pytest.approx(8000.)


@pytest.mark.parametrize('scale',[.1,10.,1000.])
def test_common_length_rate_scaling_preserves_time_and_scales_wall(scale):
    _,a=run();_,b=run(.012*scale,.004*scale,2e-12*scale,1e8)
    assert b['time_to_required_s']==pytest.approx(a['time_to_required_s'],rel=2e-15)
    assert b['predicted_thickness_m']==pytest.approx(a['predicted_thickness_m']*scale,rel=2e-15)


def test_declared_customary_length_and_year_conversion_uses_seconds_not_a_hidden_clock():
    inch=.0254;declared_year=365.25*86400
    current=.45*inch;required=.18*inch;rate=6e-3*inch/declared_year
    _,v=run(current,required,rate,0.)
    assert v['time_to_required_s']/declared_year==pytest.approx((.45-.18)/.006,rel=3e-15)


@pytest.mark.parametrize('changes',[{'current_thickness_m':0.},{'required_thickness_m':0.},{'rate_m_s':-1.},
    {'horizon_s':-1.},{'rate_m_s':None},{'required_thickness_m':None}])
def test_unsupported_or_missing_inputs_are_not_replaced_with_defaults(changes):
    q=job();q=q.model_copy(update={'parameters':q.parameters|changes})
    with pytest.raises(ValueError):evaluate_material_job(q,CONTEXT)


@pytest.mark.parametrize('field',['asset_id','clock_id'])
def test_existing_public_context_boundary_rejects_another_asset_or_clock(field):
    with pytest.raises(ValueError):evaluate_material_job(job(),CONTEXT.model_copy(update={field:'other'}))


def test_owner_schema_version_is_not_silently_reinterpreted():
    with pytest.raises(ValueError):evaluate_material_job(job().model_copy(update={'model_version':'unsupported-version'}),CONTEXT)
