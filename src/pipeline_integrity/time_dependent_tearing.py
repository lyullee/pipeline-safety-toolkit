"""Irreversible tearing under a reviewed pressure-history driving-force map.

Material resistance, pressure/depth driving force and kinetic parameters are
supplied. A two-threshold pressure-memory closure rule handles load reversals.
This conditional model does not fit material data or infer residual stresses.
"""
from __future__ import annotations

import math

import numpy as np
from pydantic import Field, model_validator

from .contracts import Contract, Name, Nonnegative, Positive, digest


class PressureHistoryTearingRequest(Contract):
    basis_id: Name
    material_condition_id: Name
    flaw_family_id: Name
    calibration_evidence_id: Name
    time_s: tuple[Nonnegative, ...] = Field(min_length=2)
    differential_pressure_pa: tuple[Nonnegative, ...] = Field(min_length=2)
    depth_grid_m: tuple[Positive, ...] = Field(min_length=2)
    pressure_grid_pa: tuple[Nonnegative, ...] = Field(min_length=2)
    supplied_driving_j_per_m2: tuple[tuple[Nonnegative, ...], ...]
    supplied_resistance_j_per_m2: tuple[Positive, ...]
    supplied_projected_crack_area_m2: tuple[Nonnegative, ...]
    initial_depth_m: Positive
    rupture_depth_m: Positive
    wall_thickness_m: Positive
    initial_peak_pressure_pa: Nonnegative
    initially_open: bool
    closing_peak_pressure_fraction: float = Field(ge=0, le=1)
    reopening_peak_pressure_fraction: float = Field(gt=0, le=1)
    supplied_rate_coefficient_m_s: Positive
    supplied_rate_exponent: Positive
    maximum_reviewed_driving_to_resistance_ratio: float = Field(gt=1)
    temperature_k: Positive
    minimum_calibrated_temperature_k: Positive
    maximum_calibrated_temperature_k: Positive
    maximum_step_s: Positive
    relative_tolerance: float = Field(default=1e-7, gt=0, le=.01)
    absolute_depth_tolerance_m: Positive = 1e-10
    absolute_area_tolerance_m2: Positive = 1e-12
    absolute_work_tolerance_j: Positive = 1e-8
    pressure_map_resistance_kinetics_closure_and_area_basis_reviewed: bool = False

    @model_validator(mode='after')
    def domains(self):
        for name in ('time_s', 'depth_grid_m', 'pressure_grid_pa'):
            values = getattr(self, name)
            if any(b <= a for a, b in zip(values, values[1:])):
                raise ValueError(name + ' must strictly increase without sorting')
        if len(self.time_s) != len(self.differential_pressure_pa):
            raise ValueError('one differential pressure is required at each history time')
        if not self.depth_grid_m[0] <= self.initial_depth_m <= self.rupture_depth_m <= self.depth_grid_m[-1] < self.wall_thickness_m:
            raise ValueError('initial, rupture and table depths must stay in ordered surface-flaw support')
        if any(not self.pressure_grid_pa[0] <= p <= self.pressure_grid_pa[-1] for p in self.differential_pressure_pa):
            raise ValueError('pressure history exceeds supplied driving-map support')
        n = len(self.depth_grid_m)
        if (len(self.supplied_driving_j_per_m2) != n
                or any(len(row) != len(self.pressure_grid_pa) for row in self.supplied_driving_j_per_m2)
                or len(self.supplied_resistance_j_per_m2) != n
                or len(self.supplied_projected_crack_area_m2) != n):
            raise ValueError('driving map is depth-by-pressure; resistance and area use the same depth knots')
        if any(b <= a for a, b in zip(self.supplied_projected_crack_area_m2, self.supplied_projected_crack_area_m2[1:])):
            raise ValueError('projected crack area must strictly increase with depth')
        if self.initial_peak_pressure_pa < self.differential_pressure_pa[0]:
            raise ValueError('peak-pressure memory cannot precede the initial pressure')
        if self.closing_peak_pressure_fraction >= self.reopening_peak_pressure_fraction:
            raise ValueError('closing threshold must be below reopening threshold')
        p0 = self.differential_pressure_pa[0]
        if self.initially_open and p0 < self.closing_peak_pressure_fraction*self.initial_peak_pressure_pa:
            raise ValueError('initially open state contradicts the closing threshold')
        if not self.initially_open and p0 > self.reopening_peak_pressure_fraction*self.initial_peak_pressure_pa:
            raise ValueError('initially closed state contradicts the reopening threshold')
        if not self.minimum_calibrated_temperature_k <= self.temperature_k <= self.maximum_calibrated_temperature_k:
            raise ValueError('temperature lies outside the supplied material calibration')
        return self


