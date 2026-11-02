"""Local leak-position reductions and acoustic/cable input transformations.

These consume independently supplied discrepancies or identified arrivals. They
do not detect a leak, select signal events, estimate unknown interface locations
or replace a transient flow solution. Existing round-trip ranging is reused.
"""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field
from .contracts import Contract,Positive,Nonnegative,Fraction
from .quadrature import _product_ratio
from .sensing_location import TimeOfFlightRequest,locate_time_of_flight,SensingLocationResult


class DiscrepancyLocationRequest(Contract):
    upstream_discrepancy_m3_s: Nonnegative | None
    downstream_discrepancy_m3_s: Nonnegative | None
    section_length_m: Positive
    small_leak_fully_propagated_linear_resistance_reviewed: bool = False


class DiscrepancyLocationResult(Contract):
    status: Literal['computed','needs_data','unidentifiable']
    distance_from_upstream_m: Nonnegative | None = None
    fraction_from_upstream: Fraction | None = None
    inferred_loss_m3_s: Positive | None = None
    fraction_underflowed: bool = False
    issues: tuple[str,...] = ()


def locate_small_leak_from_discrepancies(q: DiscrepancyLocationRequest) -> DiscrepancyLocationResult:
    """Linear resistance split, positive inlet-minus-outlet discrepancies.

    The loss must be small relative to throughflow, with its influence already
    propagated to both ends. Discrepancy bias/noise and nonlinear large leaks
    violate this reduction; they are not fitted or clipped into the section.
    """
    q=DiscrepancyLocationRequest.model_validate(q.model_dump())
    issues=[]
    if not q.small_leak_fully_propagated_linear_resistance_reviewed:issues.append('small-leak and settled linear discrepancy assumptions require review')
    if q.upstream_discrepancy_m3_s is None or q.downstream_discrepancy_m3_s is None:issues.append('both end discrepancies required')
    if issues:return DiscrepancyLocationResult(status='needs_data',issues=tuple(issues))
    a=q.upstream_discrepancy_m3_s;b=q.downstream_discrepancy_m3_s
    if a==b==0:return DiscrepancyLocationResult(status='unidentifiable',issues=('zero discrepancy has no location information',))
    # Scale before summation to keep the ratio stable for extreme unequal inputs.
    scale=max(a,b);denominator=a/scale+b/scale
    fraction=(b/scale)/denominator;total=a+b
    if not math.isfinite(total):raise ValueError('combined loss is outside finite floating representation')
    # The dimensionless ratio can underflow although multiplying it by a long
    # section would give a representable distance. Preserve that distance by
    # balancing all binary exponents in one shared product/ratio operation.
    distance=_product_ratio((q.section_length_m,b),(scale,denominator))
    underflowed=b>0 and fraction==0
    return DiscrepancyLocationResult(status='computed',distance_from_upstream_m=distance,
        fraction_from_upstream=None if underflowed else fraction,inferred_loss_m3_s=total,
        fraction_underflowed=underflowed,issues=('nonzero fraction is below floating representation; dimensional distance retained',) if underflowed else ())


class ReflectionLocationRequest(Contract):
    sensor_to_interface_m: Positive | None
    direct_arrival_s: float
    reflected_arrival_s: float
    wave_speed_m_s: Positive | None
    known_constant_pressure_interface_and_uniform_speed_reviewed: bool = False


class ReflectionLocationResult(Contract):
    status: Literal['computed','needs_data','outside_sensor_interface_segment']
    distance_upstream_from_sensor_m: float | None = None
    interface_to_source_m: Nonnegative | None = None
    issues: tuple[str,...] = ()


def locate_from_known_interface_reflection(q: ReflectionLocationRequest) -> ReflectionLocationResult:
    """Use the delay between direct/reflected arrivals in one tight segment.

    The interface is a known constant-pressure reflector upstream of the source;
    the sensor is downstream. This does not propagate an acoustic wave through
    a slack/two-phase region, or infer a moving unknown phase interface.
    """
    q=ReflectionLocationRequest.model_validate(q.model_dump())
    issues=[]
    if not q.known_constant_pressure_interface_and_uniform_speed_reviewed:issues.append('known reflector and uniform tight-segment speed require review')
    if q.sensor_to_interface_m is None or q.wave_speed_m_s is None:issues.append('interface distance and wave speed required')
    if issues:return ReflectionLocationResult(status='needs_data',issues=tuple(issues))
    delay=q.reflected_arrival_s-q.direct_arrival_s
    if delay<0:raise ValueError('reflected arrival must not precede direct arrival')
    path=locate_time_of_flight(TimeOfFlightRequest(elapsed_time_s=delay,propagation_speed_m_s=q.wave_speed_m_s,timing_basis='round_trip'))
    interface_to_source=path.position_m;distance=q.sensor_to_interface_m-interface_to_source
    return ReflectionLocationResult(status='computed' if distance>=0 else 'outside_sensor_interface_segment',
        distance_upstream_from_sensor_m=distance,interface_to_source_m=interface_to_source)


