"""Bounded calibration of a synthetic pressure-driven network law with temporal holdout."""
import math
import pytest
from pipeline_integrity.contracts import RunContext, SignalFrame, digest
from pipeline_integrity.inverse import (FitParameter,FitRequest,Prediction,ResidualScale,fit_parameters,observations_from_frames)

K_TRUE=2.4e-4

def dp(t): return 1000.0+500.0*t

def provider(parameters,times,ctx):
    k=parameters['conductance']
    q=tuple(k*math.sqrt(dp(t)) for t in times)
    return Prediction(status='computed',asset_id=ctx.asset_id,clock_id=ctx.clock_id,time_s=times,
        values={'q':q},units={'q':'m^3/s'},provider_source_digest=digest('synthetic orifice network law v1'),
        evaluation_digest=digest((k,times,q)))

def build_observations(heldout_offset=0.0):
    times=tuple(float(t) for t in range(10))
    vals=tuple(K_TRUE*math.sqrt(dp(t))+(heldout_offset if t>=6 else 0.0) for t in times)
    source=digest({'times':times,'flow_m3_s':vals,'law':'Q=K*sqrt(dP)'})
    frames=tuple(SignalFrame(stream_id='synthetic-pipe',clock_id='elapsed-s',time_s=t,
        values={'q':v},units={'q':'m^3/s'},valid={'q':True},origin='synthetic') for t,v in zip(times,vals))
    obs=observations_from_frames(frames,observation_id='pressure-driven-flow',asset_id='pipe-A',
        source_digests={'authored':source},scales={'q':ResidualScale(value=1e-5,unit='m^3/s',source_id='declared-scale')})
    ctx=RunContext(asset_id='pipe-A',scenario_id='fixed-pressure-schedule',clock_id='elapsed-s',origin='synthetic')
    return obs,ctx

def request():
    return FitRequest(parameters=(FitParameter(id='conductance',unit='m^3/s/Pa^0.5',lower=1e-5,upper=5e-4,initial=1e-4),),
        provider_id='analytic_pressure_flow',provider_version='1',fit_end_s=4.,validation_start_s=6.,validation_end_s=9.)

def test_network_fit_recovers_conductance_from_darcy_orifice_pressure_flow_law():
    obs,ctx=build_observations()
    result=fit_parameters(obs,request(),ctx,provider)
    train=[i for i,t in enumerate(obs.time_s) if t<=4.]
    q=[obs.channels[0].values[i] for i in train]
    x=[math.sqrt(dp(obs.time_s[i])) for i in train]
    closed_form=sum(a*b for a,b in zip(x,q))/sum(a*a for a in x)
    assert result['status']=='fitted'
    assert result['fitted_parameters']['conductance']==pytest.approx(K_TRUE,abs=2e-11)
    assert result['fitted_parameters']['conductance']==pytest.approx(closed_form,abs=2e-11)
    assert result['local_identifiability']['rank']==1
    assert result['validation']['used_for_parameter_selection'] is False
    assert result['validation']['fitted']['normalized_rmse']<1e-5

def test_heldout_flow_change_does_not_change_fitted_network_parameter():
    obs,ctx=build_observations()
    changed,ctx2=build_observations(heldout_offset=0.02)
    a=fit_parameters(obs,request(),ctx,provider)
    b=fit_parameters(changed,request(),ctx2,provider)
    assert a['fitted_parameters']==pytest.approx(b['fitted_parameters'],abs=1e-12)
    assert b['validation']['fitted']['normalized_rmse']>a['validation']['fitted']['normalized_rmse']