def evaluate_pressure_history_tearing(request: PressureHistoryTearingRequest):
    """Integrate da/dt=C*max(J/R-1,0)**n with irreversible depth and events.

    Driving work and resistance work use the same projected crack-area measure.
    Their difference is an overstress-work coordinate, not component kinetic
    energy. Pressure-memory closure switches exactly on the prescribed linear
    history; no step spans a closure/reopening discontinuity. Trial ODE stages
    beyond rupture or the calibrated driving-ratio boundary use frozen endpoint
    rates solely for root location. Returned physical trajectories end at the
    first such event; table extrapolation is never used.
    """
    q = PressureHistoryTearingRequest.model_validate(request.model_dump())
    base = dict(request_digest=digest(q), basis_id=q.basis_id,
                material_condition_id=q.material_condition_id, flaw_family_id=q.flaw_family_id,
                calibration_evidence_id=q.calibration_evidence_id,
                material_fit_or_field_qualification=False,
                residual_stress_or_plastic_closure_predicted=False)
    if not q.pressure_map_resistance_kinetics_closure_and_area_basis_reviewed:
        return dict(status='needs_data', **base)
    from scipy.integrate import solve_ivp

    depths = np.asarray(q.depth_grid_m)
    pressures = np.asarray(q.pressure_grid_pa)
    driving = np.asarray(q.supplied_driving_j_per_m2)
    resistance = np.asarray(q.supplied_resistance_j_per_m2)
    areas = np.asarray(q.supplied_projected_crack_area_m2)
    slopes = np.diff(areas)/np.diff(depths)
    state = np.array([q.initial_depth_m, 0., 0., 0., 0.])
    opened = q.initially_open
    peak = q.initial_peak_pressure_pa
    observations = []
    switches = []
    current_time = q.time_s[0]
    status = 'completed_history'

    def constitutive(a, p, is_open):
        # Endpoint extension applies only to solver root-location trials.
        supported_depth = min(q.rupture_depth_m, max(q.initial_depth_m, float(a)))
        i = min(len(depths)-2, max(0, int(np.searchsorted(depths, supported_depth, side='right'))-1))
        fraction = (supported_depth-depths[i])/(depths[i+1]-depths[i])
        j0 = float(np.interp(p, pressures, driving[i]))
        j1 = float(np.interp(p, pressures, driving[i+1]))
        j = (j0+(j1-j0)*fraction) if is_open else 0.
        r = float(np.interp(supported_depth, depths, resistance))
        return j, r, float(slopes[i])

    def append_observation(t, y, pressure, is_open, memory):
        j, r, _ = constitutive(y[0], pressure, is_open)
        observations.append(dict(time_s=float(t), depth_m=float(y[0]), pressure_pa=float(pressure),
            open=is_open, peak_pressure_memory_pa=float(memory), driving_j_per_m2=j,
            resistance_j_per_m2=r, new_projected_crack_area_m2=float(y[1]),
            supplied_driving_work_j=float(y[2]), resistance_work_j=float(y[3]),
            overstress_work_j=float(y[4])))

    append_observation(current_time, state, q.differential_pressure_pa[0], opened, peak)
    if q.initial_depth_m == q.rupture_depth_m:
        status = 'rupture_depth_reached'
    for index in range(0 if status == 'rupture_depth_reached' else len(q.time_s)-1):
        t0, t1 = q.time_s[index:index+2]
        p0, p1 = q.differential_pressure_pa[index:index+2]
        slope = (p1-p0)/(t1-t0)
        points = [t0]
        switch_time = None
        if opened and p1 < p0:
            threshold = q.closing_peak_pressure_fraction*peak
            if p1 <= threshold <= p0:
                switch_time = t0+(threshold-p0)/slope
        elif not opened and p1 > p0:
            threshold = q.reopening_peak_pressure_fraction*peak
            if p0 <= threshold <= p1:
                switch_time = t0+(threshold-p0)/slope
        if switch_time is not None:
            points.append(switch_time)
        points.append(t1)
        switched = False
        for segment in range(len(points)-1):
            begin, end = points[segment:segment+2]
            if end > begin:
                def pressure_at(t):
                    return p0+slope*(t-t0)

                def rhs(t, y):
                    j, r, area_slope = constitutive(y[0], pressure_at(t), opened)
                    ratio = min(j/r, q.maximum_reviewed_driving_to_resistance_ratio)
                    rate = q.supplied_rate_coefficient_m_s*max(ratio-1., 0.)**q.supplied_rate_exponent
                    area_rate = area_slope*rate
                    return [rate, area_rate, j*area_rate, r*area_rate, (j-r)*area_rate]

                def rupture_event(t, y):
                    return q.rupture_depth_m-y[0]

                def support_event(t, y):
                    j, r, _ = constitutive(y[0], pressure_at(t), opened)
                    # Permit an exactly constant calibrated endpoint. The tiny
                    # root-location allowance is not new constitutive support.
                    limit = q.maximum_reviewed_driving_to_resistance_ratio
                    return limit + max(1e-12, 1e-12*limit)-j/r

                rupture_event.terminal = True
                rupture_event.direction = -1
                support_event.terminal = True
                support_event.direction = -1
                if support_event(begin, state) < 0:
                    status = 'outside_calibrated_driving_ratio'
                    current_time = begin
                    break
                result = solve_ivp(rhs, (begin,end), state, method='DOP853',
                    rtol=q.relative_tolerance,
                    atol=[q.absolute_depth_tolerance_m,q.absolute_area_tolerance_m2,
                          q.absolute_work_tolerance_j,q.absolute_work_tolerance_j,q.absolute_work_tolerance_j],
                    max_step=q.maximum_step_s, events=(rupture_event,support_event))
                if not result.success:
                    raise RuntimeError('tearing integration did not converge: '+result.message)
                previous_depth = state[0]
                for column in range(1,len(result.t)):
                    y = result.y[:,column]
                    if not np.all(np.isfinite(y)) or y[0] < previous_depth-q.absolute_depth_tolerance_m:
                        raise RuntimeError('tearing integration violated irreversible finite-depth support')
                    append_observation(result.t[column],y,pressure_at(result.t[column]),opened,
                                       max(peak,pressure_at(result.t[column])))
                    previous_depth = y[0]
                state = result.y[:,-1]
                current_time = float(result.t[-1])
                if result.t_events[0].size:
                    status = 'rupture_depth_reached'
                    break
                if result.t_events[1].size:
                    status = 'outside_calibrated_driving_ratio'
                    break
            if switch_time is not None and end == switch_time and not switched:
                opened = not opened
                switched = True
                switches.append(dict(time_s=end,open=opened,pressure_pa=p0+slope*(end-t0),
                                     peak_pressure_memory_pa=peak))
                append_observation(end,state,p0+slope*(end-t0),opened,
                                   max(peak,p0+slope*(end-t0)))
        if status != 'completed_history':
            break
        peak = max(peak,p1)
    initial_area = float(np.interp(q.initial_depth_m,depths,areas))
    final_area = float(np.interp(state[0],depths,areas))
    work_residual = state[2]-state[3]-state[4]
    if not all(math.isfinite(float(v)) for v in state):
        raise RuntimeError('tearing trajectory exceeds finite support')
    return dict(status=status, **base, completed_time_s=current_time,
                final_depth_m=float(state[0]), final_open=opened,
                final_peak_pressure_memory_pa=max(peak,observations[-1]['pressure_pa']),
                final_state=observations[-1], observations=observations,closure_switches=switches,
                projected_area_integral_residual_m2=float(state[1]-(final_area-initial_area)),
                driving_resistance_overstress_work_residual_j=float(work_residual),
                crack_area_measure='supplied_projected_crack_area_not_two_face_area',
                pressure_interpolation='piecewise_linear_prescribed_inside_minus_outside')
