"""Known solid-path reference through the existing native NDE adapter.

A reviewed reflector returns the incident ray along its path. Oblique cases
project that path to a surface-normal depth; they do not presume specular
backscatter from an oblique, parallel planar back wall or predict detectability.
"""
import math

import pytest
from scipy.integrate import solve_ivp

from test_ultrasonic_thickness_physical_qualification import make_inputs
from pipeline_integrity.nde import evaluate_echo_thickness


@pytest.mark.parametrize("depth,lateral,speed,time_bias",[
    (.0073,0.,5911.,0.),(.017,.009,3203.,.0000027),(.0039,.0071,5807.,.0000011),
    (.049,0.,6100.,.0000043),
])
def test_native_solid_echo_recovers_known_depth_from_independent_ray_flight(tmp_path,depth,lateral,speed,time_bias):
    distance=math.hypot(depth,lateral)
    def target(t,y):return y[0]-distance
    target.terminal=True
    outbound=solve_ivp(lambda t,y:(speed,),(0,3*distance/speed),(0.,),events=target,
        rtol=1e-12,atol=1e-14,max_step=distance/speed/20)
    reached=float(outbound.y_events[0][0,0])
    def origin(t,y):return y[0]
    origin.terminal=True
    origin.direction=-1
    returned=solve_ivp(lambda t,y:(-speed,),(0,3*distance/speed),(reached,),events=origin,
        rtol=1e-12,atol=1e-14,max_step=distance/speed/20)
    flight=float(outbound.t_events[0][0]+returned.t_events[0][0])
    angle=math.atan2(lateral,depth)
    start=.00031
    times=(start,start+(flight+time_bias)/2,start+flight+time_bias)
    trace,q,context=make_inputs(tmp_path,times=times,speed=speed,offset=time_bias,angle=angle)
    result=evaluate_echo_thickness(trace,q,context)
    assert result['status']=='exchange_checked'
    # The existing trace factory writes12 significant digits. Its independent
    # input-time quantization is bounded separately from physical depth truth.
    time_rounding=max(abs(actual-supplied) for actual,supplied in zip(trace.time_s,times))
    depth_budget=2*speed*time_rounding+2e-12
    assert abs(result['thickness_m']-depth)<depth_budget
    native=result['native_result']['payload']
    assert native['inputs']['sound_speed_m_s']==speed
    assert native['inputs']['incidence_angle_rad']==angle
    assert result['physical_echo_identity_automatically_verified'] is False
    assert result['field_calibrated'] is False


def test_equal_absolute_timestamp_shift_preserves_measured_solid_echo_interval(tmp_path):
    trace,q,context=make_inputs(tmp_path,times=(0.,.000013,.000026),offset=.000001)
    original=evaluate_echo_thickness(trace,q,context)
    trace,q,context=make_inputs(tmp_path,times=(.125,.125013,.125026),offset=.000001)
    shifted=evaluate_echo_thickness(trace,q,context)
    assert shifted['thickness_m']==pytest.approx(original['thickness_m'],abs=3e-14)
