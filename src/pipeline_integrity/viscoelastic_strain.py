"""Constant tensile-stress strain from elastic, delayed and viscous components."""
import math

from pydantic import Field

from .contracts import Calculation, Contract, Nonnegative, Positive


class ConstantStressStrain(Contract):
    tensile_stress_pa: Nonnegative
    instantaneous_modulus_pa: Positive
    delayed_modulus_pa: Positive
    retardation_time_s: Positive
    viscous_resistance_pa_s: Positive
    elapsed_times_s: tuple[Nonnegative, ...] = Field(min_length=1, max_length=100000)


def evaluate_constant_stress_strain(request: ConstantStressStrain) -> Calculation:
    """Evaluate one constant-load history without inferring material parameters.

    Retardation time is supplied directly. No ambiguous modulus-to-viscosity
    conversion or long-term material qualification is introduced.
    """
    q = ConstantStressStrain.model_validate(request.model_dump(mode="python"))
    immediate = q.tensile_stress_pa / q.instantaneous_modulus_pa
    delayed_limit = q.tensile_stress_pa / q.delayed_modulus_pa
    viscous_rate = q.tensile_stress_pa / q.viscous_resistance_pa_s
    delayed = [delayed_limit * -math.expm1(-t/q.retardation_time_s) for t in q.elapsed_times_s]
    viscous = [viscous_rate*t for t in q.elapsed_times_s]
    total = [math.fsum((immediate, a, b)) for a,b in zip(delayed,viscous)]
    if not all(math.isfinite(x) for x in (immediate,delayed_limit,viscous_rate,*delayed,*viscous,*total)):
        raise ValueError("strain response exceeds finite numerical support")
    return Calculation(values={"elapsed_times_s":list(q.elapsed_times_s),
        "instantaneous_strain":immediate,"delayed_strain":delayed,
        "viscous_strain":viscous,"total_strain":total,
        "long_time_viscous_strain_rate_per_s":viscous_rate}, notes=(
        "Constant tensile stress and caller-supplied linear constitutive parameters only.",
        "Elapsed-time ordering and repeats are retained; this evaluates coordinates, not a sampled load history.",
        "No variable loading, temperature dependence, nonlinear damage, rupture time or design life is inferred.",
    ))
