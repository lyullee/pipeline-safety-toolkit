"""Authored gas-sensor quality records; no field detection performance is implied."""

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.external_sensing import (
    REVIEWS,
    ExternalAcquisition,
    ExternalChannel,
    ExternalReading,
    ExternalSensingRequest,
    SensorFootprint,
    boundary_metadata,
)
from pipeline_integrity.metrology import MeasurementBasis


def make_case(*, reviewed=False):
    channel = ExternalChannel(
        id="methane_local",
        sensor_id="detector_a",
        modality="gas_detector",
        basis=MeasurementBasis(
            quantity="mole_fraction",
            unit="1",
            definition_id="local_dry_methane_fraction",
            evidence_id="authored",
        ),
        footprint=SensorFootprint(
            id="inlet",
            coordinate_frame_id="station_local_xyz",
            kind="point",
            vertices_m=((2.0, 0.0, 1.0),),
            asset_port_ids=("sampling_inlet",),
            registration_evidence_id="authored",
            spatial_error_bound_m=0.1,
        ),
        species_id="methane_dry",
        detection_limit=1e-5,
        detection_rule="upper_one_sided",
        saturation_lower=None,
        saturation_upper=0.05,
        instrument_configuration_sha256=digest("authored detector configuration"),
        evidence_id="authored",
    )
    observations = (("quantified", 0.001), ("non_detect", None), ("saturated_upper", 0.05), ("missing", None))
    q = ExternalSensingRequest(
        id="authored_external",
        asset_id="station",
        stream_id="gas_external",
        clock_id="station_clock",
        source_clock_id="instrument_clock",
        source_to_analysis_offset_s=10.0,
        clock_error_bound_s=0.01,
        clock_mapping_evidence_id="authored",
        origin="synthetic",
        source_sha256=digest(observations),
        processing_source_sha256=digest("authored direct readings"),
        processing_configuration_sha256=digest("no retrieval algorithm"),
        channels=(channel,),
        acquisitions=tuple(
            ExternalAcquisition(
                id=f"acquisition_{i}",
                time_s=float(i),
                exposure_start_s=float(i),
                exposure_end_s=float(i),
                readings=(
                    ExternalReading(
                        channel_id=channel.id,
                        state=state,
                        value=value,
                        source_row_id=f"original_row_{i}",
                        quality_evidence_id="authored",
                    ),
                ),
            )
            for i, (state, value) in enumerate(observations)
        ),
        reviews={k: reviewed for k in REVIEWS},
        review_evidence_ids={k: "authored" for k in REVIEWS},
        evidence_id="authored",
    )
    c = RunContext(
        asset_id=q.asset_id,
        scenario_id="authored_quality_states",
        clock_id=q.clock_id,
        origin=q.origin,
        evidence=(
            EvidenceRef(id="authored", citation="Original illustrative sensor states", origin="synthetic"),
        ),
        metadata={"external_sensing_boundary": boundary_metadata(q)},
    )
    return q, c
