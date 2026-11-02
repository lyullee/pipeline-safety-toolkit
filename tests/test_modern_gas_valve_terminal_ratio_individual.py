"""Explicit normal-flow, expansion-factor and terminal-pressure-ratio references."""
import math
import pytest
from pipeline_integrity.models.library_models import GasControlValve,gas_control_valve

BASE=dict(temperature_k=330.,molar_mass_kg_mol=.028,viscosity_pa_s=1.6e-5,heat_capacity_ratio=1.4,
    compressibility=.95,inlet_pressure_pa_abs=8e5,outlet_pressure_pa_abs=4e5,standard_volume_flow_m3_s=.2,
    reference_temperature_k=273.15,reference_pressure_pa_abs=101325.,inlet_diameter_m=.1,
    outlet_diameter_m=.1,valve_diameter_m=.1,pressure_recovery_factor=.85,valve_style_factor=.7,
    terminal_pressure_ratio_factor=.5)


def calc(**changes):
    p=BASE|changes
    return gas_control_valve(GasControlValve(**p)).values,p


def reference(p):
    x=(p["inlet_pressure_pa_abs"]-p["outlet_pressure_pa_abs"])/p["inlet_pressure_pa_abs"]
    terminal=p["heat_capacity_ratio"]/1.4*p["terminal_pressure_ratio_factor"]
    used=min(x,terminal)
    expansion=1-used/(3*terminal)
    kv=p["standard_volume_flow_m3_s"]*3600/(24.6*(p["inlet_pressure_pa_abs"]/1000)*expansion)
    kv*=math.sqrt((p["molar_mass_kg_mol"]*1000)*p["temperature_k"]*p["compressibility"]/used)
    return kv,expansion,x>=terminal


@pytest.mark.parametrize("k",[1.2,1.4,1.6])
@pytest.mark.parametrize("ratio_to_terminal",[.5,1.1])
def test_equal_bore_modern_gas_capacity_matches_normal_flow_reference(k,ratio_to_terminal):
    terminal=k/1.4*BASE["terminal_pressure_ratio_factor"]
    p2=BASE["inlet_pressure_pa_abs"]*(1-terminal*ratio_to_terminal)
    out,p=calc(heat_capacity_ratio=k,outlet_pressure_pa_abs=p2)
    kv,y,choked=reference(p)
    assert out["kv"]==pytest.approx(kv,rel=4e-12)
    assert out["diagnostics"]["Y"]==pytest.approx(y,rel=3e-15)
    assert out["diagnostics"]["choked"] is choked
    assert out["diagnostics"]["laminar"] is False


def test_terminal_event_is_continuous_from_either_side():
    terminal=.5
    a,_=calc(outlet_pressure_pa_abs=BASE["inlet_pressure_pa_abs"]*(1-terminal*(1-1e-7)))
    b,_=calc(outlet_pressure_pa_abs=BASE["inlet_pressure_pa_abs"]*(1-terminal*(1+1e-7)))
    assert a["diagnostics"]["choked"] is False and b["diagnostics"]["choked"] is True
    assert a["kv"]==pytest.approx(b["kv"],rel=4e-12)


def test_flow_temperature_and_terminal_ratio_scalings_are_explicit():
    a,_=calc(outlet_pressure_pa_abs=.2*BASE["inlet_pressure_pa_abs"],terminal_pressure_ratio_factor=.1)
    b,_=calc(outlet_pressure_pa_abs=.2*BASE["inlet_pressure_pa_abs"],terminal_pressure_ratio_factor=.4)
    assert b["kv"]==pytest.approx(a["kv"]/2,rel=4e-12)
    c,_=calc(standard_volume_flow_m3_s=2*BASE["standard_volume_flow_m3_s"])
    d,_=calc(temperature_k=4*BASE["temperature_k"])
    base,_=calc()
    assert c["kv"]==pytest.approx(2*base["kv"],rel=4e-12)
    assert d["kv"]==pytest.approx(2*base["kv"],rel=4e-12)


def test_backpressure_below_terminal_limit_keeps_the_same_choked_capacity():
    a,_=calc(outlet_pressure_pa_abs=.3*BASE["inlet_pressure_pa_abs"])
    b,_=calc(outlet_pressure_pa_abs=.1*BASE["inlet_pressure_pa_abs"])
    assert a["kv"]==b["kv"]


def test_undeclared_normal_volume_reference_is_not_substituted():
    with pytest.raises(ValueError):GasControlValve(**(BASE|dict(reference_temperature_k=288.15)))


def test_non_directed_pressure_is_rejected():
    with pytest.raises(ValueError):GasControlValve(**(BASE|dict(outlet_pressure_pa_abs=BASE["inlet_pressure_pa_abs"])))
