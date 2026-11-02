"""Conditional blocked-channel velocity-frequency evidence, not a force-harmonic test."""
import json,os,sys
from pathlib import Path
sys.path.insert(0,os.environ['PIPELINE_WAKE_REFERENCE_HELPER_ROOT'])
import numpy as np
import pytest
from scipy.stats import t
from ost_wake_reference import ROOT,DIAMETER,observations,training_only_coefficient
from pipeline_integrity.wind_screening import VortexFrequencyInput,evaluate_vortex_frequency

@pytest.fixture(scope='module')
def rows():return observations()

@pytest.mark.parametrize('index',range(4))
def test_actual_four_signals_instrument_band_refinement(rows,index,record_property):
    r=rows[index]
    assert r['time'].shape==(131072,) and np.isfinite(r['velocity']).all()
    peaks=[s['peak_Hz'] for s in r['spectra']]
    assert max(peaks)-min(peaks)<=1000/8192
    record_property('actual_signal',json.dumps({k:v for k,v in r.items() if k not in ['time','velocity']}))

def test_recorded_clock_rounding_is_bounded_but_not_declared_exact(rows,record_property):
    deviations=[]
    for r in rows:
        assert np.all(np.diff(r['time'])>0)
        error=float(np.max(abs(r['time']-np.arange(131072)/1000)))
        assert 0<error<=1.00001e-5
        endpoint_rate=131071/r['time'][-1]
        clock_shift=abs(endpoint_rate/1000-1)*r['measured_dominant_wake_Hz']
        assert clock_shift<1000/32768
        deviations.append(dict(file=r['filename'],maximum_rounding_s=error,
            endpoint_rate_Hz=endpoint_rate,peak_clock_shift_Hz=clock_shift))
    record_property('clock',json.dumps(deviations))

def test_upstream_pitot_not_local_wake_mean_is_used(rows):
    for r in rows:
        assert not np.isclose(r['upstream_m_s'],np.mean(r['velocity']),rtol=.1)
    assert rows[0]['upstream_m_s']==5.08

def test_training_only_fixed_geometry_coefficient_does_not_read_holdout(rows):
    a=training_only_coefficient(rows)
    changed=[dict(r,measured_dominant_wake_Hz=999.) if r['upstream_m_s']==16. else r for r in rows]
    assert training_only_coefficient(changed)==a
    assert a[2]==3

def test_unseen_speed_dominant_wake_prediction_with_training_scatter(rows,record_property):
    mean,sd,n=training_only_coefficient(rows)
    hold=next(r for r in rows if r['upstream_m_s']==16.)
    result=evaluate_vortex_frequency(VortexFrequencyInput(cross_flow_speed_m_s=16.,
        outside_diameter_m=DIAMETER,strouhal_number=mean))
    # Conditional Student-t scatter prediction, not uncertainty on all physical conditions.
    half=float(t.ppf(.975,n-1)*sd*np.sqrt(1+1/n)*16/DIAMETER)
    observed=hold['measured_dominant_wake_Hz'];pred=result.lift_frequency_Hz
    assert abs(observed-pred)<=half
    record_property('heldout',json.dumps(dict(observed_dominant_wake_Hz=observed,
        predicted_coordinate_Hz=pred,conditional_95_scatter_half_width_Hz=half,
        train_coefficient=mean,train_coefficient_sd=sd,
        physical_force_harmonic_identified=False,pressure_or_force_sensor_present=False)))
