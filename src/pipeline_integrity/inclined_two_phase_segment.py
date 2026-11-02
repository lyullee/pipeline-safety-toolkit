"""Local inclined two-phase segment using a declared empirical holdup model."""
from __future__ import annotations
import math
from typing import Annotated, Literal
from pydantic import Field
from fluids.two_phase import Beggs_Brill, _Beggs_Brill_holdup
from .contracts import Contract, Fraction, Name, Nonnegative, Positive


class InclinedTwoPhaseSegmentRequest(Contract):
    total_mass_flow_kg_s: Positive
    gas_mass_quality: Annotated[float, Field(gt=0, lt=1)]
    liquid_density_kg_m3: Positive
    gas_density_kg_m3: Positive
    liquid_dynamic_viscosity_Pa_s: Positive
    gas_dynamic_viscosity_Pa_s: Positive
    surface_tension_N_m: Positive
    internal_diameter_m: Positive
    length_m: Nonnegative
    inclination_deg: Annotated[float, Field(ge=-90, le=90)]
    local_absolute_pressure_Pa: Positive
    gravity_m_s2: Positive = 9.80665
    max_abs_pressure_change_fraction: Fraction = .1
    phase_property_state_basis: Name
    local_constant_state_smooth_pipe_and_holdup_model_reviewed: bool = False


class InclinedTwoPhaseSegmentResult(Contract):
    status: Literal["computed", "needs_data"]
    phase_property_state_basis: Name
    signed_pressure_drop_Pa: float | None = None
    estimated_outlet_absolute_pressure_Pa: Positive | None = None
    liquid_holdup: Fraction | None = None
    gravity_pressure_drop_Pa: float | None = None
    friction_pressure_drop_Pa: Nonnegative | None = None
    model: Literal["Beggs_Brill_local_friction_and_gravity"] = "Beggs_Brill_local_friction_and_gravity"
    acceleration_included: Literal[False] = False
    issues: tuple[str, ...] = ()


def _native_holdup(q):
    """Expose the open-source owner's phase-storage closure alongside its loss.

    Dimensionless flow-map boundaries are evaluated here; inclination-dependent
    holdup itself delegates to fluids' existing constitutive helper. This is the
    same local, smooth-pipe correlation as the pressure calculation.
    """
    area = math.pi * q.internal_diameter_m**2 / 4
    gas_speed = q.total_mass_flow_kg_s * q.gas_mass_quality / (q.gas_density_kg_m3 * area)
    liquid_speed = q.total_mass_flow_kg_s * (1 - q.gas_mass_quality) / (q.liquid_density_kg_m3 * area)
    speed = gas_speed + liquid_speed
    fraction = liquid_speed / speed
    froude = speed**2 / (q.gravity_m_s2 * q.internal_diameter_m)
    velocity_number = liquid_speed * (q.liquid_density_kg_m3 / (q.gravity_m_s2 * q.surface_tension_N_m))**.25
    thresholds = tuple(a * fraction**b for a, b in (
        (316., .302), (.0009252, -2.4684), (.1, -1.4516), (.5, -6.738)))
    a, b, c, d = thresholds
    def branch(index):
        return _Beggs_Brill_holdup(index, fraction, froude, math.radians(q.inclination_deg), velocity_number)
    if froude < (a if fraction < .01 else b):
        result = branch(0)
    elif fraction >= .01 and b <= froude <= c:
        weight = (c - froude) / (c - b)
        result = weight * branch(0) + (1 - weight) * branch(2)
    elif fraction >= .01 and c < froude <= (a if fraction < .4 else d):
        result = branch(2)
    elif (froude >= a if fraction < .4 else froude > d):
        result = branch(3)
    else:
        raise ValueError('phase flow is outside the native correlation regime map')
    if not math.isfinite(result) or not 0 < result <= 1:
        raise ValueError('native liquid holdup is outside physical support')
    return result


def evaluate_inclined_two_phase_segment(q: InclinedTwoPhaseSegmentRequest) -> InclinedTwoPhaseSegmentResult:
    """Estimate local friction plus gravity at caller-supplied phase properties.

    Positive inclination rises in the declared flow direction; pressure drop is
    signed and may be negative downhill. Actual segment geometry, surface tension
    and local gas mass quality are required. Average hill count/height cannot be
    converted into these inputs without additional geometry. This empirical
    holdup model is distinct from a horizontal friction multiplier and from a
    whole network solution. Its original acceleration correction is disabled.
    """
    if not isinstance(q, InclinedTwoPhaseSegmentRequest):
        raise TypeError("InclinedTwoPhaseSegmentRequest required")
    q = InclinedTwoPhaseSegmentRequest.model_validate(q.model_dump(mode="python"))
    if not q.local_constant_state_smooth_pipe_and_holdup_model_reviewed:
        return InclinedTwoPhaseSegmentResult(status="needs_data",phase_property_state_basis=q.phase_property_state_basis,
            issues=("local constant phase state, smooth geometry and holdup correlation applicability require review",))
    if q.length_m == 0:
        drop = 0.
    else:
        try:
            drop = Beggs_Brill(m=q.total_mass_flow_kg_s,x=q.gas_mass_quality,
                rhol=q.liquid_density_kg_m3,rhog=q.gas_density_kg_m3,
                mul=q.liquid_dynamic_viscosity_Pa_s,mug=q.gas_dynamic_viscosity_Pa_s,
                sigma=q.surface_tension_N_m,P=q.local_absolute_pressure_Pa,D=q.internal_diameter_m,
                angle=q.inclination_deg,L=q.length_m,g=q.gravity_m_s2,roughness=0.,acceleration=False)
        except (ZeroDivisionError,OverflowError) as exc:
            raise ValueError("inclined segment exceeds numerical support") from exc
    if not math.isfinite(drop):
        raise ValueError("inclined segment pressure result is nonfinite")
    holdup = _native_holdup(q)
    storage_density = holdup * q.liquid_density_kg_m3 + (1 - holdup) * q.gas_density_kg_m3
    gravity = storage_density * q.gravity_m_s2 * q.length_m * math.sin(math.radians(q.inclination_deg))
    friction = drop - gravity
    if friction < 0 or not math.isfinite(gravity):
        raise ValueError('phase pressure components exceed finite dissipative support')
    outlet = q.local_absolute_pressure_Pa - drop
    if not math.isfinite(outlet):
        raise ValueError("outlet pressure exceeds numerical support")
    if outlet <= 0 or abs(drop)/q.local_absolute_pressure_Pa > q.max_abs_pressure_change_fraction:
        return InclinedTwoPhaseSegmentResult(status="needs_data",phase_property_state_basis=q.phase_property_state_basis,
            issues=("pressure change exceeds caller local-state budget; subdivide and update phase states",))
    return InclinedTwoPhaseSegmentResult(status="computed",phase_property_state_basis=q.phase_property_state_basis,
        signed_pressure_drop_Pa=drop,estimated_outlet_absolute_pressure_Pa=outlet,
        liquid_holdup=holdup, gravity_pressure_drop_Pa=gravity, friction_pressure_drop_Pa=friction)
