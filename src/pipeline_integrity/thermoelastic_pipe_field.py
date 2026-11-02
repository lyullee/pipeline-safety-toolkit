"""Conservative ideal-gas field coupled to a thermally expanding elastic wall.

The stationary wall carries entropy and energy, rather than being transported
with the fluid. Heat exchanged at the interface cancels in the combined energy
equation. All wall coefficients and support bounds are caller supplied.
"""
from __future__ import annotations

import math
from typing import Literal

import numpy as np
from pydantic import Field, model_validator
from scipy.optimize import brentq

from .contracts import Contract, Name, Nonnegative, Positive, digest


class ThermoelasticWall(Contract):
    reference_area_m2: Positive
    reference_temperature_K: Positive
    area_compliance_m2_pa: Positive
    natural_area_temperature_derivative_m2_K: float
    heat_capacity_per_length_J_m_K: Positive
    exterior_pressure_pa_abs: Nonnegative
    minimum_pressure_pa_abs: Positive
    maximum_pressure_pa_abs: Positive
    minimum_temperature_K: Positive
    maximum_temperature_K: Positive
    maximum_reference_area_fractional_change: Positive = Field(le=.1)
    constitutive_evidence_id: Name

    @model_validator(mode='after')
    def stable_support(self):
        if not self.exterior_pressure_pa_abs <= self.minimum_pressure_pa_abs < self.maximum_pressure_pa_abs:
            raise ValueError('tensile absolute-pressure bracket must be increasing and above exterior')
        if not self.minimum_temperature_K < self.maximum_temperature_K:
            raise ValueError('increasing temperature support required')
        # The wall's constant-area heat capacity must remain positive.
        a = self.natural_area_temperature_derivative_m2_K
        if self.heat_capacity_per_length_J_m_K <= self.maximum_temperature_K * a*a / self.area_compliance_m2_pa:
            raise ValueError('wall free energy has nonpositive constant-area heat capacity')
        return self


class ThermoelasticPipeFieldRequest(Contract):
    id: Name
    length_m: Positive
    gas_constant_J_kg_K: Positive
    heat_capacity_ratio: float = Field(gt=1)
    wall: ThermoelasticWall
    fluid_wall_conductance_W_m_K: Nonnegative
    external_bath_conductance_W_m_K: Nonnegative = 0
    external_bath_temperature_K: Positive | None = None
    initial_pressure_pa_abs: tuple[Positive, ...] = Field(min_length=3)
    initial_fluid_temperature_K: tuple[Positive, ...] = Field(min_length=3)
    initial_wall_temperature_K: tuple[Positive, ...] = Field(min_length=3)
    initial_velocity_m_s: tuple[float, ...] = Field(min_length=3)
    end_time_s: Positive
    maximum_step_s: Positive
    cfl: Positive = Field(le=.4)
    boundary: Literal['periodic', 'reflective'] = 'reflective'
    maximum_steps: int = Field(default=100000, ge=1)
    fixed_axial_length_quasistatic_wall_and_ideal_gas_reviewed: bool = False

    @model_validator(mode='after')
    def alignment(self):
        if len({len(self.initial_pressure_pa_abs), len(self.initial_fluid_temperature_K),
                len(self.initial_wall_temperature_K), len(self.initial_velocity_m_s)}) != 1:
            raise ValueError('initial cell arrays must align')
        if self.external_bath_conductance_W_m_K > 0 and self.external_bath_temperature_K is None:
            raise ValueError('external heat exchange requires an absolute bath temperature')
        w=self.wall
        if any(not w.minimum_pressure_pa_abs<=p<=w.maximum_pressure_pa_abs for p in self.initial_pressure_pa_abs):
            raise ValueError('initial pressure lies outside wall pressure support')
        if any(not w.minimum_temperature_K<=t<=w.maximum_temperature_K
               for t in (*self.initial_fluid_temperature_K,*self.initial_wall_temperature_K)):
            raise ValueError('initial temperature lies outside wall temperature support')
        for pressure,temperature in zip(self.initial_pressure_pa_abs,self.initial_wall_temperature_K):
            area=(w.reference_area_m2+w.natural_area_temperature_derivative_m2_K*(temperature-w.reference_temperature_K)
                  +w.area_compliance_m2_pa*(pressure-w.exterior_pressure_pa_abs))
            if area<=0 or abs(area/w.reference_area_m2-1)>w.maximum_reference_area_fractional_change:
                raise ValueError('initial area lies outside reviewed wall strain support')
        return self


