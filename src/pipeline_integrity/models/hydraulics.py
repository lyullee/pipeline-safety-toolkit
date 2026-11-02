"""Geometry, steady hydraulic budgets and limited transient screening."""

import math
from typing import Literal

from pydantic import Field, model_validator

from ..contracts import Calculation, Contract, Nonnegative, Positive


class PipeGeometry(Contract):
    inner_diameter_m: Positive
    outer_diameter_m: Positive
    length_m: Positive
    wall_density_kg_m3: Positive

    @model_validator(mode="after")
    def wall_exists(self):
        if self.outer_diameter_m <= self.inner_diameter_m:
            raise ValueError("outer diameter must exceed the internal flow diameter")
        return self


def pipe_geometry(p: PipeGeometry):
    area = math.pi * p.inner_diameter_m**2 / 4
    wall_area = math.pi * (p.outer_diameter_m**2 - p.inner_diameter_m**2) / 4
    return Calculation(
        values={
            "flow_area_m2": area,
            "internal_volume_m3": area * p.length_m,
            "wall_volume_m3": wall_area * p.length_m,
            "wall_mass_kg": wall_area * p.length_m * p.wall_density_kg_m3,
            "external_area_m2": math.pi * p.outer_diameter_m * p.length_m,
        }
    )


class _LiquidLossGeometry(Contract):
    diameter_m: Positive
    length_m: Nonnegative
    roughness_m: Nonnegative
    volume_flow_m3_s: float
    minor_loss_coefficient: Nonnegative = 0.0
    outlet_minus_inlet_elevation_m: float = 0.0
    gravity_m_s2: Positive = 9.80665
    transition_policy: Literal["withhold", "use_declared_correlation"] = "withhold"
    friction_method: Literal["Clamond", "Colebrook", "Haaland", "Swamee_Jain_1976"] = "Clamond"

    @model_validator(mode="after")
    def small_roughness(self):
        if self.roughness_m / self.diameter_m > 0.1:
            raise ValueError("relative roughness exceeds the supported pipe-correlation domain")
        return self


class LiquidPressureLoss(_LiquidLossGeometry):
    density_kg_m3: Positive
    viscosity_pa_s: Positive


class LiquidHeadLoss(_LiquidLossGeometry):
    kinematic_viscosity_m2_s: Positive


def _liquid_kinematic_loss(p, nu):
    """One Fluids/Darcy path; specific dissipation is energy per mass, not power."""
    from fluids.friction import friction_factor

    try:
        if p.volume_flow_m3_s == 0:
            return Calculation(
                values={
                    "velocity_m_s": 0.0,
                    "reynolds": 0.0,
                    "darcy_friction_factor": 0.0,
                    "specific_dissipation_m2_s2": 0.0,
                }
            )
        if not math.isfinite(nu) or nu <= 0:
            raise ValueError("kinematic viscosity is not positively representable")
        area = math.pi * p.diameter_m**2 / 4
        velocity = p.volume_flow_m3_s / area
        reynolds = abs(velocity) * p.diameter_m / nu
        if not math.isfinite(velocity) or not math.isfinite(reynolds):
            raise ValueError("velocity/Reynolds number exceeds finite arithmetic")
        if p.volume_flow_m3_s != 0 and (velocity == 0 or reynolds == 0):
            raise ValueError("nonzero flow velocity/Reynolds underflow is not a zero-flow state")
        if 2300 <= reynolds < 4000 and p.transition_policy == "withhold":
            return Calculation(
                values={},
                status="out_of_scope",
                notes=("Transitional flow requires an explicitly reviewed model choice.",),
            )
        fd = (
            0.0
            if reynolds == 0
            else friction_factor(reynolds, p.roughness_m / p.diameter_m, Method=p.friction_method, Darcy=True)
        )
        dissipation = (fd * p.length_m / p.diameter_m + p.minor_loss_coefficient) * velocity**2 / 2
        if not math.isfinite(fd) or fd < 0 or not math.isfinite(dissipation):
            raise ValueError("Darcy factor or specific dissipation is not finite/nonnegative")
        return Calculation(
            values={
                "velocity_m_s": velocity,
                "reynolds": reynolds,
                "darcy_friction_factor": fd,
                "specific_dissipation_m2_s2": dissipation,
            }
        )
    except (ArithmeticError, ValueError) as exc:
        return Calculation(values={}, status="out_of_scope", notes=(str(exc),))


