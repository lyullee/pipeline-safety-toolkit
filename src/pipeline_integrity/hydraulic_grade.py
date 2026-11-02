"""Constant-density hydraulic grade along an explicitly surveyed series path."""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive
from .models.hydraulics import LiquidHeadLoss, liquid_head_loss


class HazenWilliamsRequest(Contract):
    volume_flow_m3_s: float
    diameter_m: Positive
    length_m: Nonnegative
    coefficient_C: Positive
    si_prefactor: Positive = 10.67
    coefficient_calibration_id: Name
    water_and_coefficient_domain_reviewed: bool = False


def evaluate_hazen_williams(request: HazenWilliamsRequest):
    """Return signed head drop using the stated SI coefficient convention.

    C and the prefactor belong to the declared empirical calibration. This
    calculation does not turn C into roughness or a transferable liquid model.
    """
    q = HazenWilliamsRequest.model_validate(request.model_dump())
    if not q.water_and_coefficient_domain_reviewed:
        raise ValueError('reviewed water/coefficient domain required')
    try:
        loss = (0. if q.length_m == 0 or q.volume_flow_m3_s == 0 else
                q.si_prefactor*q.length_m*(abs(q.volume_flow_m3_s)/q.coefficient_C)**1.852/q.diameter_m**4.8704)
    except (OverflowError, ZeroDivisionError) as error:
        raise ValueError('head loss exceeds finite arithmetic') from error
    if not math.isfinite(loss):
        raise ValueError('head loss exceeds finite arithmetic')
    if loss==0 and q.length_m>0 and q.volume_flow_m3_s!=0:
        raise ValueError('nonzero head loss is below finite numerical support')
    return dict(head_loss_m=loss, signed_head_drop_m=math.copysign(loss, q.volume_flow_m3_s),
                coefficient_calibration_id=q.coefficient_calibration_id,
                empirical_accuracy_qualified=False)


class GradeStation(Contract):
    id: Name
    elevation_m: float
    flow_area_m2: Positive
    kinetic_energy_factor: Positive = 1
    minimum_allowed_pressure_pa_abs: Positive | None = None
    maximum_allowed_pressure_pa_abs: Positive | None = None

    @model_validator(mode='after')
    def pressure_criteria(self):
        if (self.minimum_allowed_pressure_pa_abs is not None
                and self.maximum_allowed_pressure_pa_abs is not None
                and self.minimum_allowed_pressure_pa_abs > self.maximum_allowed_pressure_pa_abs):
            raise ValueError('minimum pressure criterion exceeds maximum pressure criterion')
        return self


class GradeSegment(Contract):
    length_m: Nonnegative
    diameter_m: Positive
    roughness_m: Nonnegative = 0
    minor_loss_coefficient: Nonnegative = 0
    law: Literal['darcy', 'hazen_williams'] = 'darcy'
    hazen_coefficient_C: Positive | None = None
    hazen_calibration_id: Name | None = None
    hazen_si_prefactor: Positive = 10.67


class HydraulicGradeRequest(Contract):
    stations: tuple[GradeStation, ...] = Field(min_length=2)
    segments: tuple[GradeSegment, ...] = Field(min_length=1)
    volume_flow_m3_s: Nonnegative
    inlet_pressure_pa_abs: Positive
    density_kg_m3: Positive
    kinematic_viscosity_m2_s: Positive
    gravity_m_s2: Positive = 9.80665
    constant_density_single_phase_and_loss_domains_reviewed: bool = False

    @model_validator(mode='after')
    def topology(self):
        if len(self.segments) != len(self.stations)-1:
            raise ValueError('one segment between consecutive stations required')
        if len({s.id for s in self.stations}) != len(self.stations):
            raise ValueError('station IDs must be unique')
        for segment in self.segments:
            if segment.law == 'hazen_williams' and (segment.hazen_coefficient_C is None or segment.hazen_calibration_id is None):
                raise ValueError('Hazen segment requires a coefficient and calibration identity')
        return self


def evaluate_hydraulic_grade(request: HydraulicGradeRequest):
    """Integrate total head, recovering local absolute static pressure.

    Local K uses the actual segment diameter. Endpoint areas and kinetic-energy
    factors remain distinct; changing diameter is not disguised as static head.
    Pressure thresholds are caller criteria and are reported as signed margins.
    """
    q = HydraulicGradeRequest.model_validate(request.model_dump())
    if not q.constant_density_single_phase_and_loss_domains_reviewed:
        raise ValueError('reviewed constant-density phase and loss domains required')
    first = q.stations[0]
    velocity = q.volume_flow_m3_s/first.flow_area_m2
    total = q.inlet_pressure_pa_abs/(q.density_kg_m3*q.gravity_m_s2)+first.elevation_m+first.kinetic_energy_factor*velocity**2/(2*q.gravity_m_s2)
    initial = total
    cumulative = 0.
    rows = []
    for index, station in enumerate(q.stations):
        if index:
            s = q.segments[index-1]
            if s.law == 'darcy':
                loss = liquid_head_loss(LiquidHeadLoss(diameter_m=s.diameter_m, length_m=s.length_m,
                    roughness_m=s.roughness_m, volume_flow_m3_s=q.volume_flow_m3_s,
                    minor_loss_coefficient=s.minor_loss_coefficient,
                    kinematic_viscosity_m2_s=q.kinematic_viscosity_m2_s, gravity_m_s2=q.gravity_m_s2))
                if loss.status != 'computed':
                    raise ValueError('Darcy segment outside supported regime: '+str(loss.notes))
                drop = loss.values['dissipative_head_loss_m']
            else:
                drop = evaluate_hazen_williams(HazenWilliamsRequest(volume_flow_m3_s=q.volume_flow_m3_s,
                    diameter_m=s.diameter_m, length_m=s.length_m, coefficient_C=s.hazen_coefficient_C,
                    si_prefactor=s.hazen_si_prefactor, coefficient_calibration_id=s.hazen_calibration_id,
                    water_and_coefficient_domain_reviewed=True))['head_loss_m']
                local_speed = q.volume_flow_m3_s/(math.pi*s.diameter_m**2/4)
                drop += s.minor_loss_coefficient*local_speed**2/(2*q.gravity_m_s2)
            cumulative += drop
            total -= drop
        velocity = q.volume_flow_m3_s/station.flow_area_m2
        pressure_head = total-station.elevation_m-station.kinetic_energy_factor*velocity**2/(2*q.gravity_m_s2)
        pressure = pressure_head*q.density_kg_m3*q.gravity_m_s2
        if not math.isfinite(pressure) or pressure <= 0:
            raise ValueError('profile leaves positive absolute-pressure single-phase support')
        rows.append(dict(station_id=station.id, pressure_pa_abs=pressure, velocity_m_s=velocity,
            total_head_m=total, hydraulic_grade_m=pressure_head+station.elevation_m,
            minimum_pressure_margin_pa=None if station.minimum_allowed_pressure_pa_abs is None else pressure-station.minimum_allowed_pressure_pa_abs,
            maximum_pressure_margin_pa=None if station.maximum_allowed_pressure_pa_abs is None else station.maximum_allowed_pressure_pa_abs-pressure))
    return dict(stations=tuple(rows), cumulative_head_loss_m=cumulative,
        head_balance_residual_m=initial-total-cumulative,
        phase_and_criteria_calibration_qualified=False)
