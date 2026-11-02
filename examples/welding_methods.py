"""Original SI welding indicators and histories; no procedure or supplier coefficients are adopted."""

from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame
from pipeline_integrity.welding_exchange import REVIEWS, WeldingRequest, WeldThermalSpec, WeldWaveformSpec


def make_cases():
    context = RunContext(
        asset_id="authored_joint",
        scenario_id="authored_weld_indicators",
        clock_id="elapsed_s",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="reference",
                citation="Original hypothetical weld histories, assay fractions and declared geometry/rules",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    base = dict(
        asset_id=context.asset_id,
        location_id="declared_joint",
        clock_id=context.clock_id,
        material_condition_id="authored_material_condition",
        evidence_ids=("reference",),
    )
    params = {
        "welding.arc_energy": dict(voltage_V=20.0, current_A=100.0, travel_speed_m_s=0.005, efficiency=0.8),
        "welding.waveform_energy": dict(
            time_s=[0.0, 1.0, 2.0],
            voltage_V=[10.0, 30.0, 10.0],
            current_A=[30.0, 10.0, 30.0],
            travel_distance_m=0.01,
            efficiency=0.8,
        ),
        "welding.carbon_equivalents": dict(
            C=0.1, Mn=1.2, Cr=0.2, Mo=0.1, V=0.05, Ni=0.3, Cu=0.15, Si=0.2, B=0.001
        ),
        "welding.cooling_interval": dict(
            time_s=[0.0, 10.0, 20.0],
            temperature_K=[1200.0, 1000.0, 700.0],
            upper_temperature_K=1100.0,
            lower_temperature_K=800.0,
        ),
        "welding.heat_treatment_history": dict(
            time_s=[0.0, 10.0, 20.0, 30.0, 40.0, 50.0],
            sensor_temperature_K=[
                [700.0, 850.0, 850.0, 950.0, 850.0, 700.0],
                [700.0, 800.0, 850.0, 900.0, 850.0, 700.0],
            ],
            minimum_hold_temperature_K=800.0,
            maximum_hold_temperature_K=900.0,
        ),
        "welding.dilution": dict(
            constituents=[
                dict(
                    material_id="base",
                    role="base",
                    melted_mass_kg=0.002,
                    composition_mass_fraction=dict(C=0.01, Cr=0.1, Ni=0.0),
                ),
                dict(
                    material_id="filler",
                    role="filler",
                    melted_mass_kg=0.001,
                    composition_mass_fraction=dict(C=0.02, Cr=0.2, Ni=0.1),
                ),
                dict(
                    material_id="previous",
                    role="previous_layer",
                    melted_mass_kg=0.001,
                    composition_mass_fraction=dict(C=0.03, Cr=None, Ni=0.2),
                ),
            ],
            requested_elements=["C", "Cr", "Ni"],
        ),
        "welding.hardness_traverse": dict(
            method_id="authored_declared_method",
            test_force_N=9.80665,
            dwell_s=10.0,
            uncertainty_coverage_description="Original hypothetical expanded bounds; no standard rule",
            points=[
                dict(point_id=key, position_m=x, region_id=region, hardness_Pa=h, expanded_uncertainty_Pa=u)
                for key, x, region, h, u in (
                    ("B", -0.01, "base", 1.5e9, 0.1e9),
                    ("H", 0.0, "HAZ", 3.1e9, 0.2e9),
                    ("W", 0.01, "weld", 4.5e9, 0.1e9),
                )
            ],
            declared_region_upper_limits_Pa=dict(base=2e9, HAZ=3e9, weld=4e9, unmeasured=5e9),
        ),
    }
    positions = [-0.03, -0.02, -0.01, 0.01, 0.02, 0.03]
    params["welding.joint_profile"] = dict(
        transverse_position_m=positions,
        measured_surface_height_m=[0.001 + 0.01 * x if x < 0 else 0.003 + 0.02 * x for x in positions],
        excluded_joint_half_width_m=0.005,
        fit_method="ols",
    )
    cases = {
        name: WeldingRequest(
            method=name,
            parameters=p,
            maximum_sample_interval_s=10.0 if "time_s" in p else None,
            chemistry_basis="mass_percent"
            if name == "welding.carbon_equivalents"
            else "mass_fraction"
            if name == "welding.dilution"
            else None,
            applicability_reviews=dict.fromkeys(REVIEWS[name], True),
            **base,
        )
        for name, p in params.items()
    }
    wave = tuple(
        SignalFrame(
            stream_id="authored_wave",
            clock_id=context.clock_id,
            time_s=t,
            values=dict(U=u, I=i),
            units=dict(U="V", I="A"),
            valid=dict(U=True, I=True),
            origin="synthetic",
        )
        for t, u, i in zip(
            params["welding.waveform_energy"]["time_s"],
            params["welding.waveform_energy"]["voltage_V"],
            params["welding.waveform_energy"]["current_A"],
            strict=True,
        )
    )
    wave_spec = WeldWaveformSpec(
        **base,
        voltage_channel="U",
        current_channel="I",
        maximum_sample_interval_s=1.0,
        travel_distance_m=0.01,
        efficiency=0.8,
        electrical_synchronization_and_polarity_reviewed=True,
        distance_efficiency_and_window_reviewed=True,
        electrical_evidence_id="reference",
    )
    thermal = tuple(
        SignalFrame(
            stream_id="authored_thermal",
            clock_id=context.clock_id,
            time_s=t,
            values=dict(T=temperature),
            units=dict(T="K"),
            valid=dict(T=True),
            origin="synthetic",
        )
        for t, temperature in zip(
            params["welding.cooling_interval"]["time_s"],
            params["welding.cooling_interval"]["temperature_K"],
            strict=True,
        )
    )
    thermal_spec = WeldThermalSpec(
        **base,
        kind="cooling",
        temperature_channels=("T",),
        lower_temperature_k=800.0,
        upper_temperature_k=1100.0,
        maximum_sample_interval_s=10.0,
        sensor_locations_temperature_and_rule_reviewed=True,
    )
    return cases, wave, wave_spec, thermal, thermal_spec, context