class ThermoelasticPipeFieldResult(Contract):
    request_digest: str
    times_s: tuple[float, ...]
    final_pressure_pa_abs: tuple[float, ...]
    final_fluid_temperature_K: tuple[float, ...]
    final_wall_temperature_K: tuple[float, ...]
    final_velocity_m_s: tuple[float, ...]
    final_area_m2: tuple[float, ...]
    initial_mass_kg: float
    final_mass_kg: float
    mass_balance_residual_kg: float
    initial_combined_energy_J: float
    final_combined_energy_J: float
    external_heat_J: float
    boundary_energy_out_J: float
    energy_balance_residual_J: float
    initial_fluid_momentum_kg_m_s: float
    final_fluid_momentum_kg_m_s: float
    wall_and_boundary_impulse_N_s: float
    momentum_balance_residual_kg_m_s: float
    constitutive_evidence_id: str
    numerical_qualification_complete: bool = False
    limitations: tuple[str, ...]


def _physical(p, tf, tw, velocity, q):
    w = q.wall
    a = w.natural_area_temperature_derivative_m2_K
    dp = p - w.exterior_pressure_pa_abs
    area = w.reference_area_m2 + a*(tw-w.reference_temperature_K) + w.area_compliance_m2_pa*dp
    if (not all(math.isfinite(x) for x in (p, tf, tw, velocity, area))
            or not w.minimum_pressure_pa_abs <= p <= w.maximum_pressure_pa_abs
            or not w.minimum_temperature_K <= min(tf, tw)
            or max(tf, tw) > w.maximum_temperature_K or area <= 0
            or abs(area/w.reference_area_m2-1) > w.maximum_reference_area_fractional_change):
        raise ValueError('physical state leaves the reviewed pressure/temperature/area support')
    mass = p*area/(q.gas_constant_J_kg_K*tf)
    # Uwall = Fwall - Tw*dFwall/dTw, including exterior-pressure potential.
    wall_energy = (.5*w.area_compliance_m2_pa*dp*dp + tw*a*dp
                   + w.heat_capacity_per_length_J_m_K*(tw-w.reference_temperature_K)
                   + w.exterior_pressure_pa_abs*(area-w.reference_area_m2))
    energy = p*area/(q.heat_capacity_ratio-1) + .5*mass*velocity*velocity + wall_energy
    entropy = (w.heat_capacity_per_length_J_m_K*math.log(tw/w.reference_temperature_K) + a*dp)
    effective_compliance = w.area_compliance_m2_pa - tw*a*a/w.heat_capacity_per_length_J_m_K
    wave = math.sqrt(q.heat_capacity_ratio*p*area*area/mass /
                     (area + q.heat_capacity_ratio*p*effective_compliance))
    return np.array((mass, mass*velocity, energy, entropy)), (p, tf, tw, velocity, area, wave)


