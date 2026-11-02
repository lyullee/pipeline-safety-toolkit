"""Authored swept-volume and pulse-count references, not prover calibration."""
from decimal import Decimal, localcontext
import math

import pytest
from pydantic import ValidationError
from scipy.integrate import solve_ivp
from scipy.optimize import brentq

from pipeline_integrity.compact_prover import CompactProverRequest as Request, evaluate_compact_prover as evaluate


def request(**changes):
    values=dict(meter_id="meter",proving_run_id="run-one",corrected_reference_volume_m3=.017,
        meter_volume_state_basis_id="reviewed-liquid-state",reference_volume_correction_basis_id="supplied-corrected-volume",
        prover_sweep_elapsed_time_s=3.1,whole_pulse_interval_elapsed_time_s=2.9,whole_meter_pulses_in_interval=173,
        elapsed_time_clock_basis_id="two-calibrated-elapsed-seconds",volume_at_declared_meter_state_reviewed=True,
        clock_scale_and_interval_pairing_reviewed=True,representative_stationary_pulse_frequency_reviewed=True)
    values.update(changes)
    return Request(**values)


@pytest.mark.parametrize("flow,factor",[(.004,9700.),(.071,271.),(.00009,173000.)])
def test_independent_piston_volume_and_pulse_phase_ode(flow,factor):
    area,length=.037,.81
    duration=area*length/flow
    phase=.37
    # Integrate separate piston position, transported volume and cumulative pulses.
    sol=solve_ivp(lambda t,y:(flow/area,flow,factor*flow),(0,2*duration),(0.,0.,phase),
        dense_output=True,rtol=1e-12,atol=1e-13,max_step=duration/25)
    sweep=brentq(lambda t:sol.sol(t)[0]-length,0,2*duration,xtol=1e-12)
    volume=sol.sol(sweep)[1]
    first_order=2
    whole_count=max(1,int(.71*factor*volume))
    t0=brentq(lambda t:sol.sol(t)[2]-first_order,0,2*duration,xtol=1e-12)
    t1=brentq(lambda t:sol.sol(t)[2]-(first_order+whole_count),0,2*duration,xtol=1e-12)
    nominal=.97*factor
    q=request(corrected_reference_volume_m3=volume,prover_sweep_elapsed_time_s=sweep,
        whole_pulse_interval_elapsed_time_s=t1-t0,whole_meter_pulses_in_interval=whole_count,
        nominal_indicated_pulse_factor_per_m3=nominal,nominal_factor_basis_id="declared-indicated-volume")
    r=evaluate(q)
    assert r.status=="computed" and r.observed_pulse_factor_per_m3==pytest.approx(factor,rel=3e-10)
    assert r.interpolated_pulses_during_sweep==pytest.approx(sol.sol(sweep)[2]-phase,rel=3e-10)
    assert r.reciprocal_volume_per_pulse_m3*r.observed_pulse_factor_per_m3==pytest.approx(1.)
    indicated=r.interpolated_pulses_during_sweep/nominal
    assert r.true_over_indicated_volume_multiplier==pytest.approx(volume/indicated,rel=2e-14)
    assert r.true_over_indicated_volume_multiplier==pytest.approx(.97,rel=3e-10)


@pytest.mark.parametrize("a,b,count,volume",[(3.1,2.9,173,.017),(.00013,.019,1,.0007),(197.,3.,2341,71.)])
def test_high_precision_clock_ratio_and_units(a,b,count,volume):
    with localcontext() as ctx:
        ctx.prec=70;d=Decimal.from_float
        pulses=Decimal(count)*d(a)/d(b);factor=pulses/d(volume)
    r=evaluate(request(prover_sweep_elapsed_time_s=a,whole_pulse_interval_elapsed_time_s=b,
        whole_meter_pulses_in_interval=count,corrected_reference_volume_m3=volume))
    assert r.interpolated_pulses_during_sweep==pytest.approx(float(pulses),rel=4e-15)
    assert r.observed_pulse_factor_per_m3==pytest.approx(float(factor),rel=4e-15)


def test_equal_clock_scaling_and_volume_scaling():
    base=evaluate(request())
    scaled=evaluate(request(prover_sweep_elapsed_time_s=31.,whole_pulse_interval_elapsed_time_s=29.))
    assert scaled.observed_pulse_factor_per_m3==pytest.approx(base.observed_pulse_factor_per_m3)
    assert evaluate(request(corrected_reference_volume_m3=.17)).observed_pulse_factor_per_m3==pytest.approx(base.observed_pulse_factor_per_m3/10)


def test_nominal_factor_is_optional_and_orientation_is_explicit():
    assert evaluate(request()).dimensionless_factor_status=="not_requested"
    r=evaluate(request(nominal_indicated_pulse_factor_per_m3=10000.))
    assert r.status=="computed" and r.dimensionless_factor_status=="needs_data"
    assert r.true_over_indicated_volume_multiplier is None
    assert not r.state_or_steel_corrections_inferred and not r.field_or_custody_transfer_accuracy_qualified


@pytest.mark.parametrize("field",["meter_id","proving_run_id","corrected_reference_volume_m3","meter_volume_state_basis_id",
    "reference_volume_correction_basis_id","prover_sweep_elapsed_time_s","whole_pulse_interval_elapsed_time_s",
    "whole_meter_pulses_in_interval","elapsed_time_clock_basis_id"])
def test_missing_calibration_state_or_clock_is_explicit(field):
    assert evaluate(request(**{field:None})).status=="needs_data"


@pytest.mark.parametrize("field",["volume_at_declared_meter_state_reviewed","clock_scale_and_interval_pairing_reviewed",
    "representative_stationary_pulse_frequency_reviewed"])
def test_unreviewed_volume_or_frequency_is_not_inferred(field):
    assert evaluate(request(**{field:False})).status=="needs_data"


@pytest.mark.parametrize("field,value",[("whole_meter_pulses_in_interval",0),("whole_meter_pulses_in_interval",1.5),
    ("whole_meter_pulses_in_interval",True),("corrected_reference_volume_m3",0.),("prover_sweep_elapsed_time_s",-1.),
    ("whole_pulse_interval_elapsed_time_s",float("nan")),("nominal_indicated_pulse_factor_per_m3",float("inf"))])
def test_invalid_contract(field,value):
    with pytest.raises(ValidationError):request(**{field:value})


def test_finite_support_and_request_replay():
    assert evaluate(request(corrected_reference_volume_m3=5e-324)).status=="out_of_scope"
    assert evaluate(request(prover_sweep_elapsed_time_s=5e-324,whole_pulse_interval_elapsed_time_s=1e308)).status=="out_of_scope"
    q=request();before=q.model_dump(mode="python")
    assert evaluate(Request.model_validate_json(q.model_dump_json()))==evaluate(q)
    assert q.model_dump(mode="python")==before
    with pytest.raises(ValidationError):evaluate(q.model_copy(update={"whole_meter_pulses_in_interval":0}))