def liquid_head_loss(p: LiquidHeadLoss):
    """Density-free head requirement; no absolute pressure, mass flow or power inferred."""
    p = LiquidHeadLoss.model_validate(p.model_dump(mode="python"))
    core = _liquid_kinematic_loss(p, p.kinematic_viscosity_m2_s)
    if core.status != "computed":
        return core
    try:
        v = core.values
        head = v["specific_dissipation_m2_s2"] / p.gravity_m_s2
        signed = math.copysign(head, v["velocity_m_s"]) if v["velocity_m_s"] else 0.0
        values = {
            "velocity_m_s": v["velocity_m_s"],
            "reynolds": v["reynolds"],
            "darcy_friction_factor": v["darcy_friction_factor"],
            "dissipative_head_loss_m": signed,
            "static_head_change_m": p.outlet_minus_inlet_elevation_m,
            "required_inlet_minus_outlet_head_m": signed + p.outlet_minus_inlet_elevation_m,
        }
        if not all(math.isfinite(value) for value in values.values()):
            raise ValueError("head mapping exceeds finite arithmetic")
        return Calculation(values=values)
    except (ArithmeticError, ValueError) as exc:
        return Calculation(values={}, status="out_of_scope", notes=(str(exc),))


def liquid_pressure_loss(p: LiquidPressureLoss):
    """Legacy pressure/work contract mapped from the shared kinematic calculation."""
    p = LiquidPressureLoss.model_validate(p.model_dump(mode="python"))
    core = _liquid_kinematic_loss(p, p.viscosity_pa_s / p.density_kg_m3)
    if core.status != "computed":
        return core
    try:
        v = core.values
        dissipative = p.density_kg_m3 * v["specific_dissipation_m2_s2"]
        signed = math.copysign(dissipative, v["velocity_m_s"]) if v["velocity_m_s"] else 0.0
        static = p.density_kg_m3 * p.gravity_m_s2 * p.outlet_minus_inlet_elevation_m
        values = {
            "velocity_m_s": v["velocity_m_s"],
            "reynolds": v["reynolds"],
            "darcy_friction_factor": v["darcy_friction_factor"],
            "dissipative_pressure_loss_pa": signed,
            "static_pressure_change_pa": static,
            "required_inlet_minus_outlet_pa": signed + static,
            "dissipated_power_w": dissipative * abs(p.volume_flow_m3_s),
        }
        if not all(math.isfinite(value) for value in values.values()):
            raise ValueError("pressure/work mapping exceeds finite arithmetic")
        return Calculation(values=values)
    except (ArithmeticError, ValueError) as exc:
        return Calculation(values={}, status="out_of_scope", notes=(str(exc),))


class Hydrostatic(Contract):
    inlet_pressure_pa_abs: Positive
    density_kg_m3: Positive
    outlet_minus_inlet_elevation_m: float
    gravity_m_s2: Positive = 9.80665


def hydrostatic(p: Hydrostatic):
    pressure = p.inlet_pressure_pa_abs - p.density_kg_m3 * p.gravity_m_s2 * p.outlet_minus_inlet_elevation_m
    if pressure <= 0:
        return Calculation(
            values={},
            status="out_of_scope",
            notes=("Predicted pressure leaves the positive absolute-state domain.",),
        )
    return Calculation(
        values={
            "outlet_pressure_pa_abs": pressure,
            "pressure_difference_pa": p.inlet_pressure_pa_abs - pressure,
        }
    )


class GasCorrelation(Contract):
    method: Literal["Weymouth", "Panhandle_A", "Panhandle_B", "Spitzglass_high"]
    relative_density_to_air: Positive
    mean_temperature_k: Positive
    mean_compressibility: Positive
    length_m: Positive
    diameter_m: Positive
    inlet_pressure_pa_abs: Positive
    outlet_pressure_pa_abs: Positive
    standard_temperature_k: Positive
    standard_pressure_pa_abs: Positive
    empirical_efficiency: Positive = Field(default=1.0, le=1)

    @model_validator(mode="after")
    def directed_pressure(self):
        if self.inlet_pressure_pa_abs <= self.outlet_pressure_pa_abs:
            raise ValueError("this directed-flow correlation requires upstream pressure above downstream")
        return self


