"""Small-angle suspended-body frequency and supplied spectral displacement."""
from .contracts import Contract,Positive,Nonnegative
from .harmonic_response import HarmonicOscillator,frequency_response

class PendulumSpectralRequest(Contract):
    suspension_length_m: Positive | None = None
    gravity_m_s2: Positive | None = None
    spectral_acceleration_at_natural_frequency_m_s2: Nonnegative | None = None
    supplied_spectral_displacement_multiplier: Positive | None = None
    maximum_reviewed_small_angle_rad: Positive | None = None
    spectrum_ordinate_frequency_damping_and_small_angle_reviewed: bool = False

def evaluate_pendulum_spectral_coordinate(request: PendulumSpectralRequest):
    """Reuse unit-mass oscillator compliance; no earthquake history is inferred.

    The supplied spectral ordinate must already match the returned frequency
    and the caller-reviewed damping basis. Its multiplier is explicit caller
    data, not a universal empirical default. Displacement is a linear tangential
    coordinate; contact, finite-angle geometry and overturning are excluded.
    """
    q=PendulumSpectralRequest.model_validate(request.model_dump())
    if not q.spectrum_ordinate_frequency_damping_and_small_angle_reviewed or any(v is None for k,v in q.model_dump().items() if k!='spectrum_ordinate_frequency_damping_and_small_angle_reviewed'):
        return dict(status='needs_data')
    response=frequency_response(HarmonicOscillator(mass_kg=1.,stiffness_N_m=q.gravity_m_s2/q.suspension_length_m,
        damping_N_s_m=0.,force_amplitude_N=q.supplied_spectral_displacement_multiplier*q.spectral_acceleration_at_natural_frequency_m_s2,
        angular_frequency_rad_s=0.))
    angle=response.displacement_amplitude_m/q.suspension_length_m
    return dict(status='computed' if angle<=q.maximum_reviewed_small_angle_rad else 'out_of_scope',
        natural_angular_frequency_rad_s=response.natural_angular_frequency_rad_s,natural_frequency_hz=response.natural_frequency_Hz,
        linear_spectral_displacement_m=response.displacement_amplitude_m,linear_angle_coordinate_rad=angle,
        actual_time_history_contact_or_overturning_calculated=False)
