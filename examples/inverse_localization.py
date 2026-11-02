"""Authored finite predictions demonstrate the provider boundary, not a physical leak model."""

from pathlib import Path

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.inverse import Prediction
from pipeline_integrity.inverse_localization import (
    REVIEWS,
    LocalizationRequest,
    LocalizationRow,
    LocationCandidate,
    boundary_metadata,
    prediction_binding,
)
from pipeline_integrity.metrology import MeasurementBasis


def make_case(*, reviewed=False):
    basis = MeasurementBasis(
        quantity="pressure",
        unit="Pa",
        definition_id="synthetic_absolute_pressure",
        evidence_id="authored",
        pressure_reference="absolute",
    )
    q = LocalizationRequest(
        id="authored_localization",
        asset_id="pipe",
        clock_id="clock",
        event_id="event",
        rows=tuple(
            LocalizationRow(
                id=f"row_{i}",
                channel_id=f"pressure_{i}",
                sensor_id=f"sensor_{i}",
                physical_port_id=f"port_{i}",
                time_s=0.0,
                basis=basis,
                value=v,
                valid=True,
            )
            for i, v in enumerate((100100.0, 100200.0))
        ),
        candidates=tuple(
            LocationCandidate(
                id=name,
                location_id="location_" + name,
                coordinate_frame_id="local_xyz",
                position_m=(float(i) * 100.0, 0.0, 0.0),
                physical_port_id="leak_" + name,
                parameters={},
                evidence_id="authored",
            )
            for i, name in enumerate(("a", "b", "c"))
        ),
        parameter_bases={},
        covariance=((10000.0, 5000.0), (5000.0, 10000.0)),
        covariance_row_ids=("row_0", "row_1"),
        noise_law="common_candidate_independent_gaussian",
        provider_id="authored_table",
        provider_version="1",
        provider_source_sha256=file_digest(Path(__file__)),
        provider_configuration_sha256=digest("authored fixed prediction table v1"),
        observation_source_sha256=digest("authored illustrative observations"),
        evidence_id="authored",
        reviews={k: reviewed for k in REVIEWS},
        review_evidence_ids={k: "authored" for k in REVIEWS},
    )
    c = RunContext(
        asset_id="pipe",
        scenario_id="conditional_leak_candidates",
        clock_id="clock",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Original illustrative table; no field qualification",
                origin="synthetic",
            ),
        ),
        metadata={"inverse_localization_boundary": boundary_metadata(q)},
    )
    return q, c, AuthoredTable(q)


class AuthoredTable:
    def __init__(self, request):
        self.request = request
        self.id = request.provider_id
        self.version = request.provider_version
        self.source_sha256 = request.provider_source_sha256
        self.configuration_sha256 = request.provider_configuration_sha256

    def __call__(self, candidate, time_s, context):
        values = {"a": (100100.0, 100200.0), "b": (100200.0, 100100.0), "c": (100100.0, 100200.0)}[
            candidate.id
        ]
        p = Prediction(
            status="computed",
            asset_id=context.asset_id,
            clock_id=context.clock_id,
            time_s=time_s,
            values={f"pressure_{i}": tuple(v for _ in time_s) for i, v in enumerate(values)},
            units={"pressure_0": "Pa", "pressure_1": "Pa"},
            provider_source_digest=self.source_sha256,
            evaluation_digest="0" * 64,
            diagnostics={"origin": "authored synthetic table"},
        )
        return p.model_copy(
            update={"evaluation_digest": prediction_binding(self.request, candidate, context, p)}
        )
