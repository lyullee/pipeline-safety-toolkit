"""Resolve the original coarse-grid volume failures at fixed physical clocks."""
import math

import numpy as np
import pytest

from pipeline_integrity.liquid_virtual_mass import simulate_virtual_mass_liquid
from test_liquid_virtual_mass_independent import request,CONTEXT


@pytest.mark.parametrize("alpha",[1.0,1.21,1.69])
@pytest.mark.parametrize("duration_multiplier",[1.0,10.0])
def test_original_closure_and_longer_horizon_converge_without_relaxing_volume_tolerance(alpha,duration_multiplier):
    original=request(alpha)
    s=original.physical_specification
    # Restore the original withheld 34-step fixture, not the later two-step
    # first-front check. The closure history is identical at every resolution.
    duration=math.nextafter(34*s.requested_timestep_s,-math.inf)*duration_multiplier
    errors,solutions=[],[]
    for refinement in (1,2,4,8,16):
        physical=s.model_copy(update={"duration_s":duration,
            "requested_timestep_s":s.requested_timestep_s/refinement})
        assert physical.nodes==s.nodes
        assert physical.mass_balance_tolerance_m3==1e-8
        result=simulate_virtual_mass_liquid(original.model_copy(update={"physical_specification":physical}),CONTEXT)
        owner=result["owner_result"]
        assert owner["time_s"][-1]==pytest.approx(duration,rel=2e-14)
        error=max(owner["maximum_pipe_volume_residual_m3"],owner["volume_balance"]["maximum_absolute_residual_m3"])
        errors.append(error)
        if refinement==1:
            assert owner["status"]=="withheld"
            assert error>physical.mass_balance_tolerance_m3
            assert "volume balance" in " ".join(owner["notes"])
        else:
            assert owner["status"]=="computed",owner["notes"]
            assert owner["all_volume_balances_within_tolerance"]
            assert error<physical.mass_balance_tolerance_m3
        # Sum the characteristic equations over a trapezoid-weighted pipe.
        # Their continuity defect is -C*dx*r/2 * (q_start|q_start|-q_end|q_end|)
        # per step, not a physical mass source. This independently diagnoses
        # why the first-order grid must resolve the declared volume budget.
        pipe=s.pipes[0]
        area=math.pi*pipe.diameter_m**2/4
        dx=pipe.length_m/owner["grid"]["line"]["segments"]
        storage=s.gravity_m_s2*area/pipe.wave_speed_m_s**2
        resistance=pipe.darcy_friction_factor*dx/(2*s.gravity_m_s2*pipe.diameter_m*area**2)
        start=np.asarray(owner["pipes"]["line"]["start_flow_m3_s"])
        end=np.asarray(owner["pipes"]["line"]["end_flow_m3_s"])
        expected=np.r_[0.0,np.cumsum(-0.5*storage*dx*resistance*(start[:-1]*abs(start[:-1])-end[:-1]*abs(end[:-1])))]
        np.testing.assert_allclose(owner["volume_balance"]["residual_m3"],expected,rtol=0,atol=2e-14)
        solutions.append(result)
    assert all(fine<0.51*coarse for coarse,fine in zip(errors,errors[1:]))
    assert errors[-1]<7e-10
    # Compare pressure and flow on the finest common physical clock, including
    # the original closure knots; volume passing alone is insufficient.
    times=np.asarray(solutions[-1]["owner_result"]["time_s"])
    pressure,flow=[],[]
    for solution in solutions[-3:]:
        owner=solution["owner_result"]
        pressure.append(np.interp(times,owner["time_s"],solution["physical_pressure_traces"]["line"]["end_pressure_pa_abs"]))
        flow.append(np.interp(times,owner["time_s"],owner["pipes"]["line"]["start_flow_m3_s"]))
    dp=[float(np.max(abs(a-b))) for a,b in zip(pressure,pressure[1:])]
    dq=[float(np.max(abs(a-b))) for a,b in zip(flow,flow[1:])]
    assert dp[1]<0.65*dp[0]+1e-8
    assert dq[1]<0.65*dq[0]+1e-12
    assert dp[1]<1.0
    assert dq[1]<1e-7
