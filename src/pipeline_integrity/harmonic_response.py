"""A linear mass, viscous damper and spring under a prescribed cosine force.

All parameters use SI. The model has one translational coordinate, constant
coefficients and no contact, plasticity, base excitation or pipe-network model.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from numbers import Real
from typing import Iterable


def _finite(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
        raise ValueError(f"{name} must be a finite real number")
    return float(value)


@dataclass(frozen=True)
class HarmonicOscillator:
    mass_kg: float
    stiffness_N_m: float
    damping_N_s_m: float
    force_amplitude_N: float
    angular_frequency_rad_s: float
    force_phase_rad: float = 0.0

    def __post_init__(self) -> None:
        for name, value in vars(self).items():
            _finite(name, value)
        if self.mass_kg <= 0 or self.stiffness_N_m <= 0:
            raise ValueError("mass and stiffness must be positive")
        if min(self.damping_N_s_m, self.force_amplitude_N, self.angular_frequency_rad_s) < 0:
            raise ValueError("damping, force amplitude and angular frequency must be nonnegative")


@dataclass(frozen=True)
class HarmonicResponse:
    natural_angular_frequency_rad_s: float
    natural_frequency_Hz: float
    damping_ratio: float
    displacement_amplitude_m: float
    velocity_amplitude_m_s: float
    acceleration_amplitude_m_s2: float
    phase_lag_rad: float
    dynamic_magnification: float


@dataclass(frozen=True)
class ResponseSample:
    time_s: float
    displacement_m: float
    velocity_m_s: float
    acceleration_m_s2: float
    applied_force_N: float


def frequency_response(model: HarmonicOscillator) -> HarmonicResponse:
    """Return the finite periodic particular solution; lag is relative to force.

    At zero frequency the amplitude denotes the constant-force compliance,
    before multiplying by the declared force phase. Undamped exact resonance
    has no finite periodic transfer function and raises ValueError.
    """
    if not isinstance(model, HarmonicOscillator):
        raise TypeError("HarmonicOscillator required")
    m, k, c, w = model.mass_kg, model.stiffness_N_m, model.damping_N_s_m, model.angular_frequency_rad_s
    natural = math.sqrt(k) / math.sqrt(m)
    real, imaginary = k - m * w * w, c * w
    impedance = math.hypot(real, imaginary)
    if not all(math.isfinite(x) for x in (natural, real, imaginary, impedance)) or impedance == 0:
        raise ValueError("finite nonsingular frequency response required")
    amplitude = model.force_amplitude_N / impedance
    result = HarmonicResponse(natural, natural / (2 * math.pi), c / (2 * math.sqrt(m) * math.sqrt(k)),
        amplitude, amplitude * w, amplitude * w * w, math.atan2(imaginary, real), k / impedance)
    if not all(math.isfinite(x) for x in vars(result).values()):
        raise ValueError("frequency response exceeds finite numerical support")
    return result


def sample_response(
    model: HarmonicOscillator,
    times_s: Iterable[float],
    *,
    initial_displacement_m: float = 0.0,
    initial_velocity_m_s: float = 0.0,
) -> tuple[ResponseSample, ...]:
    """Evaluate the initial-value response, including all damping regimes.

    A four-state matrix exponential advances displacement, velocity and two
    forcing coordinates from time zero. Free undamped motion uses its exact
    trigonometric transition to avoid repeated-squaring energy drift. The
    forced state formulation also handles undamped resonance
    without assuming a finite steady response. Output times do not set a solver
    integration step. Initial conditions always refer to time zero.
    """
    import numpy as np
    from scipy.linalg import expm

    if not isinstance(model, HarmonicOscillator):
        raise TypeError("HarmonicOscillator required")
    times = tuple(_finite("time_s", t) for t in times_s)
    if not times or len(times) > 10000 or times[0] < 0 or any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("one to ten thousand strictly increasing nonnegative times required")
    x0 = _finite("initial_displacement_m", initial_displacement_m)
    v0 = _finite("initial_velocity_m_s", initial_velocity_m_s)
    m, k, c, f, w = model.mass_kg, model.stiffness_N_m, model.damping_N_s_m, model.force_amplitude_N, model.angular_frequency_rad_s
    if f == 0 and c == 0:
        natural = math.sqrt(k) / math.sqrt(m)
        if not math.isfinite(natural) or natural <= 0:
            raise ValueError("natural frequency exceeds finite numerical support")
        output = []
        for t in times:
            angle = natural*t
            if not math.isfinite(angle):
                raise ValueError("oscillator phase exceeds finite numerical support")
            sine, cosine = math.sin(angle), math.cos(angle)
            x = x0*cosine + (v0/natural)*sine
            v = v0*cosine - natural*x0*sine
            row = ResponseSample(t, x, v, -(k/m)*x, 0.)
            if not all(math.isfinite(value) for value in vars(row).values()):
                raise ValueError("response exceeds finite numerical support")
            output.append(row)
        return tuple(output)
    matrix = np.array([[0., 1., 0., 0.], [-k/m, -c/m, f/m, 0.], [0., 0., 0., -w], [0., 0., w, 0.]])
    if not np.isfinite(matrix).all():
        raise ValueError("state coefficients exceed finite numerical support")
    initial = np.array([x0, v0, math.cos(model.force_phase_rad), math.sin(model.force_phase_rad)])
    output = []
    for t in times:
        scaled = matrix * t
        if not np.isfinite(scaled).all():
            raise ValueError("time-scaled state coefficients exceed finite numerical support")
        state = expm(scaled) @ initial
        force = f * math.cos(w*t + model.force_phase_rad)
        acceleration = (force - c*state[1] - k*state[0]) / m
        row = ResponseSample(t, float(state[0]), float(state[1]), float(acceleration), force)
        if not all(math.isfinite(value) for value in vars(row).values()):
            raise ValueError("response exceeds finite numerical support")
        output.append(row)
    return tuple(output)