def _recover(state, q):
    mass, momentum, energy, entropy = (float(x) for x in state)
    if not all(math.isfinite(x) for x in state) or mass <= 0:
        raise ValueError('finite positive line mass and finite conservative state required')
    w = q.wall
    a = w.natural_area_temperature_derivative_m2_K
    velocity = momentum/mass

    def at_pressure(p, check=False):
        exponent = (entropy-a*(p-w.exterior_pressure_pa_abs))/w.heat_capacity_per_length_J_m_K
        if exponent > 700 or exponent < -700:
            raise ValueError('wall entropy inversion leaves finite support')
        tw = w.reference_temperature_K*math.exp(exponent)
        dp = p-w.exterior_pressure_pa_abs
        area = w.reference_area_m2+a*(tw-w.reference_temperature_K)+w.area_compliance_m2_pa*dp
        tf = p*area/(mass*q.gas_constant_J_kg_K)
        predicted = (p*area/(q.heat_capacity_ratio-1)+.5*mass*velocity*velocity
                     +.5*w.area_compliance_m2_pa*dp*dp+tw*a*dp
                     +w.heat_capacity_per_length_J_m_K*(tw-w.reference_temperature_K)
                     +w.exterior_pressure_pa_abs*(area-w.reference_area_m2))
        if check:
            return _physical(p, tf, tw, velocity, q)[1]
        return predicted-energy

    lo, hi = w.minimum_pressure_pa_abs, w.maximum_pressure_pa_abs
    # Fixed wall entropy gives a monotone temperature-pressure relation. Restrict
    # the root bracket to its declared wall temperature support before solving.
    if a != 0:
        bounds = [(entropy-w.heat_capacity_per_length_J_m_K*math.log(t/w.reference_temperature_K))/a
                  + w.exterior_pressure_pa_abs for t in (w.minimum_temperature_K, w.maximum_temperature_K)]
        lo, hi = max(lo, min(bounds)), min(hi, max(bounds))
    if lo >= hi:
        raise ValueError('wall entropy has no supported pressure interval')
    flo, fhi = at_pressure(lo), at_pressure(hi)
    if flo == 0:
        p = lo
    elif fhi == 0:
        p = hi
    elif flo*fhi > 0:
        raise ValueError('combined energy has no root in the reviewed constitutive bracket')
    else:
        p = brentq(at_pressure, lo, hi, xtol=max(1e-8, 1e-12*lo), rtol=2e-12)
    return at_pressure(p, check=True)


