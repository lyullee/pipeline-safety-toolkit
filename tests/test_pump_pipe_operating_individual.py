"""Independent pump/head-balance checks with flow-dependent pipe resistance."""
import math

import pytest
from scipy.optimize import brentq

from pipeline_integrity.models.hydraulics import LiquidHeadLoss
from pipeline_integrity.models.pump_curves import PumpPipeSystem,ReferencePumpCurve,pump_system

G=9.80665


def _request(*,series=1,parallel=1,static=12.0,quadratic=2e5,nu=1e-4,roughness=0.0,
             low_flow=0.0,length=1000.0):
    curve=ReferencePumpCurve(volume_flow_m3_s=(low_flow,0.002,0.004),
        head_m=(100-20000*low_flow,60.0,20.0),source_id="authored_linear_branch")
    segment=LiquidHeadLoss(diameter_m=0.08,length_m=length,roughness_m=roughness,
        volume_flow_m3_s=0.0,kinematic_viscosity_m2_s=nu,minor_loss_coefficient=1.5)
    return PumpPipeSystem(reference_curve=curve,static_head_m=static,resistance_head_s2_m5=quadratic,
        parallel_pumps=parallel,series_pumps=series,density_kg_m3=900.0,
        pump_efficiency=0.8,motor_efficiency=0.9,pipe_head_loss_templates=(segment,))


@pytest.mark.parametrize("series,parallel", [(1,1),(2,1),(1,2),(2,2)])
@pytest.mark.parametrize("static", [12.0,40.0])
def test_laminar_pipe_linear_pump_and_quadratic_minor_loss_exact_root(series,parallel,static):
    q=_request(series=series,parallel=parallel,static=static)
    segment=q.pipe_head_loss_templates[0]
    area=math.pi*segment.diameter_m**2/4
    linear=series*20000/parallel+128*segment.kinematic_viscosity_m2_s*segment.length_m/(math.pi*G*segment.diameter_m**4)
    quadratic=q.resistance_head_s2_m5+segment.minor_loss_coefficient/(2*G*area**2)
    available=series*100-static
    expected=2*available/(linear+math.sqrt(linear**2+4*quadratic*available))
    r=pump_system(q)
    assert r.status == "computed",r.notes
    v=r.values
    assert v["volume_flow_m3_s"] == pytest.approx(expected,rel=2e-12)
    assert v["flow_per_parallel_branch_m3_s"] == pytest.approx(expected/parallel)
    assert v["head_per_series_stage_m"] == pytest.approx(100-20000*expected/parallel)
    assert v["pipe_head_losses"][0]["values"]["reynolds"] < 2300
    pipe_head=v["pipe_head_losses"][0]["values"]["dissipative_head_loss_m"]
    assert v["total_head_m"] == pytest.approx(static+quadratic*expected**2+
        (linear-series*20000/parallel)*expected)
    assert v["total_head_m"] == pytest.approx(static+q.resistance_head_s2_m5*expected**2+pipe_head)
    assert v["electrical_power_w"]*0.8*0.9 == pytest.approx(900*G*expected*v["total_head_m"])


@pytest.mark.parametrize("nu", [1e-6,1e-14])
def test_turbulent_pipe_root_matches_independent_inverse_sqrt_friction_solution(nu):
    q=_request(nu=nu,roughness=0.0001,low_flow=0.0005,quadratic=0)
    segment=q.pipe_head_loss_templates[0]
    area=math.pi*segment.diameter_m**2/4
    def residual(flow):
        speed=flow/area
        reynolds=speed*segment.diameter_m/nu
        y=brentq(lambda a:a+2*math.log10(segment.roughness_m/(3.7*segment.diameter_m)+2.51*a/reynolds),1,40,xtol=1e-13)
        loss=(segment.length_m/(segment.diameter_m*y*y)+segment.minor_loss_coefficient)*speed**2/(2*G)
        return 100-20000*flow-q.static_head_m-loss
    expected=brentq(residual,0.0005,0.004,xtol=1e-15)
    r=pump_system(q)
    assert r.status == "computed",r.notes
    assert r.values["volume_flow_m3_s"] == pytest.approx(expected,rel=2e-11)
    assert r.values["pipe_head_losses"][0]["values"]["reynolds"] > 4000
    if nu == 1e-14:
        rough_limit=1/(-2*math.log10(segment.roughness_m/(3.7*segment.diameter_m)))**2
        got=r.values["pipe_head_losses"][0]["values"]["darcy_friction_factor"]
        assert got == pytest.approx(rough_limit,rel=1e-7)


def test_serial_pipe_partition_invariance_preserves_minor_loss_and_common_flow():
    q=_request()
    segment=q.pipe_head_loss_templates[0]
    split=tuple(segment.model_copy(update={"length_m":segment.length_m/4,
        "minor_loss_coefficient":segment.minor_loss_coefficient/4}) for _ in range(4))
    base=pump_system(q)
    divided=pump_system(q.model_copy(update={"pipe_head_loss_templates":split}))
    assert divided.values["volume_flow_m3_s"] == pytest.approx(base.values["volume_flow_m3_s"],rel=2e-13)
    assert sum(s["values"]["dissipative_head_loss_m"] for s in divided.values["pipe_head_losses"]) == pytest.approx(
        base.values["pipe_head_losses"][0]["values"]["dissipative_head_loss_m"])


@pytest.mark.parametrize("field,value",[("volume_flow_m3_s",0.001),("outlet_minus_inlet_elevation_m",1.0)])
def test_template_does_not_silently_override_flow_or_double_count_elevation(field,value):
    q=_request()
    with pytest.raises(ValueError,match="placeholder flow and zero elevation"):
        PumpPipeSystem.model_validate({**q.model_dump(),"pipe_head_loss_templates":(
            q.pipe_head_loss_templates[0].model_copy(update={field:value}),)})


def test_declared_transition_trial_is_withheld_and_not_replaced_by_fixed_resistance():
    # Re=2500 at the lower branch endpoint: the selected owner withholds it.
    nu=(0.0005/(math.pi*0.08**2/4))*0.08/2500
    q=_request(nu=nu,low_flow=0.0005)
    r=pump_system(q)
    assert r.status == "out_of_scope"
    assert not r.values
    assert "Transitional" in " ".join(r.notes)


def test_no_intersection_within_reviewed_pump_branch_is_not_extrapolated():
    r=pump_system(_request(static=500))
    assert r.status == "out_of_scope" and not r.values
