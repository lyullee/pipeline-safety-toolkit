"""Smooth horizontal two-phase friction via an explicit named correlation backend."""
from __future__ import annotations
import math
from typing import Literal
from fluids.two_phase import Lockhart_Martinelli
from .contracts import Contract, Fraction, Name, Nonnegative, Positive


class HorizontalTwoPhaseFrictionRequest(Contract):
    total_mass_flow_kg_s: Nonnegative
    gas_mass_quality: Fraction
    liquid_density_kg_m3: Positive
    gas_density_kg_m3: Positive
    liquid_dynamic_viscosity_Pa_s: Positive
    gas_dynamic_viscosity_Pa_s: Positive
    internal_diameter_m: Positive
    length_m: Nonnegative
    transition_reynolds: Positive = 2000.
    phase_property_state_basis: Name
    common_state_and_horizontal_smooth_pipe_reviewed: bool = False


class HorizontalTwoPhaseFrictionResult(Contract):
    status: Literal["computed", "needs_data"]
    phase_property_state_basis: Name
    friction_pressure_drop_Pa: Nonnegative | None = None
    liquid_reynolds: Nonnegative | None = None
    gas_reynolds: Nonnegative | None = None
    liquid_regime: Literal["absent", "laminar", "turbulent"] | None = None
    gas_regime: Literal["absent", "laminar", "turbulent"] | None = None
    backend_method: Literal["Lockhart_Martinelli_Chisholm"] = "Lockhart_Martinelli_Chisholm"
    gravity_and_acceleration_included: Literal[False] = False
    issues: tuple[str, ...] = ()


def evaluate_horizontal_two_phase_friction(
    request: HorizontalTwoPhaseFrictionRequest,
) -> HorizontalTwoPhaseFrictionResult:
    """Return friction-only loss at supplied constant phase properties.

    Quality is gas mass fraction, not void fraction. Pressures, temperatures and
    phase equilibrium are represented by the caller's common property-state
    basis. The named smooth-pipe algebraic fit uses one declared transition for
    its four liquid/gas Reynolds regimes. It supplies no roughness, inclined
    holdup, acceleration, phase-change or time-resolved network calculation.
    """
    if not isinstance(request, HorizontalTwoPhaseFrictionRequest):
        raise TypeError("HorizontalTwoPhaseFrictionRequest required")
    q = HorizontalTwoPhaseFrictionRequest.model_validate(request.model_dump(mode="python"))
    if not q.common_state_and_horizontal_smooth_pipe_reviewed:
        return HorizontalTwoPhaseFrictionResult(status="needs_data",
            phase_property_state_basis=q.phase_property_state_basis,
            issues=("common phase state and horizontal smooth-pipe applicability require review",))
    liquid_mass_flow = q.total_mass_flow_kg_s*(1-q.gas_mass_quality)
    gas_mass_flow = q.total_mass_flow_kg_s*q.gas_mass_quality
    liquid_re = (4/math.pi)*(liquid_mass_flow/q.internal_diameter_m)/q.liquid_dynamic_viscosity_Pa_s
    gas_re = (4/math.pi)*(gas_mass_flow/q.internal_diameter_m)/q.gas_dynamic_viscosity_Pa_s
    if not all(math.isfinite(x) for x in (liquid_re, gas_re)):
        raise ValueError("phase Reynolds number exceeds finite numerical support")
    def regime(re, flow):
        return "absent" if flow == 0 else "laminar" if re < q.transition_reynolds else "turbulent"
    if q.total_mass_flow_kg_s == 0 or q.length_m == 0:
        pressure_drop = 0.
    else:
        kwargs=dict(m=q.total_mass_flow_kg_s, x=q.gas_mass_quality,
            rhol=q.liquid_density_kg_m3, rhog=q.gas_density_kg_m3,
            mul=q.liquid_dynamic_viscosity_Pa_s, mug=q.gas_dynamic_viscosity_Pa_s,
            D=q.internal_diameter_m, L=q.length_m, Re_c=q.transition_reynolds)
        if q.gas_mass_quality == 0:
            # The backend evaluates its gas friction term before the x=0 limit.
            # Its x=1 branch is the same surviving-phase friction calculation.
            kwargs.update(x=1., rhog=q.liquid_density_kg_m3, mug=q.liquid_dynamic_viscosity_Pa_s)
        try:
            pressure_drop = Lockhart_Martinelli(**kwargs)
        except (ZeroDivisionError, OverflowError) as exc:
            raise ValueError("two-phase friction exceeds numerical support") from exc
    if not math.isfinite(pressure_drop) or pressure_drop < 0:
        raise ValueError("two-phase friction result is not finite and nonnegative")
    return HorizontalTwoPhaseFrictionResult(status="computed",
        phase_property_state_basis=q.phase_property_state_basis,
        friction_pressure_drop_Pa=pressure_drop, liquid_reynolds=liquid_re, gas_reynolds=gas_re,
        liquid_regime=regime(liquid_re, liquid_mass_flow), gas_regime=regime(gas_re, gas_mass_flow))
