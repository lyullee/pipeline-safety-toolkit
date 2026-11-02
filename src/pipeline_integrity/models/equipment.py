"""Equipment budgets with declared operating conditions and vendor inputs."""

import math

from pydantic import Field, model_validator

from ..contracts import Calculation, Contract, Nonnegative, Positive
from ..thermo import CoolPropProvider, StateRequest


class PumpPower(Contract):
    volume_flow_m3_s: Nonnegative
    density_kg_m3: Positive
    head_m: Nonnegative
    pump_efficiency: Positive = Field(le=1)
    motor_efficiency: Positive = Field(le=1)
    gravity_m_s2: Positive = 9.80665


def pump_power(p: PumpPower):
    hydraulic = p.volume_flow_m3_s * p.density_kg_m3 * p.gravity_m_s2 * p.head_m
    shaft = hydraulic / p.pump_efficiency
    return Calculation(
        values={
            "hydraulic_power_w": hydraulic,
            "shaft_power_w": shaft,
            "electrical_power_w": shaft / p.motor_efficiency,
        }
    )


class PumpAffinity(Contract):
    reference_flow_m3_s: Nonnegative
    reference_head_m: Nonnegative
    reference_power_w: Nonnegative
    speed_ratio: Positive
    geometric_diameter_ratio: Positive = 1.0


def pump_affinity(p: PumpAffinity):
    n, d = p.speed_ratio, p.geometric_diameter_ratio
    return Calculation(
        values={
            "flow_m3_s": p.reference_flow_m3_s * n * d**3,
            "head_m": p.reference_head_m * n**2 * d**2,
            "power_w": p.reference_power_w * n**3 * d**5,
        },
        notes=(
            "Assumes similar geometry and unchanged efficiency; impeller trimming needs a separate vendor model.",
        ),
    )


class PumpNPSH(Contract):
    surface_pressure_pa_abs: Positive
    vapour_pressure_pa_abs: Positive
    density_kg_m3: Positive
    surface_above_pump_m: float
    suction_head_loss_m: Nonnegative
    vendor_required_npsh_m: Nonnegative
    gravity_m_s2: Positive = 9.80665


def pump_npsh(p: PumpNPSH):
    available = (
        (p.surface_pressure_pa_abs - p.vapour_pressure_pa_abs) / (p.density_kg_m3 * p.gravity_m_s2)
        + p.surface_above_pump_m
        - p.suction_head_loss_m
    )
    return Calculation(
        values={"available_npsh_m": available, "declared_margin_m": available - p.vendor_required_npsh_m},
        notes=("Vendor required NPSH and the chosen margin criterion remain external reviewed inputs.",),
    )


class Compression(Contract):
    fluid: str = Field(min_length=1)
    inlet_pressure_pa_abs: Positive
    inlet_temperature_k: Positive
    outlet_pressure_pa_abs: Positive
    mass_flow_kg_s: Nonnegative
    isentropic_efficiency: Positive = Field(le=1)

    @model_validator(mode="after")
    def compression(self):
        if self.outlet_pressure_pa_abs <= self.inlet_pressure_pa_abs:
            raise ValueError("compressor outlet pressure must exceed inlet pressure")
        return self


def compression(p: Compression, *, property_provider=None):
    provider = property_provider if property_provider is not None else CoolPropProvider()
    inlet = provider.resolve(
        StateRequest(fluid=p.fluid, inputs={"P": p.inlet_pressure_pa_abs, "T": p.inlet_temperature_k})
    )
    if not (inlet.phase.startswith("gas") or inlet.phase.startswith("supercritical")):
        return Calculation(
            values={},
            status="out_of_scope",
            notes=("The compressor model requires a gaseous or supercritical inlet.",),
        )
    ideal = provider.resolve(
        StateRequest(fluid=p.fluid, inputs={"P": p.outlet_pressure_pa_abs, "Smass": inlet.entropy_j_kg_k})
    )
    work = (ideal.enthalpy_j_kg - inlet.enthalpy_j_kg) / p.isentropic_efficiency
    outlet = provider.resolve(
        StateRequest(
            fluid=p.fluid, inputs={"P": p.outlet_pressure_pa_abs, "Hmass": inlet.enthalpy_j_kg + work}
        )
    )
    return Calculation(
        values={
            "specific_work_j_kg": work,
            "shaft_power_w": work * p.mass_flow_kg_s,
            "outlet_temperature_k": outlet.temperature_k,
            "outlet_enthalpy_j_kg": outlet.enthalpy_j_kg,
        },
        notes=("Adiabatic steady compressor; no intercooler, stage map or mechanical losses are inferred.",),
    )


class LiquidMeter(Contract):
    pipe_diameter_m: Positive
    opening_diameter_m: Positive
    pressure_difference_pa: Nonnegative
    density_kg_m3: Positive
    discharge_coefficient: Positive = Field(le=1)

    @model_validator(mode="after")
    def opening_inside_pipe(self):
        if self.opening_diameter_m >= self.pipe_diameter_m:
            raise ValueError("meter opening must be smaller than pipe bore")
        return self


def liquid_meter(p: LiquidMeter):
    beta = p.opening_diameter_m / p.pipe_diameter_m
    area = math.pi * p.opening_diameter_m**2 / 4
    q = (
        p.discharge_coefficient
        * area
        * math.sqrt(2 * p.pressure_difference_pa / p.density_kg_m3 / (1 - beta**4))
    )
    return Calculation(
        values={"volume_flow_m3_s": q, "mass_flow_kg_s": q * p.density_kg_m3, "diameter_ratio": beta},
        notes=(
            "Single-phase incompressible differential-pressure relation; coefficient and cavitation applicability are reviewed upstream.",
        ),
    )
