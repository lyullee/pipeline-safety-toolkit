"""Reference liquid volume from a reviewed volumetric engineering-strain curve."""
from __future__ import annotations

import math
from typing import Literal
from .contracts import Contract, Nonnegative, Positive
from .thermal_expansion import ExpansionCurve,integrate_thermal_expansion


class LiquidReferenceVolumeRequest(Contract):
    observed_volume_m3: Nonnegative
    observed_temperature_K: Positive
    reference_temperature_K: Positive
    volumetric_expansion_curve: ExpansionCurve | None = None
    common_pressure_and_volumetric_engineering_basis_reviewed: bool = False


class LiquidReferenceVolumeResult(Contract):
    status: Literal["computed","needs_data","out_of_scope"]
    reference_volume_m3: Nonnegative | None = None
    volumetric_engineering_strain: float | None = None
    observed_minus_reference_volume_m3: float | None = None
    issues: tuple[str,...] = ()


def evaluate_liquid_reference_volume(request: LiquidReferenceVolumeRequest) -> LiquidReferenceVolumeResult:
    """Reuse the existing piecewise tangent-curve integral on a volume basis.

    The supplied coefficient is d(V/V_reference)/dT, not the axial thermal
    expansion coefficient and not d(log V)/dT. No factor of three or property
    conversion is inferred. Temperature support and the common pressure basis
    must be reviewed; contraction, phase change and real-fluid EOS are separate.
    """
    if not isinstance(request,LiquidReferenceVolumeRequest):raise TypeError("LiquidReferenceVolumeRequest required")
    q=LiquidReferenceVolumeRequest.model_validate(request.model_dump(mode="python"))
    issues=[]
    if q.volumetric_expansion_curve is None:issues.append("missing volumetric expansion curve")
    if not q.common_pressure_and_volumetric_engineering_basis_reviewed:
        issues.append("common pressure and volumetric engineering-strain coefficient basis require review")
    if q.volumetric_expansion_curve is not None and not q.volumetric_expansion_curve.tangent_small_engineering_strain_basis_reviewed:
        issues.append("supplied tangent engineering-strain coefficient basis requires review")
    if issues:return LiquidReferenceVolumeResult(status="needs_data",issues=tuple(issues))
    curve=q.volumetric_expansion_curve
    lo,hi=sorted((q.observed_temperature_K,q.reference_temperature_K))
    if lo<curve.knots[0].temperature_K or hi>curve.knots[-1].temperature_K:
        return LiquidReferenceVolumeResult(status="out_of_scope",issues=("temperature exceeds supplied expansion-curve support",))
    strain=integrate_thermal_expansion(curve,q.reference_temperature_K,q.observed_temperature_K)["free_engineering_strain"]
    factor=1+strain
    if factor<=0:
        return LiquidReferenceVolumeResult(status="out_of_scope",volumetric_engineering_strain=strain,
            issues=("expansion relation does not leave a positive volume ratio",))
    reference=q.observed_volume_m3/factor
    delta=q.observed_volume_m3-reference
    if not math.isfinite(reference) or not math.isfinite(delta):raise ValueError("reference volume exceeds finite support")
    return LiquidReferenceVolumeResult(status="computed",reference_volume_m3=reference,
        volumetric_engineering_strain=strain,observed_minus_reference_volume_m3=delta)