class DielectricCableRangingRequest(Contract):
    relative_permittivity: float | None = Field(ge=1)
    elapsed_round_trip_s: Nonnegative | None
    fixed_latency_s: Nonnegative = 0.0
    cable_length_m: Positive | None = None
    nonmagnetic_nondispersive_dielectric_reviewed: bool = False


def locate_dielectric_cable(q: DielectricCableRangingRequest) -> SensingLocationResult:
    """Vacuum light speed divided by sqrt(relative permittivity), then range.

    Applicable to a declared nonmagnetic nondispersive transmission path. No
    attenuation, permeability correction, frequency dependence or fault-detection
    transfer function is supplied. The existing ranging API handles latency.
    """
    q=DielectricCableRangingRequest.model_validate(q.model_dump())
    if not q.nonmagnetic_nondispersive_dielectric_reviewed or q.relative_permittivity is None:
        return SensingLocationResult(method='time_of_flight',status='needs_data',position_m=None,within_declared_cable=None,
            missing_inputs=('reviewed dielectric relative permittivity required',))
    return locate_time_of_flight(TimeOfFlightRequest(elapsed_time_s=q.elapsed_round_trip_s,
        propagation_speed_m_s=299792458.0/math.sqrt(q.relative_permittivity),timing_basis='round_trip',
        fixed_latency_s=q.fixed_latency_s,cable_length_m=q.cable_length_m))


class DirectionalAcousticSignalRequest(Contract):
    pressure_change_pa: float
    volume_flow_change_m3_s: float
    density_kg_m3: Positive
    wave_speed_m_s: Positive
    flow_area_m2: Positive
    direction: Literal[-1,1]
    linear_single_phase_acoustic_impedance_reviewed: bool = False


class DirectionalAcousticSignalResult(Contract):
    status: Literal['computed','needs_data']
    combined_pressure_signal_pa: float | None = None
    pressure_equivalent_flow_signal_pa: float | None = None


def combine_directional_acoustic_signal(q: DirectionalAcousticSignalRequest) -> DirectionalAcousticSignalResult:
    """Form p-change plus signed acoustic-impedance flow-change.

    Rightward flow is positive. A right-running linear wave has dp=+rho*a*du;
    left-running has the opposite sign. Selecting direction +1 doubles a pure
    right-running pressure wave and cancels a left-running one. This algebraic
    projection is not a noise detector or a nonlinear wave simulation.
    """
    q=DirectionalAcousticSignalRequest.model_validate(q.model_dump())
    if not q.linear_single_phase_acoustic_impedance_reviewed:return DirectionalAcousticSignalResult(status='needs_data')
    if q.volume_flow_change_m3_s==0:
        equivalent=0.
    else:
        # Scale the product by binary exponents so a finite final signal does
        # not overflow at rho*c or underflow before division by area.
        factors=[math.frexp(value) for value in (q.density_kg_m3,q.wave_speed_m_s,
            abs(q.volume_flow_change_m3_s),q.flow_area_m2)]
        mantissa=factors[0][0]*factors[1][0]*factors[2][0]/factors[3][0]
        exponent=factors[0][1]+factors[1][1]+factors[2][1]-factors[3][1]
        try:
            equivalent=math.copysign(math.ldexp(mantissa,exponent),q.volume_flow_change_m3_s)*q.direction
        except OverflowError as error:
            raise ValueError('directional pressure projection exceeds finite support') from error
        if equivalent==0:
            raise ValueError('nonzero directional pressure projection is below finite support')
    try:
        combined=math.fsum((q.pressure_change_pa,equivalent))
    except OverflowError as error:
        raise ValueError('combined directional pressure exceeds finite support') from error
    return DirectionalAcousticSignalResult(status='computed',combined_pressure_signal_pa=combined,
        pressure_equivalent_flow_signal_pa=equivalent)
