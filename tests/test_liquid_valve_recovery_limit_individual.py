"""Explicit equal-bore recovery-pressure budget and choked-capacity references."""
import math
import pytest
from pipeline_integrity.models.library_models import LiquidControlValve,liquid_control_valve

RHO_REFERENCE=999.10329075702327
BASE=dict(density_kg_m3=850.,vapour_pressure_pa_abs=10000.,critical_pressure_pa_abs=4e6,
          viscosity_pa_s=.0002,inlet_pressure_pa_abs=1e6,outlet_pressure_pa_abs=2e5,volume_flow_m3_s=.02,
          inlet_diameter_m=.1,outlet_diameter_m=.1,valve_diameter_m=.1,
          pressure_recovery_factor=.7,valve_style_factor=.8)


def cap(fl):
    ff=.96-.28*math.sqrt(BASE["vapour_pressure_pa_abs"]/BASE["critical_pressure_pa_abs"])
    return fl*fl*(BASE["inlet_pressure_pa_abs"]-ff*BASE["vapour_pressure_pa_abs"])


def calc(fl,drop,**changes):
    return liquid_control_valve(LiquidControlValve(**(BASE|dict(pressure_recovery_factor=fl,
        outlet_pressure_pa_abs=BASE["inlet_pressure_pa_abs"]-drop)|changes))).values


@pytest.mark.parametrize("fl",[.5,.7,.9])
@pytest.mark.parametrize("drop_fraction",[.5,1.1])
def test_equal_bore_capacity_matches_explicit_available_pressure_budget(fl,drop_fraction):
    limit=cap(fl);drop=limit*drop_fraction
    out=calc(fl,drop)
    expected=BASE["volume_flow_m3_s"]*3600*math.sqrt(
        (BASE["density_kg_m3"]/RHO_REFERENCE)/(min(drop,limit)/1e5))
    assert out["kv"]==pytest.approx(expected,rel=3e-12)
    assert out["diagnostics"]["choked"] is (drop_fraction>=1.)
    assert out["diagnostics"]["laminar"] is False


def test_recovery_boundary_from_either_side_is_continuous_with_changed_choke_flag():
    fl=.65;limit=cap(fl)
    left,right=calc(fl,limit*(1-1e-7)),calc(fl,limit*(1+1e-7))
    assert left["diagnostics"]["choked"] is False
    assert right["diagnostics"]["choked"] is True
    assert left["kv"]==pytest.approx(right["kv"],rel=1e-7)


def test_decreasing_downstream_pressure_below_choke_does_not_raise_capacity():
    fl=.5;limit=cap(fl)
    a,b=calc(fl,1.2*limit),calc(fl,2.*limit)
    assert a["kv"]==b["kv"]


def test_choked_coefficient_scales_with_requested_flow_and_inverse_recovery_factor():
    a=calc(.5,1.1*cap(.5))
    b=calc(.5,1.1*cap(.5),volume_flow_m3_s=2*BASE["volume_flow_m3_s"])
    c=calc(.7,1.1*cap(.7))
    assert b["kv"]==pytest.approx(2*a["kv"],rel=3e-12)
    assert c["kv"]==pytest.approx(a["kv"]*.5/.7,rel=3e-12)


def test_inlet_at_or_below_supplied_vapour_pressure_is_rejected():
    with pytest.raises(ValueError):
        LiquidControlValve(**(BASE|dict(vapour_pressure_pa_abs=BASE["inlet_pressure_pa_abs"])))
