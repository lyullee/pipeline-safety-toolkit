"""Independent IVP and closed-form references for one linear oscillator."""
import math

import numpy as np
import pytest
from scipy.integrate import solve_ivp

from pipeline_integrity.harmonic_response import HarmonicOscillator, frequency_response, sample_response


@pytest.mark.parametrize('frequency', [0.4, 2.0, 7.0])
def test_frequency_response_matches_separately_integrated_steady_oscillator(frequency):
    q = HarmonicOscillator(2.0, 8.0, 0.6, 3.0, frequency, 0.3)
    r = frequency_response(q)
    phase = q.force_phase_rad - r.phase_lag_rad
    x0, v0 = r.displacement_amplitude_m*math.cos(phase), -frequency*r.displacement_amplitude_m*math.sin(phase)
    times = np.linspace(0., 10*2*math.pi/frequency, 131)
    def derivative(t, y):
        return [y[1], (3*math.cos(frequency*t+0.3)-0.6*y[1]-8*y[0])/2]
    ivp = solve_ivp(derivative, (0., times[-1]), (x0, v0), t_eval=times, method='DOP853', rtol=2e-12, atol=2e-13)
    assert ivp.success
    assert ivp.y[0] == pytest.approx(r.displacement_amplitude_m*np.cos(frequency*times+phase), abs=2e-10)
    assert ivp.y[1] == pytest.approx(-frequency*r.displacement_amplitude_m*np.sin(frequency*times+phase), abs=3e-10)


@pytest.mark.parametrize('damping', [0., 0.5, 2., 4.])
def test_initial_value_matrix_response_matches_independent_adaptive_ivp(damping):
    q = HarmonicOscillator(1., 1., damping, 0.8, 1.3, -0.7)
    times = np.linspace(0., 7., 81)
    rows = sample_response(q, times, initial_displacement_m=0.4, initial_velocity_m_s=-0.2)
    def derivative(t, y):
        return [y[1], 0.8*math.cos(1.3*t-0.7)-damping*y[1]-y[0]]
    ivp = solve_ivp(derivative, (0., 7.), (0.4, -0.2), t_eval=times, method='DOP853', rtol=2e-12, atol=2e-13)
    assert ivp.success
    assert [r.displacement_m for r in rows] == pytest.approx(ivp.y[0], abs=4e-10)
    assert [r.velocity_m_s for r in rows] == pytest.approx(ivp.y[1], abs=4e-10)


def test_undamped_resonance_transient_has_secular_growth_but_no_periodic_solution():
    q = HarmonicOscillator(2., 8., 0., 3., 2.)
    times = np.linspace(0., 12., 53)
    rows = sample_response(q, times)
    assert [r.displacement_m for r in rows] == pytest.approx(3/8*times*np.sin(2*times), abs=3e-12)
    assert [r.velocity_m_s for r in rows] == pytest.approx(3/8*(np.sin(2*times)+2*times*np.cos(2*times)), abs=5e-12)
    with pytest.raises(ValueError, match='nonsingular'):
        frequency_response(q)


def test_critical_free_decay_matches_exact_repeated_root_solution():
    times = np.array([0., 0.1, 0.5, 1., 3.])
    rows = sample_response(HarmonicOscillator(1., 4., 4., 0., 0.), times, initial_displacement_m=1.)
    assert [r.displacement_m for r in rows] == pytest.approx((1+2*times)*np.exp(-2*times), abs=1e-13)


def test_static_resonance_and_high_frequency_limits_and_phase_quadrant():
    static = frequency_response(HarmonicOscillator(2., 8., 0.6, 3., 0.))
    assert static.displacement_amplitude_m == pytest.approx(3/8)
    assert static.dynamic_magnification == 1.
    resonance = frequency_response(HarmonicOscillator(2., 8., 0.6, 3., 2.))
    assert resonance.displacement_amplitude_m == pytest.approx(3/(0.6*2))
    assert resonance.phase_lag_rad == pytest.approx(math.pi/2)
    high = frequency_response(HarmonicOscillator(2., 8., 0.6, 3., 2e5))
    assert high.displacement_amplitude_m == pytest.approx(3/(2*(2e5)**2), rel=2e-10)
    assert high.phase_lag_rad == pytest.approx(math.pi, abs=2e-6)


def test_si_parameter_scaling_and_output_clock_do_not_change_trajectory():
    q = HarmonicOscillator(2., 8., 0.6, 3., 1.1)
    scaled = HarmonicOscillator(20., 80., 6., 30., 1.1)
    sparse = sample_response(q, [0., 0.5, 1., 2.])
    dense = sample_response(scaled, [0., 0.25, 0.5, 0.75, 1., 1.5, 2.])
    by_time = {row.time_s: row for row in dense}
    for row in sparse:
        assert row.displacement_m == pytest.approx(by_time[row.time_s].displacement_m, abs=1e-13)
        assert row.velocity_m_s == pytest.approx(by_time[row.time_s].velocity_m_s, abs=1e-13)


def test_unforced_undamped_oscillator_conserves_mechanical_energy():
    rows = sample_response(HarmonicOscillator(2., 8., 0., 0., 0.), np.linspace(0., 40., 101),
        initial_displacement_m=0.3, initial_velocity_m_s=-0.7)
    expected = 0.5*2*0.7**2 + 0.5*8*0.3**2
    assert [0.5*2*r.velocity_m_s**2+0.5*8*r.displacement_m**2 for r in rows] == pytest.approx(
        np.full(len(rows), expected), abs=3e-13)


def test_zero_frequency_constant_force_includes_declared_phase():
    q = HarmonicOscillator(2., 8., 3., 3., 0., math.pi/3)
    row = sample_response(q, [100.])[-1]
    assert row.applied_force_N == pytest.approx(1.5)
    assert row.displacement_m == pytest.approx(1.5/8, abs=2e-14)
    assert row.velocity_m_s == pytest.approx(0., abs=2e-14)


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), True, -1., 0.])
def test_invalid_mass_is_rejected(bad):
    with pytest.raises(ValueError):
        HarmonicOscillator(bad, 8., 0.6, 3., 1.)


@pytest.mark.parametrize('times', [[], [-1.], [0., 0.], [1., 0.], [float('nan')], [True]])
def test_invalid_time_coordinates_are_rejected(times):
    with pytest.raises(ValueError):
        sample_response(HarmonicOscillator(2., 8., 0.6, 3., 1.), times)
