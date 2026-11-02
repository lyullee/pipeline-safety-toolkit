"""Individual compression paths checked against work integrals and energy identities."""
import math

import pytest

from pipeline_integrity.compression_process import IdealGasCompressionProcess, evaluate_ideal_gas_compression_process


def case(n=1.0, ratio=4.0, flow=2.0, cp=1005.0):
    return IdealGasCompressionProcess(inlet_pressure_pa_abs=1e5, outlet_pressure_pa_abs=1e5*ratio,
        inlet_temperature_k=300.0, specific_gas_constant_j_kg_k=287.05,
        polytropic_exponent=n, mass_flow_kg_s=flow, constant_cp_j_kg_k=cp)


@pytest.mark.parametrize("n", [1.0, 1.2, 1.4, 1.65])
def test_work_matches_independent_log_pressure_volume_integral(n):
    q = case(n=n)
    got = evaluate_ideal_gas_compression_process(q).values
    # Integrate v*dp in log-pressure coordinates with composite Simpson quadrature.
    count = 2048
    stop = math.log(q.outlet_pressure_pa_abs/q.inlet_pressure_pa_abs)
    step = stop/count
    def integrand(x):
        pressure = q.inlet_pressure_pa_abs * math.exp(x)
        specific_volume = q.specific_gas_constant_j_kg_k*q.inlet_temperature_k/q.inlet_pressure_pa_abs * math.exp(-x/n)
        return specific_volume*pressure
    reference = step/3*(integrand(0)+integrand(stop)+sum((4 if i%2 else 2)*integrand(i*step) for i in range(1,count)))
    assert got["specific_work_input_j_kg"] == pytest.approx(reference, rel=2e-12)
    assert got["specific_enthalpy_rise_j_kg"] == pytest.approx(got["specific_work_input_j_kg"]+got["specific_heat_into_gas_j_kg"], abs=2e-10)


def test_isothermal_work_heat_and_near_isothermal_limit():
    exact = evaluate_ideal_gas_compression_process(case()).values
    expected = 287.05*300*math.log(4)
    assert exact["specific_work_input_j_kg"] == pytest.approx(expected, rel=2e-15)
    assert exact["outlet_temperature_k"] == 300.0
    assert exact["specific_heat_into_gas_j_kg"] == pytest.approx(-expected)
    near = evaluate_ideal_gas_compression_process(case(n=1+1e-10)).values
    assert near["specific_work_input_j_kg"] == pytest.approx(expected, rel=1e-9)


def test_isentropic_exponent_has_zero_heat_and_matches_enthalpy():
    cp, r = 1005.0, 287.05
    gamma = cp/(cp-r)
    result = evaluate_ideal_gas_compression_process(case(n=gamma, cp=cp)).values
    assert abs(result["specific_heat_into_gas_j_kg"]) < 1e-9
    assert result["specific_work_input_j_kg"] == pytest.approx(cp*result["temperature_rise_k"], rel=2e-14)


def test_zero_flow_preserves_specific_work_and_optional_heat_is_withheld():
    a = evaluate_ideal_gas_compression_process(case(flow=0, cp=None)).values
    b = evaluate_ideal_gas_compression_process(case(flow=3, cp=None)).values
    assert a["work_input_power_w"] == 0
    assert a["specific_work_input_j_kg"] == b["specific_work_input_j_kg"]
    assert "specific_heat_into_gas_j_kg" not in a


@pytest.mark.parametrize("changes", [dict(polytropic_exponent=0.9),dict(outlet_pressure_pa_abs=1e5),dict(constant_cp_j_kg_k=200.0),dict(inlet_temperature_k=float("nan"))])
def test_invalid_individual_process_inputs_are_rejected(changes):
    with pytest.raises(ValueError):
        IdealGasCompressionProcess.model_validate(case().model_dump() | changes)
