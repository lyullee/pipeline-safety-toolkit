"""Declared calibration correctness, not instrument calibration/accuracy."""
from decimal import Decimal, localcontext
import math

import numpy as np
import pytest
from pydantic import ValidationError
from scipy.integrate import solve_ivp

from pipeline_integrity.vibrating_density import VibratingDensityCalibration as Calibration, VibratingDensityRequest as Request, observe_vibrating_element_density as observe


def calibration(**changes):
    values=dict(instrument_id="sensor-one",calibration_id="authored-calibration",
        quadratic_kg_m3_s2=8e8,linear_kg_m3_s=-900.,constant_kg_m3=90.,
        period_support_s=(.0001,.01),temperature_support_K=(280.,330.),
        pressure_support_pa_abs=(80000.,900000.),coefficient_units_and_applicability_reviewed=True)
    values.update(changes)
    return Calibration(**values)


def request(**changes):
    values=dict(calibration=calibration(),observed_instrument_id="sensor-one",oscillation_period_s=.001,
        temperature_K=300.,pressure_pa_abs=140000.,phase="single_liquid",single_phase_and_measurement_state_reviewed=True)
    values.update(changes)
    return Request(**values)


@pytest.mark.parametrize("density",[3.,180.,970.])
@pytest.mark.parametrize("period_bias",[0.,.000017])
def test_independent_loaded_spring_mass_ode_period_and_energy(density,period_bias):
    stiffness,solid_mass,volume=1300.,.002,.000006
    mass=solid_mass+density*volume
    expected_period=2*math.pi*math.sqrt(mass/stiffness)
    def crossing(t,y):return y[0]
    crossing.direction=1
    sol=solve_ivp(lambda t,y:(y[1],-stiffness*y[0]/mass),(0,3*expected_period),(1.,0.),
        events=crossing,rtol=2e-12,atol=2e-13,max_step=expected_period/70)
    # A bias of the reported period is distinct from equal timestamp latency.
    timestamps=sol.t_events[0]
    common_latency=.017
    assert (timestamps[1]+common_latency)-(timestamps[0]+common_latency)==pytest.approx(timestamps[1]-timestamps[0],rel=1e-13)
    observed_period=timestamps[1]-timestamps[0]+period_bias
    energy=.5*mass*sol.y[1]**2+.5*stiffness*sol.y[0]**2
    assert np.max(abs(energy-energy[0]))/energy[0]<3e-10
    a=stiffness/(4*math.pi**2*volume)
    c=calibration(quadratic_kg_m3_s2=a,linear_kg_m3_s=-2*a*period_bias,
        constant_kg_m3=a*period_bias**2-solid_mass/volume,period_support_s=(.00001,1.))
    r=observe(request(calibration=c,oscillation_period_s=observed_period,
        phase="single_gas" if density<10 else "single_liquid"))
    assert r.status=="computed" and r.density_kg_m3==pytest.approx(density,rel=3e-9)
    assert not r.coefficients_estimated and not r.measurement_uncertainty_or_field_accuracy_qualified


@pytest.mark.parametrize("period",[.0001,.00037,.0031,.01])
def test_dimensioned_polynomial_against_high_precision(period):
    c=calibration();r=observe(request(oscillation_period_s=period))
    with localcontext() as ctx:
        ctx.prec=80;d=Decimal.from_float;t=d(period)
        expected=d(c.quadratic_kg_m3_s2)*t*t+d(c.linear_kg_m3_s)*t+d(c.constant_kg_m3)
    assert r.density_kg_m3==pytest.approx(float(expected),rel=3e-15)


def test_millisecond_coefficient_conversion_is_explicit_si_data():
    # Authored coefficients originally expressed against period milliseconds.
    a,b,c=17.,-2.,51.
    r=observe(request(calibration=calibration(quadratic_kg_m3_s2=a*1e6,linear_kg_m3_s=b*1000,constant_kg_m3=c),oscillation_period_s=.002))
    assert r.density_kg_m3==pytest.approx(17*4-2*2+51)


def test_temperature_pressure_screen_does_not_invent_compensation():
    low=observe(request(temperature_K=280.,pressure_pa_abs=80000.))
    high=observe(request(temperature_K=330.,pressure_pa_abs=900000.))
    assert low.density_kg_m3==high.density_kg_m3
    assert not low.temperature_pressure_compensation_inferred


@pytest.mark.parametrize("field",["calibration","observed_instrument_id","oscillation_period_s","temperature_K","pressure_pa_abs"])
def test_unknown_inputs_are_not_replaced_by_eos(field):
    assert observe(request(**{field:None})).status=="needs_data"


@pytest.mark.parametrize("changes",[dict(phase="unknown"),dict(single_phase_and_measurement_state_reviewed=False),
    dict(calibration=calibration(coefficient_units_and_applicability_reviewed=False))])
def test_reviews_remain_explicit(changes):
    assert observe(request(**changes)).status=="needs_data"


@pytest.mark.parametrize("changes",[dict(observed_instrument_id="different-device"),dict(phase="two_phase"),
    dict(temperature_K=279.),dict(pressure_pa_abs=900001.),dict(oscillation_period_s=.011),
    dict(calibration=calibration(density_support_kg_m3=(1.,50.)))])
def test_support_exits_and_device_mismatch(changes):
    assert observe(request(**changes)).status=="out_of_scope"


@pytest.mark.parametrize("constant",[0.,-10.,1e308])
def test_nonpositive_and_overflow_polynomial_is_withheld(constant):
    c=calibration(quadratic_kg_m3_s2=1e308 if constant>0 else 0.,linear_kg_m3_s=0.,constant_kg_m3=constant,period_support_s=(.0001,10.))
    assert observe(request(calibration=c,oscillation_period_s=2.)).status=="out_of_scope"


@pytest.mark.parametrize("changes",[dict(period_support_s=(1.,.1)),dict(temperature_support_K=(330.,280.)),
    dict(pressure_support_pa_abs=(200000.,100000.)),dict(quadratic_kg_m3_s2=float("nan")),dict(linear_kg_m3_s=True)])
def test_invalid_calibration(changes):
    with pytest.raises(ValidationError):calibration(**changes)


def test_replay_and_forged_contract_revalidation():
    q=request();saved=q.model_dump(mode="python")
    assert observe(Request.model_validate_json(q.model_dump_json()))==observe(q)
    assert q.model_dump(mode="python")==saved
    with pytest.raises(ValidationError):observe(q.model_copy(update={"oscillation_period_s":-1.}))
