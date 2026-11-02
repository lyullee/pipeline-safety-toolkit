"""Isothermal lumped gas pumpdown with series conductance and supplied gas load."""
from __future__ import annotations
import math
from typing import Literal
from pydantic import Field
from .contracts import Contract,Name,Nonnegative,Positive


class VacuumPumpdownRequest(Contract):
    volume_m3: Positive
    initial_pressure_Pa_abs: Positive
    pump_speed_m3_s: Nonnegative | None = None
    line_conductances_m3_s: tuple[Nonnegative | None,...] | None = None
    pump_ultimate_pressure_Pa_abs: Nonnegative | None = None
    constant_gas_load_Pa_m3_s: Nonnegative | None = None
    gas_temperature_K: Positive
    specific_gas_constant_J_kg_K: Positive
    elapsed_times_s: tuple[Nonnegative,...] = Field(min_length=1,max_length=10000)
    target_pressure_Pa_abs: Nonnegative | None = None
    clock_id: Name
    pump_start_time_s: float
    isothermal_fixed_composition_and_constant_linear_throughput_reviewed: bool = False


class VacuumPumpdownPoint(Contract):
    elapsed_time_s: Nonnegative
    time_s: float
    pressure_Pa_abs: Nonnegative
    gas_mass_kg: Nonnegative
    cumulative_removed_mass_kg: Nonnegative
    cumulative_added_mass_kg: Nonnegative
    mass_budget_residual_kg: float


class VacuumPumpdownResult(Contract):
    status: Literal['computed','needs_data']
    clock_id: Name
    effective_speed_m3_s: Nonnegative | None = None
    asymptotic_pressure_Pa_abs: Nonnegative | None = None
    target_status: Literal['not_requested','at_start','finite_time','unreachable'] = 'not_requested'
    time_to_target_s: Nonnegative | None = None
    points: tuple[VacuumPumpdownPoint,...] = ()
    issues: tuple[str,...] = ()
    free_water_evaporation_or_dryness_predicted: Literal[False] = False


def evaluate_vacuum_pumpdown(q: VacuumPumpdownRequest) -> VacuumPumpdownResult:
    """Solve V*dP/dt=Qload-Seff*(P-Pultimate) at one fixed gas temperature.

    Pump speed and each line conductance are supplied constant linear throughput
    coefficients in m3/s at the declared gas state. An empty conductance tuple
    explicitly requests direct connection; None is unknown. A zero coefficient
    isolates the vessel. Loads, vapor replenishment and pump ultimate pressure
    are separate inputs; no water saturation curve or heat supply is inferred.
    Target time is the first time pressure is at or below the stated upper bound.
    """
    if not isinstance(q,VacuumPumpdownRequest):raise TypeError('VacuumPumpdownRequest required')
    q=VacuumPumpdownRequest.model_validate(q.model_dump(mode='python'))
    missing=(q.pump_speed_m3_s is None or q.line_conductances_m3_s is None or
        any(x is None for x in (q.line_conductances_m3_s or ())) or q.pump_ultimate_pressure_Pa_abs is None or
        q.constant_gas_load_Pa_m3_s is None)
    if missing or not q.isothermal_fixed_composition_and_constant_linear_throughput_reviewed:
        return VacuumPumpdownResult(status='needs_data',clock_id=q.clock_id,issues=('reviewed constant gas state/pump-line model and complete coefficients/load/ultimate pressure required',))
    if q.initial_pressure_Pa_abs<q.pump_ultimate_pressure_Pa_abs:
        raise ValueError('initial pressure below the supplied pump ultimate pressure is outside this model')
    speeds=(q.pump_speed_m3_s,*q.line_conductances_m3_s);small=min(speeds)
    speed=0. if small==0 else small/math.fsum(small/x for x in speeds)
    if small>0 and speed==0:raise ValueError('effective conductance is below numerical support')
    rt=q.specific_gas_constant_J_kg_K*q.gas_temperature_K
    if not math.isfinite(rt):raise ValueError('gas mass coordinate exceeds numerical support')
    initial_mass=q.initial_pressure_Pa_abs*q.volume_m3/rt
    equilibrium=None;tau=None
    if speed>0:
        tau=q.volume_m3/speed;equilibrium=q.pump_ultimate_pressure_Pa_abs+q.constant_gas_load_Pa_m3_s/speed
        if not math.isfinite(tau) or not math.isfinite(equilibrium):raise ValueError('pumpdown scale exceeds numerical support')
    rows=[]
    for t in q.elapsed_times_s:
        added=q.constant_gas_load_Pa_m3_s*t/rt
        if speed==0:
            pressure=q.initial_pressure_Pa_abs+q.constant_gas_load_Pa_m3_s*t/q.volume_m3;removed=0.
        else:
            z=t/tau;fraction=-math.expm1(-z)
            pressure=(q.pump_ultimate_pressure_Pa_abs+(q.initial_pressure_Pa_abs-q.pump_ultimate_pressure_Pa_abs)*math.exp(-z)
                +(q.constant_gas_load_Pa_m3_s/speed)*fraction)
            # Integrating nonnegative pump throughput avoids subtracting nearly
            # equal added/inventory quantities at very short times.
            lag=tau*(z*z*(.5-z/6+z*z/24)) if z<1e-4 else t-tau*fraction
            pumped_pv=q.volume_m3*(q.initial_pressure_Pa_abs-q.pump_ultimate_pressure_Pa_abs)*fraction+q.constant_gas_load_Pa_m3_s*lag
            removed=pumped_pv/rt
        mass=pressure*q.volume_m3/rt;time=q.pump_start_time_s+t
        error=math.fsum((initial_mass,added,-removed,-mass))
        if not all(math.isfinite(x) for x in (pressure,mass,added,removed,time,error)) or min(pressure,mass,added,removed)<0:
            raise ValueError('pumpdown state exceeds finite nonnegative numerical support')
        rows.append(VacuumPumpdownPoint(elapsed_time_s=t,time_s=time,pressure_Pa_abs=pressure,gas_mass_kg=mass,
            cumulative_removed_mass_kg=removed,cumulative_added_mass_kg=added,mass_budget_residual_kg=error))
    target_status='not_requested';target_time=None
    if q.target_pressure_Pa_abs is not None:
        target=q.target_pressure_Pa_abs
        if target>=q.initial_pressure_Pa_abs:target_status='at_start';target_time=0.
        elif speed==0 or q.initial_pressure_Pa_abs<=equilibrium or target<=equilibrium:target_status='unreachable'
        else:
            ratio=(q.initial_pressure_Pa_abs-target)/(target-equilibrium)
            log_ratio=math.log1p(ratio) if math.isfinite(ratio) else math.log(q.initial_pressure_Pa_abs-equilibrium)-math.log(target-equilibrium)
            target_time=tau*log_ratio;target_status='finite_time'
            if not math.isfinite(target_time):raise ValueError('target time exceeds finite numerical support')
    return VacuumPumpdownResult(status='computed',clock_id=q.clock_id,effective_speed_m3_s=speed,
        asymptotic_pressure_Pa_abs=equilibrium,target_status=target_status,time_to_target_s=target_time,points=tuple(rows))
