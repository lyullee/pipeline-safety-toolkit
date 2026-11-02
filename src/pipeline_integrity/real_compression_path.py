"""Steady gas compression with a differential efficiency and supplied cooling.

The pressure path uses the existing property-provider protocol. A differential
efficiency is distinct from a finite endpoint isentropic efficiency or an
externally imposed ideal-gas exponent. Cooling boundaries reuse the train API.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator
from scipy.integrate import solve_ivp

from .compressor_train import IntercoolerBoundary
from .contracts import Calculation, Contract, Name, Nonnegative, Positive
from .thermo import CheckedProvider, CoolPropProvider, StateRequest


class DifferentialCompressionStage(Contract):
    id: Name
    outlet_pressure_pa_abs: Positive | None
    differential_polytropic_efficiency: Positive | None = Field(le=1)
    intercooler: IntercoolerBoundary | None
    mechanical_efficiency: Positive | None = Field(default=None, le=1)
    drive_efficiency: Positive | None = Field(default=None, le=1)
    maximum_outlet_temperature_k: Positive | None = None
    evidence_id: Name


class RealCompressionPathRequest(Contract):
    fluid: Name
    backend: Literal['HEOS', 'IF97', 'PR', 'SRK'] = 'HEOS'
    inlet_pressure_pa_abs: Positive | None
    inlet_temperature_k: Positive | None
    mass_flow_kg_s: Nonnegative | None
    stages: tuple[DifferentialCompressionStage, ...] = Field(min_length=1, max_length=16)
    single_phase_steady_adiabatic_stage_path_reviewed: bool | None
    efficiency_and_cooler_boundaries_reviewed: bool | None
    evidence_id: Name
    relative_tolerance: float = Field(default=1e-8, ge=1e-12, le=1e-3)
    absolute_work_tolerance_j_kg: Positive = 1e-5
    minimum_log_pressure_intervals: int = Field(default=16, ge=4, le=1024)

    @model_validator(mode='after')
    def distinct_stages(self):
        if len({s.id for s in self.stages}) != len(self.stages):
            raise ValueError('stage identifiers must be distinct')
        return self


def evaluate_real_compression_path(request: RealCompressionPathRequest, *, property_provider=None) -> Calculation:
    """Integrate dh = v dp / eta, then apply explicit cooler states.

    Each compressor is steady and adiabatic with negligible kinetic and height
    change. Positive heat rejection belongs to the separate cooler. No map,
    surge, phase-change, heat-exchanger transport or machine rating is inferred.
    """
    q = RealCompressionPathRequest.model_validate(request.model_dump(mode='python'))
    required = (q.inlet_pressure_pa_abs, q.inlet_temperature_k, q.mass_flow_kg_s)
    if any(v is None for v in required) or (
        q.single_phase_steady_adiabatic_stage_path_reviewed is not True
        or q.efficiency_and_cooler_boundaries_reviewed is not True
    ):
        return Calculation(status='needs_data', values={}, notes=('Explicit state, flow and reviewed process/cooling boundaries are required.',))
    for stage in q.stages:
        cooler = stage.intercooler
        if stage.outlet_pressure_pa_abs is None or stage.differential_polytropic_efficiency is None or cooler is None:
            return Calculation(status='needs_data', values={}, notes=(f'{stage.id}: pressure, efficiency or cooler boundary is unknown.',))
        if cooler.mode is None or cooler.pressure_drop_pa is None:
            return Calculation(status='needs_data', values={}, notes=(f'{stage.id}: cooler mode and pressure loss must be supplied.',))
        if (cooler.mode == 'outlet_temperature' and cooler.outlet_total_temperature_K is None) or (
            cooler.mode == 'heat_removal' and cooler.heat_removal_W is None
        ):
            return Calculation(status='needs_data', values={}, notes=(f'{stage.id}: selected cooling boundary is unknown.',))

    provider = CheckedProvider(property_provider if property_provider is not None else CoolPropProvider())
    identity = None

    def state(p, *, temperature=None, enthalpy=None):
        nonlocal identity
        inputs = {'P': p, 'T': temperature} if temperature is not None else {'P': p, 'Hmass': enthalpy}
        result = provider.resolve(StateRequest(fluid=q.fluid, backend=q.backend, inputs=inputs))
        if result.phase not in {'gas', 'supercritical_gas', 'supercritical'}:
            raise ValueError('compression/cooling path left the supported gas phase')
        marker = (result.provider_version, result.provider_revision)
        if identity is None:
            identity = marker
        if marker != identity:
            raise ValueError('property-provider revision changed along the path')
        if not math.isclose(result.pressure_pa_abs, p, rel_tol=1e-8):
            raise ValueError('property provider did not reproduce requested pressure')
        if temperature is not None and not math.isclose(result.temperature_k, temperature, rel_tol=1e-8):
            raise ValueError('property provider did not reproduce requested temperature')
        if enthalpy is not None and not math.isclose(result.enthalpy_j_kg, enthalpy, rel_tol=1e-8, abs_tol=1e-4):
            raise ValueError('property provider did not reproduce requested enthalpy')
        return result

    inlet = state(q.inlet_pressure_pa_abs, temperature=q.inlet_temperature_k)
    first = inlet
    outputs, works, heats, shafts, drives = [], [], [], [], []
    for stage in q.stages:
        p1, p2 = inlet.pressure_pa_abs, stage.outlet_pressure_pa_abs
        if p2 <= p1:
            raise ValueError('every compressor stage requires increased absolute pressure')
        extent = math.log(p2) - math.log(p1)
        h1 = inlet.enthalpy_j_kg
        eta = stage.differential_polytropic_efficiency

        def derivative(log_ratio, increment):
            p = p1 * math.exp(log_ratio)
            current = state(p, enthalpy=h1 + float(increment[0]))
            return [p / (current.density_kg_m3 * eta)]

        solution = solve_ivp(derivative, (0., extent), [0.], method='DOP853',
                            rtol=q.relative_tolerance, atol=q.absolute_work_tolerance_j_kg,
                            max_step=extent/q.minimum_log_pressure_intervals)
        if not solution.success:
            raise ValueError('differential compression integration did not complete')
        work = float(solution.y[0, -1])
        compressed = state(p2, enthalpy=h1 + work)
        if stage.maximum_outlet_temperature_k is not None and compressed.temperature_k > stage.maximum_outlet_temperature_k:
            return Calculation(status='out_of_scope', values={'stage_id': stage.id, 'outlet_temperature_k': compressed.temperature_k},
                               notes=('Supplied compressor outlet-temperature limit exceeded.',))
        cooler = stage.intercooler
        pc = p2 - cooler.pressure_drop_pa
        if pc <= 0:
            raise ValueError('cooler pressure loss exhausts absolute pressure')
        if cooler.mode == 'none':
            cooled = compressed
        elif cooler.mode == 'outlet_temperature':
            cooled = state(pc, temperature=cooler.outlet_total_temperature_K)
        else:
            if q.mass_flow_kg_s == 0:
                raise ValueError('a heat-rate cooling boundary needs positive mass flow')
            cooled = state(pc, enthalpy=compressed.enthalpy_j_kg - cooler.heat_removal_W/q.mass_flow_kg_s)
        heat = compressed.enthalpy_j_kg - cooled.enthalpy_j_kg
        if heat < -1e-7:
            raise ValueError('supplied cooler boundary adds heat instead of rejecting it')
        heat = max(heat, 0.)
        duty = q.mass_flow_kg_s * heat
        if cooler.available_heat_rejection_W is not None and duty > cooler.available_heat_rejection_W:
            return Calculation(status='out_of_scope', values={'stage_id': stage.id, 'required_heat_rejection_w': duty},
                               notes=('Supplied cooling capacity is insufficient.',))
        if cooler.minimum_approach_K is not None and cooler.sink_temperature_K is None:
            return Calculation(status='needs_data', values={}, notes=('Cooler approach requires the sink temperature.',))
        if cooler.sink_temperature_K is not None and cooler.minimum_approach_K is not None:
            if cooled.temperature_k < cooler.sink_temperature_K + cooler.minimum_approach_K:
                return Calculation(status='out_of_scope', values={'stage_id': stage.id}, notes=('Supplied cooler approach is infeasible.',))
        power = q.mass_flow_kg_s * work
        shaft = None if stage.mechanical_efficiency is None else power/stage.mechanical_efficiency
        drive = None if shaft is None or stage.drive_efficiency is None else shaft/stage.drive_efficiency
        outputs.append(dict(stage_id=stage.id, inlet=inlet.model_dump(mode='json'),
                            compressed=compressed.model_dump(mode='json'), outlet=cooled.model_dump(mode='json'),
                            specific_fluid_work_j_kg=work, specific_heat_rejected_j_kg=heat,
                            fluid_power_w=power, shaft_power_w=shaft, drive_power_w=drive,
                            property_evaluations=solution.nfev, cooler_evidence_id=cooler.evidence_id,
                            stage_evidence_id=stage.evidence_id))
        works.append(work); heats.append(heat); shafts.append(shaft); drives.append(drive)
        inlet = cooled
    work, heat = math.fsum(works), math.fsum(heats)
    values = dict(stages=outputs, specific_fluid_work_j_kg=work, specific_heat_rejected_j_kg=heat,
                  fluid_power_w=q.mass_flow_kg_s*work, heat_rejection_w=q.mass_flow_kg_s*heat,
                  specific_enthalpy_change_j_kg=inlet.enthalpy_j_kg-first.enthalpy_j_kg,
                  first_law_residual_j_kg=work-heat-(inlet.enthalpy_j_kg-first.enthalpy_j_kg),
                  shaft_power_w=None if any(v is None for v in shafts) else math.fsum(shafts),
                  drive_power_w=None if any(v is None for v in drives) else math.fsum(drives),
                  provider_version=identity[0], provider_revision=identity[1], evidence_id=q.evidence_id)
    return Calculation(values=values, notes=(
        'Differential efficiency is v dp/dh for each adiabatic compressor; it is not an endpoint efficiency.',
        'Coolers use supplied outlet state or heat rate; capacity, sink approach and pressure loss are explicit.',
        'No compressor map, surge, multiphase process or property calibration is predicted.',
    ))
