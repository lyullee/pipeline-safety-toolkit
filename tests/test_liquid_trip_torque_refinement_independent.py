"""Physical torque residuals of the three originally withheld iterations."""
from pathlib import Path
import runpy
import pytest

F=runpy.run_path(str(Path(__file__).with_name('test_liquid_pressure_trip_independent.py')))

@pytest.mark.parametrize('direction,initial',[(1,False),(-1,False),(1,True)])
def test_actual_implicit_rotor_torque_balance_and_exact_speed(direction,initial):
    q=F['request'](dt=.00025 if not initial else .0005,direction=direction)
    if initial:q=q.model_copy(update={'absolute_trip_pressure_pa':101325+F['RHO']*F['G']*10})
    result=F['evaluate'](q,F['CTX']);assert result.state=='computed',result.reason
    times=result.operated_transient['time_s'];rows=result.operated_transient['pumps']['pump']
    event=result.trip_time_s
    for i in range(1,len(times)):
        dt=times[i]-times[i-1];mid=.5*(times[i]+times[i-1])
        drive=1. if mid<=event else max(1-(mid-event)/.002,0.)
        residual=F['INERTIA']*(rows[i]['speed_rad_s']-rows[i-1]['speed_rad_s'])/dt-drive+1.
        assert abs(residual)<=1e-9
        assert rows[i]['speed_rad_s']==pytest.approx(F['speed'](times[i],event),abs=2e-8)
        assert abs(rows[i]['energy_residual_J'])<=q.specification.pumps[0].energy_tolerance_J
    # Verify the actual numerical corrective path, not a disabled diagnostic.
    assert 'physical_residual_refinement' in str(result.operated_transient)
