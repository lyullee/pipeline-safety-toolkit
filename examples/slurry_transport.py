"""Authored homogeneous Bingham example; no measured settling/erosion claim."""

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.slurry_transport import (
    REVIEWS,
    SlurryPropertySnapshot,
    SlurryTransportRequest,
    boundary_metadata,
    evaluate_slurry_transport,
    native_backend_metadata,
)


def make_case(*, reviewed=False, **changes):
    evidence = "authored_slurry_snapshot"
    properties = SlurryPropertySnapshot(
        provider_id="authored_constant_rheology",
        provider_version="1",
        source_sha256=digest({"authored": "synthetic slurry snapshot v1"}),
        reference_basis="snapshot mixture density and SI constitutive coefficients; no enthalpy EOS",
        rheology="bingham",
        consistency_Pa_s_n=0.1,
        flow_index=1.0,
        yield_stress_Pa=5.0,
        mixture_density_kg_m3=1100.0,
        temperature_K=300.0,
        temperature_support_K=(290.0, 310.0),
        maximum_shear_rate_s_inv=10000.0,
        maximum_shear_stress_Pa=1000.0,
        solid_volume_fraction=0.1,
        concentration_support=(0.0, 0.2),
        particle_diameter_m=1e-5,
        maximum_particle_to_pipe_diameter_ratio=0.01,
        carrier_density_kg_m3=1000.0,
        particle_density_kg_m3=2000.0,
        maximum_density_mixing_error_kg_m3=1e-8,
        homogeneous_condition="supported",
        settling_condition="supported_homogeneous_operating_window",
        supported_absolute_velocity_m_s=(0.0, 1.0),
        flow_regime="laminar_supplied",
        evidence_id=evidence,
    )
    fields = dict(
        id="slurry_transport",
        asset_id="line_01",
        event_id="authored_homogeneous_flow",
        clock_id="plant_s",
        time_s=0.0,
        inlet_port_id="in",
        outlet_port_id="out",
        inlet_pressure_pa_abs=1000000.0,
        volume_flow_m3_s=0.0001,
        length_m=10.0,
        inner_diameter_m=0.05,
        roughness_m=0.0,
        minor_loss_coefficient=0.0,
        outlet_minus_inlet_elevation_m=0.0,
        gravity_m_s2=9.80665,
        properties=properties,
        geometry_evidence_id=evidence,
        maximum_wall_stress_bracket_Pa=1000.0,
        evidence_ids=(evidence,),
        reviews=dict.fromkeys(REVIEWS, reviewed),
        review_evidence_ids=dict.fromkeys(REVIEWS, evidence) if reviewed else {},
        owner_pins=native_backend_metadata() if reviewed else {},
    )
    fields.update(changes)
    q = SlurryTransportRequest(**fields)
    c = RunContext(
        asset_id=q.asset_id,
        scenario_id=q.event_id,
        clock_id=q.clock_id,
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id=evidence,
                citation="Authored homogeneous rheology test boundary",
                sha256=q.properties.source_sha256,
                origin="synthetic",
                limitations=(
                    "Support flags are authored test assumptions, not measurements or deposition predictions",
                ),
            ),
        ),
        metadata={"slurry_transport_boundary": boundary_metadata(q)},
    )
    return q, c


if __name__ == "__main__":
    import json

    print(json.dumps(evaluate_slurry_transport(*make_case()), indent=2))
