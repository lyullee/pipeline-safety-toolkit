"""Original pressure-test references; supplied factors and volumes are hypothetical."""

from pipeline_integrity.contracts import EvidenceRef, RunContext, SignalFrame
from pipeline_integrity.pressure_tests import REVIEWS, GasDecaySpec, PressureTestRequest


def make_cases():
    context = RunContext(
        asset_id="authored_test_volume",
        scenario_id="authored_pressure_test",
        clock_id="elapsed_s",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="reference",
                citation="Original hypothetical SI pressure-test rules, gas states and isolated-volume assumptions",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    base = dict(
        asset_id=context.asset_id,
        location_id="gauge_and_chamber",
        clock_id=context.clock_id,
        evidence_ids=("reference",),
    )
    params = {
        "inspection.pressure_test_load": dict(
            design_pressure_Pa=1e6,
            declared_test_factor=1.5,
            allowable_at_test_Pa=150e6,
            allowable_at_design_Pa=100e6,
            fluid_density_kg_m3=1000.0,
            elevation_below_gauge_m=10.0,
        ),
        "inspection.gas_expansion_energy": dict(
            initial_absolute_pressure_Pa=2e5,
            final_absolute_pressure_Pa=1e5,
            initial_volume_m3=0.1,
            heat_capacity_ratio=1.4,
        ),
        "inspection.gas_pressure_decay": dict(
            rigid_volume_m3=0.1,
            initial_absolute_pressure_Pa=1e5,
            final_absolute_pressure_Pa=9e4,
            initial_temperature_K=300.0,
            final_temperature_K=300.0,
            initial_compressibility_factor=1.0,
            final_compressibility_factor=1.0,
            duration_s=10.0,
            reference_pressure_Pa=1e5,
            reference_temperature_K=300.0,
            reference_compressibility_factor=1.0,
        ),
        "inspection.vacuum_rise": dict(
            rigid_volume_m3=0.01,
            initial_absolute_pressure_Pa=1.0,
            final_absolute_pressure_Pa=3.0,
            duration_s=100.0,
            background_throughput_Pa_m3_s=0.0003,
            constant_temperature_declared=True,
        ),
    }
    requests = {
        key: PressureTestRequest(
            method=key, parameters=value, applicability_reviews=dict.fromkeys(REVIEWS[key], True), **base
        )
        for key, value in params.items()
    }
    frames = tuple(
        SignalFrame(
            stream_id="authored_gas",
            clock_id=context.clock_id,
            time_s=t,
            values=dict(p=p, T=300.0),
            units=dict(p="Pa", T="K"),
            valid=dict(p=True, T=True),
            origin="synthetic",
        )
        for t, p in ((0.0, 1e5), (5.0, 9.5e4), (10.0, 9e4))
    )
    spec = GasDecaySpec(
        **base,
        pressure_channel="p",
        temperature_channel="T",
        pressure_reference="absolute",
        rigid_volume_m3=0.1,
        reference_pressure_pa_abs=1e5,
        reference_temperature_k=300.0,
        compressibility_source="declared_constant",
        compressibility_factor=1.0,
        reference_compressibility_factor=1.0,
        maximum_interval_s=5.0,
        gas_temperature_representative_reviewed=True,
        isolated_rigid_fixed_composition_reviewed=True,
    )
    return requests, frames, spec, context
