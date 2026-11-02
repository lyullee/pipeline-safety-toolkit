"""Authored envelope and operating samples; not hydrate/wax material calibration."""

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.deposition_risk import (
    REVIEWS,
    DepositionBasis,
    DepositionEnvelope,
    DepositionObservation,
    DepositionRequest,
    EnvelopeKnot,
    boundary_metadata,
    envelope_binding,
    envelope_payload_sha256,
)


def make_case(*, reviewed=False):
    basis = DepositionBasis(
        composition_mole_fractions={"C1": 0.9999, "H2O": 0.0001},
        composition_definition_id="authored_total_molar_composition",
        water_activity_mode="native_composition_model",
        water_activity=None,
        water_activity_definition_id="authored_envelope_fixed_basis",
        inhibitor_mass_fraction=0.0,
        inhibitor_definition_id="no_inhibitor_present",
        wax_characterization_sha256=None,
        evidence_id="authored",
    )
    envelope = DepositionEnvelope(
        status="computed",
        phenomenon="hydrate",
        parameter_basis_sha256=digest(basis),
        provider_id="authored_envelope",
        provider_version="1",
        provider_source_sha256=digest("authored synthetic envelope"),
        provider_configuration_sha256=digest("authored fixed pressure-temperature boundary"),
        interpolation="piecewise_linear_temperature_in_pressure",
        stability_side="below_boundary_temperature",
        boundary_uncertainty_basis_id="authored_plus_minus_one_kelvin",
        knots=(
            EnvelopeKnot(
                pressure_pa_abs=1e6, boundary_temperature_lower_k=269.0, boundary_temperature_upper_k=271.0
            ),
            EnvelopeKnot(
                pressure_pa_abs=2e6, boundary_temperature_lower_k=279.0, boundary_temperature_upper_k=281.0
            ),
        ),
        native_receipt={"origin": "authored synthetic boundary; no physical solver executed"},
        evaluation_sha256="0" * 64,
    )
    q = DepositionRequest(
        id="authored_deposition",
        asset_id="pipe",
        location_id="cold_section",
        physical_port_id="wall_sensor",
        clock_id="process_clock",
        source_origin="synthetic",
        phenomenon="hydrate",
        basis=basis,
        observations=tuple(
            DepositionObservation(
                id=f"row_{i}",
                time_s=float(i),
                pressure_bounds_pa_abs=(1.5e6, 1.5e6),
                temperature_bounds_k=(temperature, temperature),
                valid=True,
                evidence_id="authored",
            )
            for i, temperature in enumerate((280.0, 270.0, 270.0, 270.0))
        ),
        source_mode="reviewed_envelope",
        provider_id=envelope.provider_id,
        provider_version=envelope.provider_version,
        provider_source_sha256=envelope.provider_source_sha256,
        provider_configuration_sha256=envelope.provider_configuration_sha256,
        expected_envelope_payload_sha256=envelope_payload_sha256(envelope),
        observation_source_sha256=digest("authored process samples"),
        maximum_sample_gap_s=1.0,
        minimum_sampled_window_s=2.0,
        maximum_interpolation_pressure_gap_pa=1e6,
        reviews={k: reviewed for k in REVIEWS},
        review_evidence_ids={k: "authored" for k in REVIEWS},
        evidence_id="authored",
    )
    c = RunContext(
        asset_id=q.asset_id,
        scenario_id="authored_envelope_comparison",
        clock_id=q.clock_id,
        origin=q.source_origin,
        evidence=(
            EvidenceRef(
                id="authored", citation="Original synthetic boundary and observations", origin="synthetic"
            ),
        ),
        metadata={"deposition_boundary": boundary_metadata(q)},
    )
    envelope = envelope.model_copy(update={"evaluation_sha256": envelope_binding(q, c, envelope)})
    return q, c, envelope
