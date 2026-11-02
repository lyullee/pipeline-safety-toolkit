"""Default engineering registry and explicit quantity metadata."""

from functools import partial

from .contracts import ModelInfo
from .models import economics as ec
from .models import equipment as eq
from .models import hydraulics as hy
from .models import library_models as lm
from .models import pump_curves as pc
from .models import thermal as th
from .registry import ModelSpec, Registry

SOURCES = {
    "scipy_pchip": "https://docs.scipy.org/doc/scipy/reference/generated/scipy.interpolate.PchipInterpolator.html",
    "fluids": "https://fluids.readthedocs.io/",
    "coolprop": "https://coolprop.org/coolprop/HighLevelAPI.html",
    "fundamentals": "https://openstax.org/books/university-physics-volume-1/pages/14-introduction",
    "energy": "https://openstax.org/books/university-physics-volume-2/pages/3-introduction",
    "financial": "https://numpy.org/numpy-financial/latest/npv.html",
    "ht": "https://ht.readthedocs.io/",
}

_DIMENSIONLESS = {
    "minor_loss_coefficient",
    "empirical_efficiency",
    "relative_density_to_air",
    "mean_compressibility",
    "darcy_friction_factor",
    "restraint_coefficient",
    "side_slope_horizontal_per_vertical",
    "loose_volume_ratio",
    "pump_efficiency",
    "motor_efficiency",
    "speed_ratio",
    "geometric_diameter_ratio",
    "isentropic_efficiency",
    "discharge_coefficient",
    "reynolds",
    "diameter_ratio",
    "discount_rate_per_period",
    "prandtl",
    "relative_roughness",
    "nusselt",
    "pressure_recovery_factor",
    "valve_style_factor",
    "heat_capacity_ratio",
    "compressibility",
    "terminal_pressure_ratio_factor",
    "back_pressure_factor",
    "combination_factor",
    "parallel_pumps",
    "series_pumps",
}
_SEMANTIC = {
    "method": "selection",
    "friction_method": "selection",
    "transition_policy": "selection",
    "fluid": "fluid_identifier",
    "currency": "currency_identifier",
    "price_year": "calendar_year",
    "factor_source": "evidence_identifier",
    "cashflows": "declared_currency",
    "discounted_cashflows": "declared_currency",
    "net_present_value": "declared_currency",
    "undiscounted_sum": "declared_currency",
    "energy_cost": "declared_currency",
    "price_per_kwh": "declared_currency/kWh",
    "emission_kg_co2e_per_kwh": "kg_CO2e/kWh",
    "declared_emissions_kg_co2e": "kg_CO2e",
    "fast_closure": "bool",
}
_SEMANTIC.update(kv="Kv_conventional_m3/h", diagnostics="structured_library_diagnostics")
_SEMANTIC["reference_curve"] = "flow_head_curve_SI"
_SEMANTIC.update(segments="inventory_segments_SI", segment_results="inventory_segment_results_SI")
_SUFFIXES = sorted(
    {
        "_kg_m3": "kg/m3",
        "_s2_m5": "s2/m5",
        "_kg_s": "kg/s",
        "_m3_s": "m3/s",
        "_pa_s": "Pa*s",
        "_kg_mol": "kg/mol",
        "_m_s2": "m/s2",
        "_m_s": "m/s",
        "_pa_abs": "Pa_abs",
        "_pa": "Pa",
        "_m2_k": "m2*K",
        "_w_m2_k": "W/(m2*K)",
        "_w_m_k": "W/(m*K)",
        "_j_kg_k": "J/(kg*K)",
        "_j_kg": "J/kg",
        "_j": "J",
        "_w_k": "W/K",
        "_k_w": "K/W",
        "_w_m": "W/m",
        "_m3": "m3",
        "_m2": "m2",
        "_m": "m",
        "_kg": "kg",
        "_kwh": "kWh",
        "_per_k": "1/K",
        "_k": "K",
        "_s": "s",
        "_w": "W",
    }.items(),
    key=lambda pair: -len(pair[0]),
)


