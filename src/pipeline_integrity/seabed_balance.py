"""Local static seabed force balance with caller-supplied loads per pipe length."""
import math

from .contracts import Calculation, Contract, Nonnegative


class SeabedContactBalance(Contract):
    submerged_weight_n_m: Nonnegative
    lift_upward_n_m: float
    drag_horizontal_n_m: float
    inertia_horizontal_n_m: float
    static_friction_coefficient: Nonnegative
    passive_lateral_resistance_n_m: Nonnegative = 0.0


def evaluate_seabed_contact_balance(request: SeabedContactBalance) -> Calculation:
    """Resolve supplied signed loads and the local Coulomb resistance margin.

    Upward lift reduces the normal reaction. Horizontal drag and inertia share
    one signed axis; their magnitudes are not added independently.
    """
    q = SeabedContactBalance.model_validate(request.model_dump(mode="python"))
    normal = q.submerged_weight_n_m - q.lift_upward_n_m
    horizontal = q.drag_horizontal_n_m + q.inertia_horizontal_n_m
    if not math.isfinite(normal) or not math.isfinite(horizontal):
        raise ValueError("force sum exceeds finite numerical support")
    values = {
        "effective_normal_load_n_m": normal,
        "total_horizontal_load_n_m": horizontal,
        "horizontal_load_magnitude_n_m": abs(horizontal),
    }
    if normal < 0:
        return Calculation(values=values, status="out_of_scope", notes=(
            "Supplied upward lift exceeds submerged weight; the assumed seabed contact is lost.",
            "No Coulomb sliding capacity is inferred after lift-off.",
        ))
    friction = q.static_friction_coefficient * normal
    resistance = friction + q.passive_lateral_resistance_n_m
    margin = resistance - abs(horizontal)
    if not all(math.isfinite(x) for x in (friction, resistance, margin)):
        raise ValueError("resistance exceeds finite numerical support")
    values.update(friction_capacity_n_m=friction,
                  total_lateral_resistance_n_m=resistance,
                  lateral_margin_n_m=margin)
    return Calculation(values=values, notes=(
        "A positive margin means supplied lateral resistance exceeds the instantaneous load magnitude.",
        "Zero margin is the sliding boundary; zero normal load is the contact-loss boundary.",
        "No wave/current loads, soil coefficients, safety factors, pipe motion or design approval are inferred.",
    ))