def gas_correlation(p: GasCorrelation):
    from fluids import compressible

    function = getattr(compressible, p.method)
    q = function(
        SG=p.relative_density_to_air,
        Tavg=p.mean_temperature_k,
        Zavg=p.mean_compressibility,
        L=p.length_m,
        D=p.diameter_m,
        P1=p.inlet_pressure_pa_abs,
        P2=p.outlet_pressure_pa_abs,
        Ts=p.standard_temperature_k,
        Ps=p.standard_pressure_pa_abs,
        E=p.empirical_efficiency,
    )
    return Calculation(
        values={"standard_volume_flow_m3_s": q},
        notes=("Flow volume belongs to the explicitly supplied reference temperature and pressure.",),
    )


class IsothermalGas(Contract):
    mean_density_kg_m3: Positive
    darcy_friction_factor: Positive
    inlet_pressure_pa_abs: Positive
    outlet_pressure_pa_abs: Positive
    length_m: Positive
    diameter_m: Positive

    @model_validator(mode="after")
    def directed_pressure(self):
        if self.inlet_pressure_pa_abs <= self.outlet_pressure_pa_abs:
            raise ValueError("upstream pressure must exceed downstream pressure")
        return self


def isothermal_gas(p: IsothermalGas):
    from fluids.compressible import isothermal_gas as solve

    m = solve(
        rho=p.mean_density_kg_m3,
        fd=p.darcy_friction_factor,
        P1=p.inlet_pressure_pa_abs,
        P2=p.outlet_pressure_pa_abs,
        L=p.length_m,
        D=p.diameter_m,
    )
    return Calculation(
        values={
            "mass_flow_kg_s": m,
            "velocity_at_mean_density_m_s": m / (p.mean_density_kg_m3 * math.pi * p.diameter_m**2 / 4),
        },
        notes=("Mean density and friction are reviewed inputs, not a self-consistent thermal solver.",),
    )


class WaveSpeed(Contract):
    density_kg_m3: Positive
    fluid_bulk_modulus_pa: Positive
    pipe_elastic_modulus_pa: Positive
    inner_diameter_m: Positive
    wall_thickness_m: Positive
    restraint_coefficient: Nonnegative


def wave_speed(p: WaveSpeed):
    compliance = 1 / p.fluid_bulk_modulus_pa + p.restraint_coefficient * p.inner_diameter_m / (
        p.pipe_elastic_modulus_pa * p.wall_thickness_m
    )
    speed = math.sqrt(1 / (p.density_kg_m3 * compliance))
    return Calculation(
        values={
            "wave_speed_m_s": speed,
            "rigid_wall_sound_speed_m_s": math.sqrt(p.fluid_bulk_modulus_pa / p.density_kg_m3),
        }
    )


class SurgePulse(Contract):
    density_kg_m3: Positive
    wave_speed_m_s: Positive
    initial_velocity_m_s: float
    final_velocity_m_s: float
    length_m: Positive
    closure_time_s: Positive


def surge_pulse(p: SurgePulse):
    delta = p.density_kg_m3 * p.wave_speed_m_s * (p.initial_velocity_m_s - p.final_velocity_m_s)
    transit = 2 * p.length_m / p.wave_speed_m_s
    return Calculation(
        values={
            "instantaneous_pressure_pulse_pa": delta,
            "round_trip_time_s": transit,
            "fast_closure": p.closure_time_s <= transit,
        },
        notes=(
            "This is a simple-wave pulse scale; reflected waves, cavitation and slow closure require a transient solver.",
        ),
    )


class TrenchVolume(Contract):
    bottom_width_m: Positive
    depth_m: Positive
    side_slope_horizontal_per_vertical: Nonnegative
    length_m: Positive
    loose_volume_ratio: Positive


def trench_volume(p: TrenchVolume):
    area = (p.bottom_width_m + p.side_slope_horizontal_per_vertical * p.depth_m) * p.depth_m
    volume = area * p.length_m
    return Calculation(
        values={
            "cross_section_m2": area,
            "in_place_volume_m3": volume,
            "loose_volume_m3": volume * p.loose_volume_ratio,
        },
        notes=(
            "Specified geometry does not establish soil stability, safe shoring or excavation compliance.",
        ),
    )