def unit_for(name):
    if name in _DIMENSIONLESS:
        return "1"
    if name in _SEMANTIC:
        return _SEMANTIC[name]
    for suffix, unit in _SUFFIXES:
        if name.endswith(suffix):
            return unit
    raise ValueError(f"no explicit quantity convention for {name}")


def default_registry(*, property_provider=None, provider_id=None, provider_version=None):
    custom_properties = property_provider is not None
    if custom_properties:
        if (
            not callable(getattr(property_provider, "resolve", None))
            or not isinstance(provider_id, str)
            or not provider_id.strip()
            or not isinstance(provider_version, str)
            or not provider_version.strip()
        ):
            raise ValueError("a custom property provider requires resolve(), a provider ID and version")
    elif provider_id is not None or provider_version is not None:
        raise ValueError("provider identity belongs to an explicitly supplied provider")
    registry = Registry()
    records = [
        (
            "geometry.pipe",
            "Pipe flow and wall geometry",
            "geometry",
            hy.PipeGeometry,
            hy.pipe_geometry,
            ("flow_area_m2", "internal_volume_m3", "wall_volume_m3", "wall_mass_kg", "external_area_m2"),
            None,
            "fundamentals",
            ("Circular uniform pipe geometry and declared wall density.",),
        ),
        (
            "hydraulics.liquid_loss",
            "Signed liquid pressure and energy budget",
            "hydraulics",
            hy.LiquidPressureLoss,
            hy.liquid_pressure_loss,
            (
                "velocity_m_s",
                "reynolds",
                "darcy_friction_factor",
                "dissipative_pressure_loss_pa",
                "static_pressure_change_pa",
                "required_inlet_minus_outlet_pa",
                "dissipated_power_w",
            ),
            "fluids",
            "fluids",
            (
                "Steady single-phase constant-property flow; no cavitation or two-phase closure.",
                "Darcy convention; transition treatment is explicit.",
            ),
        ),
        (
            "hydraulics.liquid_head_loss",
            "Signed liquid hydraulic head demand without density",
            "hydraulics",
            hy.LiquidHeadLoss,
            hy.liquid_head_loss,
            (
                "velocity_m_s",
                "reynolds",
                "darcy_friction_factor",
                "dissipative_head_loss_m",
                "static_head_change_m",
                "required_inlet_minus_outlet_head_m",
            ),
            "fluids",
            "fluids",
            (
                "Steady single-phase Newtonian kinematic-viscosity input; no density or absolute pressure is inferred.",
                "Darcy convention; transition treatment is explicit and shared with the pressure-loss owner.",
                "The result is a required head difference, not an absolute pressure state or measured sensor mapping.",
            ),
        ),
        (
            "hydraulics.hydrostatic",
            "Hydrostatic absolute pressure",
            "hydraulics",
            hy.Hydrostatic,
            hy.hydrostatic,
            ("outlet_pressure_pa_abs", "pressure_difference_pa"),
            None,
            "fundamentals",
            ("Constant-density stationary fluid under declared gravity.",),
        ),
        (
            "hydraulics.gas_correlation",
            "Selectable empirical gas pipeline correlation",
            "hydraulics",
            hy.GasCorrelation,
            hy.gas_correlation,
            ("standard_volume_flow_m3_s",),
            "fluids",
            "fluids",
            (
                "Empirical directed gas flow with declared mean temperature, compressibility, efficiency and reference conditions.",
                "The selected correlation's original regime must be reviewed for the target gas and pipe.",
            ),
        ),
        (
            "hydraulics.isothermal_gas",
            "Isothermal gas mass-flow calculation",
            "hydraulics",
            hy.IsothermalGas,
            hy.isothermal_gas,
            ("mass_flow_kg_s", "velocity_at_mean_density_m_s"),
            "fluids",
            "fluids",
            ("Steady horizontal isothermal gas; supplied average density and friction.",),
        ),
        (
            "transient.wave_speed",
            "Elastic-pipe liquid wave speed",
            "transient",
            hy.WaveSpeed,
            hy.wave_speed,
            ("wave_speed_m_s", "rigid_wall_sound_speed_m_s"),
            None,
            "fundamentals",
            ("Linear small-disturbance fluid and wall compliance; caller supplies restraint coefficient.",),
        ),
        (
            "transient.surge_pulse",
            "Simple-wave pressure pulse and closure timescale",
            "transient",
            hy.SurgePulse,
            hy.surge_pulse,
            ("instantaneous_pressure_pulse_pa", "round_trip_time_s", "fast_closure"),
            None,
            "fundamentals",
            ("Joukowsky pulse scale in a uniform liquid line; not a reflected-wave transient simulation.",),
        ),
        (
            "construction.trench_volume",
            "Declared trapezoidal excavation volume",
            "construction",
            hy.TrenchVolume,
            hy.trench_volume,
            ("cross_section_m2", "in_place_volume_m3", "loose_volume_m3"),
            None,
            "fundamentals",
            (
                "Uniform declared trench section; soil stability and excavation protections are external requirements.",
            ),
        ),
        (
            "equipment.pump_power",
            "Pump hydraulic, shaft and electrical power",
            "equipment",
            eq.PumpPower,
            eq.pump_power,
            ("hydraulic_power_w", "shaft_power_w", "electrical_power_w"),
            None,
            "energy",
            ("Steady liquid flow and independently declared pump/motor efficiencies.",),
        ),
        (
            "equipment.pump_affinity",
            "Similar-pump operating-point scaling",
            "equipment",
            eq.PumpAffinity,
            eq.pump_affinity,
            ("flow_m3_s", "head_m", "power_w"),
            None,
            "fluids",
            ("Geometrically similar pumps with unchanged efficiency; not a generic impeller-trim model.",),
        ),
        (
            "equipment.pump_npsh",
            "Available suction head and declared vendor margin",
            "equipment",
            eq.PumpNPSH,
            eq.pump_npsh,
            ("available_npsh_m", "declared_margin_m"),
            None,
            "fundamentals",
            (
                "Steady suction energy budget with a declared surface pressure, vapour pressure and vendor NPSH.",
            ),
        ),
        (
            "equipment.compression",
            "Real-fluid isentropic-reference compressor",
            "equipment",
            eq.Compression,
            eq.compression,
            ("specific_work_j_kg", "shaft_power_w", "outlet_temperature_k", "outlet_enthalpy_j_kg"),
            "CoolProp",
            "coolprop",
            ("Steady adiabatic single-phase compression with declared isentropic efficiency.",),
        ),
        (
            "instrument.liquid_dp_meter",
            "Liquid differential-pressure flow meter",
            "instrument",
            eq.LiquidMeter,
            eq.liquid_meter,
            ("volume_flow_m3_s", "mass_flow_kg_s", "diameter_ratio"),
            None,
            "fundamentals",
            (
                "Incompressible single-phase flow with externally reviewed discharge coefficient and geometry.",
            ),
        ),
        (
            "inventory.uniform",
            "Uniform real-fluid inventory and reference volume",
            "inventory",
            th.Inventory,
            th.inventory,
            ("density_kg_m3", "mass_kg", "reference_volume_m3", "specific_enthalpy_j_kg"),
            "CoolProp",
            "coolprop",
            (
                "Uniform single-phase actual and reference states; no unknown phase or stratification inferred.",
            ),
        ),
        (
            "thermal.flow_cooling",
            "Constant-property flow thermal relaxation",
            "thermal",
            th.Cooling,
            th.cooling,
            ("outlet_temperature_k", "heat_loss_w"),
            None,
            "energy",
            ("Constant ambient, heat capacity and total conductance without phase change.",),
        ),
        (
            "thermal.free_expansion",
            "Unrestrained linear thermal expansion",
            "thermal",
            th.Expansion,
            th.expansion,
            ("free_length_change_m", "operating_length_m"),
            None,
            "energy",
            ("Small linear unrestrained expansion with a declared coefficient.",),
        ),
        (
            "economics.cashflow",
            "Declared-period discounted cashflow",
            "economics",
            ec.DiscountedCashflow,
            ec.discounted_cashflow,
            ("net_present_value", "discounted_cashflows", "undiscounted_sum"),
            None,
            "financial",
            (
                "Explicit elapsed times or uniform periods with time-zero first cashflow; no cost-year or currency conversion.",
            ),
        ),
        (
            "economics.energy",
            "Operational energy, cost and declared emissions",
            "economics",
            ec.EnergyBudget,
            ec.energy_budget,
            ("energy_kwh", "energy_cost", "declared_emissions_kg_co2e"),
            None,
            "energy",
            (
                "Supplied duty, signed tariff and emissions factor; unknown factors remain null; not a lifecycle result.",
            ),
        ),
        (
            "thermal.internal_convection",
            "Selectable internal-flow convection correlation",
            "thermal",
            lm.InternalConvection,
            lm.internal_convection,
            ("nusselt", "coefficient_w_m2_k"),
            "ht",
            "ht",
            ("Single-phase internal flow with independently checked thermal boundary and regime.",),
        ),
        (
            "equipment.control_valve_liquid",
            "Installed liquid control-valve coefficient",
            "equipment",
            lm.LiquidControlValve,
            lm.liquid_control_valve,
            ("kv", "diagnostics"),
            "fluids",
            "fluids",
            ("Library sizing correlation with explicitly supplied manufacturer factors and diameters.",),
        ),
        (
            "equipment.control_valve_gas",
            "Installed gas control-valve coefficient",
            "equipment",
            lm.GasControlValve,
            lm.gas_control_valve,
            ("kv", "diagnostics"),
            "fluids",
            "fluids",
            ("Gas reference volume at 273.15 K and 101325 Pa; explicit manufacturer factors.",),
        ),
        (
            "equipment.relief_gas",
            "Gas relief-area correlation adapter",
            "equipment",
            lm.ReliefGas,
            lm.relief_gas,
            ("required_area_m2",),
            "fluids",
            "fluids",
            ("Single-phase gas correlation; declared coefficients, back pressure and relieving load.",),
        ),
    ]
    records.append(
        (
            "equipment.pump_system",
            "Identical-pump and equivalent-system operating point",
            "equipment",
            pc.PumpSystem,
            pc.pump_system,
            (
                "volume_flow_m3_s",
                "total_head_m",
                "flow_per_parallel_branch_m3_s",
                "head_per_series_stage_m",
                "head_intersection_residual_m",
                "hydraulic_power_w",
                "shaft_power_w",
                "electrical_power_w",
            ),
            "scipy",
            "scipy_pchip",
            (
                "Caller-owned monotone pump curve and static-plus-quadratic system head.",
                "Identical series/parallel pumps and declared constant efficiencies.",
            ),
        )
    )
    records.append(
        (
            "inventory.segmented",
            "Spatially declared real-fluid inventory integration",
            "inventory",
            th.SegmentedInventory,
            th.segmented_inventory,
            (
                "total_mass_kg",
                "total_internal_volume_m3",
                "reference_volume_m3",
                "mass_weighted_enthalpy_j_kg",
                "total_internal_energy_j",
                "segment_results",
            ),
            "CoolProp",
            "coolprop",
            (
                "Caller supplies resolved spatial segments and a common fluid/property reference.",
                "Every state is individually single phase; no transport or interface motion is inferred.",
            ),
        )
    )
    for identity, title, category, inputs, function, outputs, dependency, source, assumptions in records:
        sources = (SOURCES[source],)
        custom_model = custom_properties and dependency == "CoolProp"
        if custom_model:
            from .thermo import CheckedProvider

            function = partial(function, property_provider=CheckedProvider(property_provider))
            dependency = None
            sources = (f"user_provider:{provider_id}:{provider_version}",)
            assumptions = assumptions + (
                f"User-supplied property provider {provider_id}, version {provider_version}.",
            )
        info = ModelInfo(
            id=identity,
            version="2" if identity in {"economics.cashflow", "economics.energy"} else "1",
            title=title,
            category=category,
            input_units={name: unit_for(name) for name in inputs.model_fields},
            output_units={name: unit_for(name) for name in outputs},
            sources=sources,
            assumptions=assumptions,
            dependency=dependency,
            implementation="user_model" if custom_model else "library_adapter" if dependency else "native",
            qualification="user_model" if custom_model else "engineering_screening",
        )
        registry.register(ModelSpec(info, inputs, function))
    return registry
