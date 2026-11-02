"""Fixed-geometry pump speed similarity and pressure-work references."""
import pytest

from pipeline_integrity.models.equipment import PumpAffinity, pump_affinity


@pytest.mark.parametrize("speed", [.4,.8,1.3,2.])
def test_speed_only_flow_head_and_power_are_coherent_with_pressure_work(speed):
    q0,h0,rho,g,eta=.03,42.,870.,9.80665,.78
    p0=q0*rho*g*h0/eta
    out=pump_affinity(PumpAffinity(reference_flow_m3_s=q0,reference_head_m=h0,
        reference_power_w=p0,speed_ratio=speed,geometric_diameter_ratio=1.)).values
    assert out["flow_m3_s"]==pytest.approx(q0*speed,rel=1e-14)
    assert out["head_m"]==pytest.approx(h0*speed*speed,rel=1e-14)
    expected_work=out["flow_m3_s"]*rho*g*out["head_m"]/eta
    assert out["power_w"]==pytest.approx(expected_work,rel=1e-14)
    assert out["power_w"]/p0==pytest.approx(speed**3,rel=1e-14)


def test_speed_transform_composition_and_inverse_preserve_reference_state():
    state=dict(reference_flow_m3_s=.04,reference_head_m=50.,reference_power_w=10000.)
    def scaled(s,n):
        return pump_affinity(PumpAffinity(**s,speed_ratio=n,geometric_diameter_ratio=1.)).values
    first=scaled(state,1.4)
    intermediate={"reference_flow_m3_s":first["flow_m3_s"],"reference_head_m":first["head_m"],
                  "reference_power_w":first["power_w"]}
    second=scaled(intermediate,.75)
    direct=scaled(state,1.4*.75)
    assert second==pytest.approx(direct,rel=1e-14)
    inverse=scaled(intermediate,1/1.4)
    assert inverse==pytest.approx(dict(flow_m3_s=state["reference_flow_m3_s"],head_m=state["reference_head_m"],
                                      power_w=state["reference_power_w"]),rel=1e-14)


def test_zero_speed_is_not_accepted_by_operating_similarity_relation():
    with pytest.raises(ValueError):
        PumpAffinity(reference_flow_m3_s=.04,reference_head_m=50.,reference_power_w=10000.,speed_ratio=0.)