def solve_thermoelastic_pipe_field(request: ThermoelasticPipeFieldRequest) -> ThermoelasticPipeFieldResult:
    """Advance a horizontal fixed-length thermoelastic gas pipe with SSP-RK2.

    Fluid mass and combined fluid/wall/exterior-potential energy use FV face
    ledgers. Momentum includes the physical wall-area traction p*dA/dx. Wall
    entropy has no axial flux; its source is net wall heat divided by Tw.
    Support exits raise an error rather than clipping temperatures or areas.
    """
    q = ThermoelasticPipeFieldRequest.model_validate(request.model_dump())
    if not q.fixed_axial_length_quasistatic_wall_and_ideal_gas_reviewed:
        raise ValueError('reviewed fixed-length ideal-gas and quasi-static wall basis required')
    state = np.array([_physical(*row, q)[0] for row in zip(q.initial_pressure_pa_abs,
                      q.initial_fluid_temperature_K, q.initial_wall_temperature_K,
                      q.initial_velocity_m_s)])
    count = len(state)
    dx = q.length_m/count
    initial = np.sum(state[:, :3], axis=0)*dx
    external_heat = boundary_energy_out = impulse = boundary_mass_out = 0.
    times = [0.]

    def rhs(values):
        physical = np.array([_recover(row, q) for row in values])
        if q.boundary == 'periodic':
            padded = np.vstack((values[-1], values, values[0]))
            points = np.vstack((physical[-1], physical, physical[0]))
        else:
            left, right = values[0].copy(), values[-1].copy()
            left[1] *= -1; right[1] *= -1
            pl, pr = physical[0].copy(), physical[-1].copy()
            pl[3] *= -1; pr[3] *= -1
            padded = np.vstack((left, values, right))
            points = np.vstack((pl, physical, pr))
        pressure, velocity, area = points[:, 0], points[:, 3], points[:, 4]
        flux = np.zeros_like(padded)
        flux[:, 0] = padded[:, 1]
        flux[:, 1] = padded[:, 1]*velocity + pressure*area
        # Stationary wall energy is excluded from the advected enthalpy flux.
        flux[:, 2] = velocity*(q.heat_capacity_ratio/(q.heat_capacity_ratio-1)*pressure*area
                               + .5*padded[:, 0]*velocity**2)
        waves = np.maximum(np.abs(points[:-1, 3])+points[:-1, 5],
                           np.abs(points[1:, 3])+points[1:, 5])
        faces = .5*(flux[:-1]+flux[1:])-.5*waves[:, None]*(padded[1:]-padded[:-1])
        faces[:, 3] = 0.  # Wall entropy belongs to the stationary material.
        derivative = -(faces[1:]-faces[:-1])/dx
        traction = physical[:, 0]*(points[2:, 4]-points[:-2, 4])/(2*dx)
        derivative[:, 1] += traction
        tf, tw = physical[:, 1], physical[:, 2]
        interface = q.fluid_wall_conductance_W_m_K*(tw-tf)
        bath = (np.zeros(count) if q.external_bath_conductance_W_m_K == 0 else
                q.external_bath_conductance_W_m_K*(q.external_bath_temperature_K-tw))
        derivative[:, 2] += bath
        derivative[:, 3] = (bath-interface)/tw
        # These are rates of the same discrete update, including wall traction.
        rates = np.array((np.sum(bath)*dx, faces[-1, 2]-faces[0, 2],
                          np.sum(traction)*dx-(faces[-1, 1]-faces[0, 1]),
                          faces[-1, 0]-faces[0, 0]))
        return derivative, physical, rates

    for _ in range(q.maximum_steps):
        if times[-1] >= q.end_time_s:
            break
        k1, physical, r1 = rhs(state)
        dt = min(q.maximum_step_s, q.end_time_s-times[-1],
                 q.cfl*dx/float(np.max(np.abs(physical[:, 3])+physical[:, 5])))
        # Explicit thermal exchange also needs a heat-capacity step bound.
        cv = q.gas_constant_J_kg_K/(q.heat_capacity_ratio-1)
        wall_cv = (q.wall.heat_capacity_per_length_J_m_K
                   - physical[:, 2]*q.wall.natural_area_temperature_derivative_m2_K**2/q.wall.area_compliance_m2_pa)
        conductance = q.fluid_wall_conductance_W_m_K+q.external_bath_conductance_W_m_K
        if conductance > 0:
            dt = min(dt, .2*float(np.min(np.minimum(state[:, 0]*cv, wall_cv)))/conductance)
        if not math.isfinite(dt) or dt <= 0 or times[-1]+dt == times[-1]:
            raise ValueError('no representable stable time increment')
        # A support boundary is an event. Retry by reducing the numerical step;
        # no alternate constitutive branch is silently selected.
        for _retry in range(24):
            try:
                stage = state+dt*k1
                k2, _, r2 = rhs(stage)
                candidate = .5*state+.5*(stage+dt*k2)
                for row in candidate:
                    _recover(row, q)
                break
            except ValueError:
                dt *= .5
        else:
            raise ValueError('time advance reaches unsupported thermoelastic state')
        rates = .5*dt*(r1+r2)
        external_heat += rates[0]; boundary_energy_out += rates[1]
        impulse += rates[2]; boundary_mass_out += rates[3]
        state = candidate
        times.append(min(q.end_time_s, times[-1]+dt))
    else:
        if times[-1] < q.end_time_s:
            raise ValueError('maximum_steps reached before the requested end time')
    physical = np.array([_recover(row, q) for row in state])
    final = np.sum(state[:, :3], axis=0)*dx
    return ThermoelasticPipeFieldResult(request_digest=digest(q.model_dump()), times_s=tuple(times),
        final_pressure_pa_abs=tuple(physical[:, 0]), final_fluid_temperature_K=tuple(physical[:, 1]),
        final_wall_temperature_K=tuple(physical[:, 2]), final_velocity_m_s=tuple(physical[:, 3]),
        final_area_m2=tuple(physical[:, 4]), initial_mass_kg=initial[0], final_mass_kg=final[0],
        mass_balance_residual_kg=final[0]-initial[0]+boundary_mass_out,
        initial_combined_energy_J=initial[2], final_combined_energy_J=final[2],
        external_heat_J=external_heat, boundary_energy_out_J=boundary_energy_out,
        energy_balance_residual_J=final[2]-initial[2]+boundary_energy_out-external_heat,
        initial_fluid_momentum_kg_m_s=initial[1], final_fluid_momentum_kg_m_s=final[1],
        wall_and_boundary_impulse_N_s=impulse,
        momentum_balance_residual_kg_m_s=final[1]-initial[1]-impulse,
        constitutive_evidence_id=q.wall.constitutive_evidence_id,
        limitations=('Calorically perfect ideal gas; fixed axial length and exterior pressure, horizontal pipe.',
            'Linear pressure-area and natural-area temperature law with stable supplied wall heat capacity.',
            'Periodic or reflecting boundary; no imposed reservoir, open nozzle, axial motion or wall inertia.',
            'Numerical convergence, thermodynamic references and field calibration remain pending.'))
