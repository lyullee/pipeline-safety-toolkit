"""Curated library calls with declared operating and coefficient conventions."""

from typing import Literal

from pydantic import Field, model_validator

from ..contracts import Calculation, Contract, Nonnegative, Positive


class InternalConvection(Contract):
    reynolds: Positive
    prandtl: Positive
    diameter_m: Positive
    conductivity_w_m_k: Positive
    relative_roughness: Nonnegative = Field(default=0.0, le=0.1)
    method: Literal[
        "Gnielinski", "Churchill-Zajic", "Dittus-Boelter", "Laminar - constant T", "Laminar - constant Q"
    ]


def internal_convection(p: InternalConvection):
    from ht.conv_internal import Nu_conv_internal, Nu_conv_internal_methods

    if p.method not in Nu_conv_internal_methods(p.reynolds, p.prandtl, eD=p.relative_roughness):
        return Calculation(
            values={},
            status="out_of_scope",
            notes=("Selected correlation is unavailable for the declared Reynolds/Prandtl regime.",),
        )
    if 2300 <= p.reynolds < 4000:
        return Calculation(
            values={},
            status="out_of_scope",
            notes=("Transition is withheld from the laminar/fully turbulent correlation adapter.",),
        )
    if p.method == "Gnielinski" and not (3000 <= p.reynolds <= 5e6 and 0.5 <= p.prandtl <= 2000):
        return Calculation(
            values={},
            status="out_of_scope",
            notes=("State is outside this adapter's Gnielinski screening range.",),
        )
    nu = Nu_conv_internal(
        Re=p.reynolds, Pr=p.prandtl, eD=p.relative_roughness, Di=p.diameter_m, Method=p.method
    )
    return Calculation(
        values={"nusselt": float(nu), "coefficient_w_m2_k": float(nu * p.conductivity_w_m_k / p.diameter_m)},
        notes=(
            "A regime lookup is not a complete check of entrance length, wall condition or property variation.",
        ),
    )


class LiquidControlValve(Contract):
    density_kg_m3: Positive
    vapour_pressure_pa_abs: Positive
    critical_pressure_pa_abs: Positive
    viscosity_pa_s: Positive
    inlet_pressure_pa_abs: Positive
    outlet_pressure_pa_abs: Positive
    volume_flow_m3_s: Positive
    inlet_diameter_m: Positive
    outlet_diameter_m: Positive
    valve_diameter_m: Positive
    pressure_recovery_factor: Positive = Field(le=1)
    valve_style_factor: Positive = Field(le=1)

    @model_validator(mode="after")
    def pressure_state(self):
        if self.inlet_pressure_pa_abs <= self.outlet_pressure_pa_abs:
            raise ValueError("the directed valve sizing model requires a positive pressure drop")
        if self.vapour_pressure_pa_abs >= self.inlet_pressure_pa_abs:
            raise ValueError("declared liquid inlet is not above the supplied vapour pressure")
        if self.vapour_pressure_pa_abs >= self.critical_pressure_pa_abs:
            raise ValueError("supplied vapour pressure must be below the liquid critical pressure")
        return self


def liquid_control_valve(p: LiquidControlValve):
    from fluids.control_valve import size_control_valve_l

    result = size_control_valve_l(
        rho=p.density_kg_m3,
        Psat=p.vapour_pressure_pa_abs,
        Pc=p.critical_pressure_pa_abs,
        mu=p.viscosity_pa_s,
        P1=p.inlet_pressure_pa_abs,
        P2=p.outlet_pressure_pa_abs,
        Q=p.volume_flow_m3_s,
        D1=p.inlet_diameter_m,
        D2=p.outlet_diameter_m,
        d=p.valve_diameter_m,
        FL=p.pressure_recovery_factor,
        Fd=p.valve_style_factor,
        full_output=True,
    )
    return Calculation(
        values={"kv": float(result["Kv"]), "diagnostics": result},
        notes=(
            "Kv is the conventional water coefficient in m3/h at a 1-bar drop, not an SI volumetric conductance.",
            "Manufacturer coefficients and installed behavior need independent confirmation.",
        ),
    )


class GasControlValve(Contract):
    temperature_k: Positive
    molar_mass_kg_mol: Positive
    viscosity_pa_s: Positive
    heat_capacity_ratio: float = Field(gt=1)
    compressibility: Positive
    inlet_pressure_pa_abs: Positive
    outlet_pressure_pa_abs: Positive
    standard_volume_flow_m3_s: Positive
    reference_temperature_k: Literal[273.15]
    reference_pressure_pa_abs: Literal[101325.0]
    inlet_diameter_m: Positive
    outlet_diameter_m: Positive
    valve_diameter_m: Positive
    pressure_recovery_factor: Positive = Field(le=1)
    valve_style_factor: Positive = Field(le=1)
    terminal_pressure_ratio_factor: Positive = Field(le=1)

    @model_validator(mode="after")
    def directed_pressure(self):
        if self.inlet_pressure_pa_abs <= self.outlet_pressure_pa_abs:
            raise ValueError("the directed valve sizing model requires a positive pressure drop")
        return self


def gas_control_valve(p: GasControlValve):
    from fluids.control_valve import size_control_valve_g

    result = size_control_valve_g(
        T=p.temperature_k,
        MW=p.molar_mass_kg_mol * 1000,
        mu=p.viscosity_pa_s,
        gamma=p.heat_capacity_ratio,
        Z=p.compressibility,
        P1=p.inlet_pressure_pa_abs,
        P2=p.outlet_pressure_pa_abs,
        Q=p.standard_volume_flow_m3_s,
        D1=p.inlet_diameter_m,
        D2=p.outlet_diameter_m,
        d=p.valve_diameter_m,
        FL=p.pressure_recovery_factor,
        Fd=p.valve_style_factor,
        xT=p.terminal_pressure_ratio_factor,
        full_output=True,
    )
    return Calculation(
        values={"kv": float(result["Kv"]), "diagnostics": result},
        notes=(
            "Gas reference flow is specifically at 273.15 K and 101325 Pa; no other normal-volume convention is substituted.",
        ),
    )


class ReliefGas(Contract):
    mass_flow_kg_s: Positive
    temperature_k: Positive
    compressibility: Positive
    molar_mass_kg_mol: Positive
    heat_capacity_ratio: float = Field(gt=1)
    relieving_pressure_pa_abs: Positive
    back_pressure_pa_abs: Positive
    discharge_coefficient: Positive = Field(le=1)
    back_pressure_factor: Positive = Field(le=1)
    combination_factor: Positive = Field(le=1)

    @model_validator(mode="after")
    def directed_pressure(self):
        if self.relieving_pressure_pa_abs <= self.back_pressure_pa_abs:
            raise ValueError("relieving pressure must exceed back pressure")
        return self


def relief_gas(p: ReliefGas):
    from fluids.safety_valve import API520_A_g

    area = API520_A_g(
        m=p.mass_flow_kg_s,
        T=p.temperature_k,
        Z=p.compressibility,
        MW=p.molar_mass_kg_mol * 1000,
        k=p.heat_capacity_ratio,
        P1=p.relieving_pressure_pa_abs,
        P2=p.back_pressure_pa_abs,
        Kd=p.discharge_coefficient,
        Kb=p.back_pressure_factor,
        Kc=p.combination_factor,
    )
    return Calculation(
        values={"required_area_m2": float(area)},
        notes=(
            "External Fluids correlation only; relief scenarios, certified devices and complete standards compliance are outside this result.",
        ),
    )
