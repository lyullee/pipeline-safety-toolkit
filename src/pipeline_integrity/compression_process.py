"""Reversible steady-flow ideal-gas compression along a declared polytropic path."""
import math

from pydantic import Field, model_validator

from .contracts import Calculation, Contract, Nonnegative, Positive


class IdealGasCompressionProcess(Contract):
    inlet_pressure_pa_abs: Positive
    outlet_pressure_pa_abs: Positive
    inlet_temperature_k: Positive
    specific_gas_constant_j_kg_k: Positive
    polytropic_exponent: float = Field(ge=1.0)
    mass_flow_kg_s: Nonnegative = 1.0
    constant_cp_j_kg_k: Positive | None = None

    @model_validator(mode="after")
    def compression_domain(self):
        if self.outlet_pressure_pa_abs <= self.inlet_pressure_pa_abs:
            raise ValueError("compression requires a strictly higher absolute outlet pressure")
        if self.constant_cp_j_kg_k is not None and self.constant_cp_j_kg_k <= self.specific_gas_constant_j_kg_k:
            raise ValueError("ideal-gas heat capacity requires cp greater than R")
        return self


def evaluate_ideal_gas_compression_process(request: IdealGasCompressionProcess) -> Calculation:
    """Compute positive work input; optional heat is positive into the gas.

    Work is the steady-flow integral of specific volume against pressure.
    The supplied exponent describes the process; it is not a compressor efficiency.
    """
    q = IdealGasCompressionProcess.model_validate(request.model_dump(mode="python"))
    jump = (q.outlet_pressure_pa_abs - q.inlet_pressure_pa_abs) / q.inlet_pressure_pa_abs
    log_ratio = math.log1p(jump) if math.isfinite(jump) else math.log(q.outlet_pressure_pa_abs) - math.log(q.inlet_pressure_pa_abs)
    exponent = (q.polytropic_exponent - 1) / q.polytropic_exponent
    y = exponent * log_ratio
    try:
        # expm1(y)/y retains the isothermal limit without cancellation.
        relative_exponential = 1.0 if y == 0 else math.expm1(y) / y
        work = q.specific_gas_constant_j_kg_k * q.inlet_temperature_k * log_ratio * relative_exponential
        temperature_rise = q.inlet_temperature_k * math.expm1(y)
        values = {
            "specific_work_input_j_kg": work,
            "work_input_power_w": q.mass_flow_kg_s * work,
            "outlet_temperature_k": q.inlet_temperature_k + temperature_rise,
            "temperature_rise_k": temperature_rise,
        }
        if q.constant_cp_j_kg_k is not None:
            delta_h = q.constant_cp_j_kg_k * temperature_rise
            heat = delta_h - work
            values.update(specific_enthalpy_rise_j_kg=delta_h,
                          specific_heat_into_gas_j_kg=heat,
                          heat_into_gas_power_w=q.mass_flow_kg_s * heat)
        if not all(math.isfinite(v) for v in values.values()) or work <= 0:
            raise ValueError("compression result exceeds finite numerical support")
    except (ArithmeticError, OverflowError) as exc:
        raise ValueError("compression path exceeds finite numerical support") from exc
    return Calculation(values=values, notes=(
        "Ideal gas with a supplied constant polytropic exponent; exponent one is isothermal.",
        "Steady reversible flow work only; kinetic/potential changes and equipment losses are omitted.",
        "Heat transfer is calculated only when constant cp is supplied; no real-fluid property accuracy is inferred.",
    ))
