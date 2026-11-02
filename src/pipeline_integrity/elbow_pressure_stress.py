"""An OD nominal hoop coordinate with an explicit curved-pipe correction."""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field,model_validator
from .contracts import Contract,Positive,Nonnegative
from .nominal_pipe_pressure import NominalHoopStressInput,nominal_hoop_stress_Pa


class ElbowPressureStressRequest(Contract):
    pressure_difference_pa: Nonnegative | None = None
    outside_diameter_m: Positive | None = None
    wall_m: Positive | None = None
    centreline_bend_radius_m: Positive | None = None
    circumferential_angles_rad: tuple[float,...] = Field(min_length=1)
    maximum_reviewed_wall_inside_radius_ratio: Positive | None = None
    uniform_wall_thin_curved_membrane_basis_reviewed: bool = False

    @model_validator(mode='after')
    def geometry(self):
        if self.outside_diameter_m is not None:
            if self.wall_m is not None and self.wall_m>=self.outside_diameter_m/2:
                raise ValueError('wall must leave a positive bore')
            if self.centreline_bend_radius_m is not None and self.centreline_bend_radius_m<=self.outside_diameter_m/2:
                raise ValueError('bend centreline radius must exceed outside cross-section radius')
        return self


class ElbowPressureStressResult(Contract):
    status: Literal['computed','needs_data','out_of_scope']
    nominal_hoop_stress_pa: tuple[Nonnegative,...] | None = None
    straight_od_nominal_hoop_pa: Nonnegative | None = None
    curvature_correction_factors: tuple[Positive,...] | None = None
    inside_radius_m: Positive | None = None
    intrados_nominal_hoop_pa: Nonnegative | None = None
    extrados_nominal_hoop_pa: Nonnegative | None = None
    issues: tuple[str,...] = ()
    stress_basis: Literal['OD_nominal_with_inside_radius_curvature_correction'] = 'OD_nominal_with_inside_radius_curvature_correction'
    angle_basis: Literal['side_zero_extrados_positive_pi_over_two_intrados_negative_pi_over_two'] = 'side_zero_extrados_positive_pi_over_two_intrados_negative_pi_over_two'
    actual_elbow_shell_stress_or_pressure_rating_qualified: Literal[False] = False


def evaluate_elbow_pressure_stress(request: ElbowPressureStressRequest) -> ElbowPressureStressResult:
    """Return a nominal curved-pipe field while preserving its hybrid convention.

    The straight stress uses outside diameter; the curvature multiplier uses
    inside cross-section radius. This finite-wall convention is an approximation,
    not an exact toroidal shell solution. Uniform thickness, membrane behaviour,
    geometry support and angular orientation must be reviewed by the caller.
    """
    if not isinstance(request,ElbowPressureStressRequest):raise TypeError('ElbowPressureStressRequest required')
    q=ElbowPressureStressRequest.model_validate(request.model_dump(mode='python'))
    missing=['missing '+k for k in ('pressure_difference_pa','outside_diameter_m','wall_m','centreline_bend_radius_m','maximum_reviewed_wall_inside_radius_ratio') if getattr(q,k) is None]
    if not q.uniform_wall_thin_curved_membrane_basis_reviewed:missing.append('uniform-wall curved membrane approximation requires review')
    if missing:return ElbowPressureStressResult(status='needs_data',issues=tuple(missing))
    inside=q.outside_diameter_m/2-q.wall_m
    if q.wall_m/inside>q.maximum_reviewed_wall_inside_radius_ratio:
        return ElbowPressureStressResult(status='out_of_scope',inside_radius_m=inside,issues=('wall/inside-radius ratio exceeds reviewed membrane support',))
    native=nominal_hoop_stress_Pa(NominalHoopStressInput(differential_pressure_Pa=q.pressure_difference_pa,
        outside_diameter_m=q.outside_diameter_m,wall_thickness_m=q.wall_m))
    radius=q.centreline_bend_radius_m
    factors=tuple(.5*(1+radius/(radius+inside*math.sin(theta))) for theta in q.circumferential_angles_rad)
    stresses=tuple(native*factor for factor in factors)
    intrados=native*.5*(1+radius/(radius-inside));extrados=native*.5*(1+radius/(radius+inside))
    if not all(math.isfinite(v) for v in (*stresses,intrados,extrados)):raise ValueError('nominal elbow stress exceeds finite support')
    return ElbowPressureStressResult(status='computed',nominal_hoop_stress_pa=stresses,straight_od_nominal_hoop_pa=native,
        curvature_correction_factors=factors,inside_radius_m=inside,intrados_nominal_hoop_pa=intrados,extrados_nominal_hoop_pa=extrados)
