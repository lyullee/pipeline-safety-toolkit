"""Bounded analytic qualification of the real creep Larson–Miller exchange."""

import math
import runpy

import pytest
from pydantic import ValidationError

from pipeline_integrity.creep_exchange import evaluate_creep


CASES, _, CONTEXT = runpy.run_path("examples/creep_methods.py")["make_cases"]()
BASE = CASES["creep.larson_miller"]


def evaluate(*, temperature_K=None, constant=None, rupture_time_s=None):
    parameters = dict(BASE.parameters)
    if temperature_K is not None:
        parameters["temperature_K"] = temperature_K
    if constant is not None:
        parameters["constant"] = constant
    if rupture_time_s is not None:
        parameters["rupture_time_s"] = rupture_time_s
    request = BASE.model_copy(update={"parameters": parameters})
    return evaluate_creep(request, CONTEXT)


@pytest.mark.parametrize(
    "temperature,constant,time_s",
    [
        (923.15, 20.0, 36000.0),
        (900.0, 18.5, 3600.0),
        (1000.0, 22.0, 360000.0),
        (750.0, 15.0, 86400.0),
        (1200.0, 25.0, 1.0e7),
    ],
)
def test_parameter_matches_independent_base10_hours_relation(temperature, constant, time_s):
    record = evaluate(temperature_K=temperature, constant=constant, rupture_time_s=time_s)
    expected = temperature * (constant + math.log10(time_s / 3600.0))
    assert record["status"] == "computed"
    assert record["values"]["parameter_K"] == pytest.approx(expected, rel=2e-14, abs=1e-10)
    assert record["output_units"]["parameter_K"] == "K"
    assert record["native_receipt"] is not None
    native = record["native_receipt"]["native"]["payload"]
    assert native["model_id"] == "creep.larson_miller"
    assert native["values"]["parameter_K"] == pytest.approx(expected, rel=2e-14, abs=1e-10)
    assert native["context"]["asset_id"] == CONTEXT.asset_id
    assert record["probability_semantics"] == {}


def test_one_decade_rupture_time_increases_parameter_by_temperature():
    base = evaluate(rupture_time_s=3600.0)["values"]["parameter_K"]
    decade = evaluate(rupture_time_s=36000.0)["values"]["parameter_K"]
    assert decade - base == pytest.approx(BASE.parameters["temperature_K"], rel=2e-14)


def test_temperature_and_constant_scalings_follow_the_formula():
    baseline = evaluate(temperature_K=900.0, constant=20.0, rupture_time_s=7200.0)["values"]["parameter_K"]
    doubled_temperature = evaluate(temperature_K=1800.0, constant=20.0, rupture_time_s=7200.0)["values"]["parameter_K"]
    shifted_constant = evaluate(temperature_K=900.0, constant=21.0, rupture_time_s=7200.0)["values"]["parameter_K"]
    assert doubled_temperature == pytest.approx(2.0 * baseline, rel=2e-14)
    assert shifted_constant - baseline == pytest.approx(900.0, rel=2e-14)


@pytest.mark.parametrize("bad_time", [0.0, -1.0])
def test_nonpositive_rupture_time_is_rejected_by_native_contract(bad_time):
    parameters = dict(BASE.parameters, rupture_time_s=bad_time)
    request = BASE.model_copy(update={"parameters": parameters})
    with pytest.raises(ValidationError):
        evaluate_creep(request, CONTEXT)
