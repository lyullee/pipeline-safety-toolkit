"""Independent exact 1-D travel-time qualification for the finite inverse evaluator."""

from dataclasses import dataclass
from pathlib import Path

import pytest

from pipeline_integrity.contracts import digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.inverse import Prediction
from pipeline_integrity.inverse_localization import (
    REVIEWS,
    LocalizationRequest,
    LocalizationRow,
    LocationCandidate,
    boundary_metadata,
    localize_leak,
    prediction_binding,
)
from pipeline_integrity.metrology import MeasurementBasis


ROOT = Path(__file__).parents[1]
TEST_SHA = file_digest(Path(__file__))
OBS_BASIS = MeasurementBasis(
    quantity="time", unit="s", definition_id="sensor_arrival_time", evidence_id="analytic"
)
SPEED_BASIS = MeasurementBasis(
    quantity="velocity", unit="m/s", definition_id="homogeneous_wave_speed", evidence_id="analytic"
)


@dataclass
class ExactTravelTimeProvider:
    """Homogeneous 1-D two-sided first-arrival oracle: t_i = |x-x_i|/c."""

    request: LocalizationRequest
    sensor_x: dict[str, float]

    def __post_init__(self):
        self.id = self.request.provider_id
        self.version = self.request.provider_version
        self.source_sha256 = TEST_SHA
        self.configuration_sha256 = digest({"sensor_x": self.sensor_x, "formula": "abs(x-xs)/c"})

    def __call__(self, candidate, time_s, context):
        x = candidate.position_m[0]
        c = candidate.parameters["wave_speed"]
        arrivals = {ch: abs(x - sx) / c for ch, sx in self.sensor_x.items()}
        values = {
            row.channel_id: tuple(arrivals[row.channel_id] for _ in time_s)
            for row in self.request.rows
        }
        pred = Prediction(
            status="computed",
            asset_id=context.asset_id,
            clock_id=context.clock_id,
            time_s=time_s,
            values=values,
            units={ch: "s" for ch in values},
            provider_source_digest=self.source_sha256,
            evaluation_digest="0" * 64,
            diagnostics={"oracle": "homogeneous straight 1-D first arrival"},
        )
        return pred.model_copy(update={"evaluation_digest": prediction_binding(self.request, candidate, context, pred)})


def make_case(*, scale=1.0, sensors=(0.0, 100.0), source_x=20.0, candidates=(20.0, 50.0, 80.0)):
    sensor_x = {f"arrival_{i}": sx * scale for i, sx in enumerate(sensors)}
    speeds = {"wave_speed": 10.0 * scale}
    rows = tuple(
        LocalizationRow(
            id=f"row_{i}", channel_id=f"arrival_{i}", sensor_id=f"sensor_{i}",
            physical_port_id=f"port_{i}", time_s=0.0, basis=OBS_BASIS,
            value=abs(source_x * scale - sensor_x[f"arrival_{i}"]) / speeds["wave_speed"], valid=True,
        )
        for i in range(len(sensors))
    )
    candidate_rows = tuple(
        LocationCandidate(
            id=f"x_{int(x)}", location_id=f"loc_{int(x)}", coordinate_frame_id="pipe_axis_m",
            position_m=(x * scale, 0.0, 0.0), physical_port_id=f"leak_{int(x)}",
            parameters=speeds, evidence_id="analytic",
        )
        for x in candidates
    )
    q = LocalizationRequest(
        id="analytic_1d", asset_id="pipe", clock_id="synchronized", event_id="event",
        rows=rows, candidates=candidate_rows, parameter_bases={"wave_speed": SPEED_BASIS},
        covariance=tuple(tuple(1e-4 if i == j else 0.0 for j in range(len(rows))) for i in range(len(rows))),
        covariance_row_ids=tuple(r.id for r in rows), noise_law="common_candidate_independent_gaussian",
        provider_id="exact_1d_oracle", provider_version="1", provider_source_sha256=TEST_SHA,
        provider_configuration_sha256=digest({"sensor_x": sensor_x, "formula": "abs(x-xs)/c"}),
        observation_source_sha256=digest({"source_x": source_x * scale, "sensor_x": sensor_x}),
        evidence_id="analytic", reviews={r: True for r in REVIEWS},
        review_evidence_ids={r: "analytic" for r in REVIEWS},
    )
    from pipeline_integrity.contracts import EvidenceRef, RunContext

    context = RunContext(
        asset_id=q.asset_id, scenario_id="analytic qualification", clock_id=q.clock_id, origin="synthetic",
        evidence=(EvidenceRef(id="analytic", citation="Hand-derived 1-D constant-speed case", origin="synthetic"),),
        metadata={"inverse_localization_boundary": boundary_metadata(q)},
    )
    return q, context, ExactTravelTimeProvider(q, sensor_x)


def test_exact_two_sensor_arrival_selects_unique_true_candidate():
    q, context, provider = make_case()
    record = localize_leak(q, context, provider)
    assert record["status"] == "computed"
    assert record["best_candidate_ids"] == ["x_20"]
    assert record["best_is_separated_from_every_alternative"] is True
    losses = {r["candidate_id"]: r["mahalanobis_squared"] for r in record["candidate_results"]}
    assert losses["x_20"] == pytest.approx(0.0, abs=1e-20)
    assert losses["x_50"] > 0 and losses["x_80"] > losses["x_50"]


def test_joint_length_and_speed_scaling_preserves_arrival_solution():
    base = localize_leak(*make_case())
    scaled = localize_leak(*make_case(scale=7.5))
    assert base["best_candidate_ids"] == scaled["best_candidate_ids"] == ["x_20"]
    base_arrivals = [r["value"] for r in base["request"]["rows"]]
    scaled_arrivals = [r["value"] for r in scaled["request"]["rows"]]
    assert scaled_arrivals == pytest.approx(base_arrivals)


def test_midpoint_single_sensor_is_nonidentifiable_and_invalid_rows_withhold():
    ambiguous = make_case(sensors=(50.0,), source_x=20.0, candidates=(20.0, 80.0))
    # One observed sensor has the same travel distance for the mirrored candidates.
    q, context, provider = ambiguous
    result = localize_leak(q, context, provider)
    assert result["best_candidate_ids"] == ["x_20", "x_80"]
    assert result["best_is_separated_from_every_alternative"] is False
    bad_q = q.model_copy(update={"rows": (q.rows[0].model_copy(update={"valid": False}),)})
    bad_q = LocalizationRequest.model_validate(bad_q.model_dump(mode="python"))
    bad_context = context.model_copy(update={"metadata": {"inverse_localization_boundary": boundary_metadata(bad_q)}})
    withheld = localize_leak(bad_q, bad_context)
    assert withheld["status"] == "needs_data" and withheld["candidate_results"] == []
