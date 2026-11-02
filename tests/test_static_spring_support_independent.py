"""Existing zero-frequency spring response under imposed cold/hot travel."""
import dataclasses
import pytest
from scipy.integrate import quad
from scipy.optimize import brentq
from pipeline_integrity.harmonic_response import HarmonicOscillator,frequency_response

@pytest.mark.parametrize('mass',[1.,20.,1000.])
@pytest.mark.parametrize('hot_force,stiffness,cold_minus_hot_compression',[(100.,200.,.1),(250.,1500.,-.05),(50.,1000.,0.)])
def test_reviewed_linear_spring_cold_hot_travel_force_and_elastic_work(mass,hot_force,stiffness,cold_minus_hot_compression):
    model=HarmonicOscillator(mass,stiffness,0.,hot_force,0.)
    hot=frequency_response(model).displacement_amplitude_m
    cold_position=hot+cold_minus_hot_compression
    assert cold_position>0
    def force_at(position):
        return brentq(lambda force:frequency_response(dataclasses.replace(model,force_amplitude_N=force)).displacement_amplitude_m-position,
            0.,max(hot_force,stiffness*cold_position)*2,xtol=1e-10)
    cold_force=force_at(cold_position)
    assert cold_force-hot_force==pytest.approx(stiffness*cold_minus_hot_compression,rel=1e-12,abs=1e-10)
    cold=frequency_response(dataclasses.replace(model,force_amplitude_N=cold_force))
    assert cold.displacement_amplitude_m-hot==pytest.approx(cold_minus_hot_compression,abs=1e-13)
    assert cold.velocity_amplitude_m_s==cold.acceleration_amplitude_m_s2==0
    work=quad(force_at,hot,cold_position,epsabs=1e-10,epsrel=1e-11)[0]
    energy_change=.5*stiffness*(cold_position**2-hot**2)
    assert work==pytest.approx(energy_change,rel=2e-12,abs=1e-12)
