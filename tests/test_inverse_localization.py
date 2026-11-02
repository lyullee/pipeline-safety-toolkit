"""Prepared for HPC execution; static checks are not numerical validation."""

from copy import deepcopy
from dataclasses import dataclass
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path

import pytest

from pipeline_integrity.contracts import digest
from pipeline_integrity.inverse_localization import (
    boundary_metadata,
    localize_leak,
    prediction_binding,
    verify_inverse_localization_record,
)

spec = spec_from_file_location(
    "localization_example", Path(__file__).parents[1] / "examples/inverse_localization.py"
)
example = module_from_spec(spec)
spec.loader.exec_module(example)


def rebound(q, c, **updates):
    q = type(q).model_validate(q.model_copy(update=updates).model_dump(mode="python"))
    return q, c.model_copy(update={"metadata": {"inverse_localization_boundary": boundary_metadata(q)}})


def test_default_withholds_without_provider():
    q, c, _ = example.make_case()
    record = localize_leak(q, c)
    assert record["status"] == "needs_data" and record["runtime"] is None
    assert verify_inverse_localization_record(record)


def test_correlated_loss_and_tied_candidates():
    q, c, provider = example.make_case(reviewed=True)
    record = localize_leak(q, c, provider)
    rows = {r["candidate_id"]: r for r in record["candidate_results"]}
    assert record["best_candidate_ids"] == ["a", "c"]
    assert record["best_indistinguishable_candidate_ids"] == ["a", "c"]
    assert record["best_is_separated_from_every_alternative"] is False
    assert rows["b"]["mahalanobis_squared"] == pytest.approx(4.0)
    assert rows["b"]["relative_log_likelihood"] == pytest.approx(-2.0)
    assert record["location_posterior"] is None
    assert record["leak_existence_probability"] is None
    assert any(
        r["left"] == "a" and r["right"] == "c" and r["distance_squared"] == 0
        for r in record["pairwise_distances"]
    )
    assert verify_inverse_localization_record(record, provider)


@pytest.mark.parametrize(
    "update",
    [
        {"covariance": None},
        {"noise_law": None},
        {"reviews": {}},
    ],
)
def test_unknowns_do_not_call_provider(update):
    q, c, _ = example.make_case(reviewed=True)
    q, c = rebound(q, c, **update)
    assert localize_leak(q, c)["runtime"] is None


def test_invalid_observation_not_deleted():
    q, c, _ = example.make_case(reviewed=True)
    q, c = rebound(q, c, rows=(q.rows[0].model_copy(update={"valid": False}), q.rows[1]))
    assert localize_leak(q, c)["candidate_results"] == []


@pytest.mark.parametrize(
    "covariance", [((1.0, 2.0), (2.0, 1.0)), ((1.0, 0.5), (0.4, 1.0)), ((0.0, 0.0), (0.0, 1.0))]
)
def test_bad_covariance_rejected(covariance):
    q, c, _ = example.make_case(reviewed=True)
    q, c = rebound(q, c, covariance=covariance)
    with pytest.raises(ValueError):
        localize_leak(q, c, example.AuthoredTable(q))


@dataclass
class ModifiedProvider:
    original: object
    mode: str

    def __getattr__(self, name):
        return getattr(self.original, name)

    def __call__(self, candidate, times, context):
        p = self.original(candidate, times, context)
        if self.mode == "withhold" and candidate.id == "c":
            p = p.model_copy(update={"status": "withheld"})
        elif self.mode == "units":
            p = p.model_copy(update={"units": {"pressure_0": "kPa", "pressure_1": "Pa"}})
        elif self.mode == "clock":
            p = p.model_copy(update={"clock_id": "wrong"})
        elif self.mode == "source":
            p = p.model_copy(update={"provider_source_digest": "f" * 64})
        elif self.mode == "nan_copy":
            return p.model_copy(update={"values": {"pressure_0": (float("nan"),), "pressure_1": (1.0,)}})
        elif self.mode == "overflow":
            p = p.model_copy(update={"values": {"pressure_0": (1e308,), "pressure_1": (1e308,)}})
        elif self.mode == "unsealed":
            return p.model_copy(update={"values": {"pressure_0": (1.0,), "pressure_1": (2.0,)}})
        return p.model_copy(
            update={"evaluation_digest": prediction_binding(self.original.request, candidate, context, p)}
        )


def test_withheld_candidate_prevents_overall_winner():
    q, c, provider = example.make_case(reviewed=True)
    record = localize_leak(q, c, ModifiedProvider(provider, "withhold"))
    assert record["candidate_results"] and record["status"] == "needs_data"
    assert record["best_candidate_ids"] == []


@pytest.mark.parametrize("mode", ["units", "clock", "source", "unsealed", "nan_copy"])
def test_provider_boundary_changes_rejected(mode):
    q, c, provider = example.make_case(reviewed=True)
    with pytest.raises(ValueError):
        localize_leak(q, c, ModifiedProvider(provider, mode))


def test_native_overflow_never_becomes_computed_score():
    q, c, provider = example.make_case(reviewed=True)
    with pytest.raises((ValueError, FloatingPointError)):
        localize_leak(q, c, ModifiedProvider(provider, "overflow"))


@pytest.mark.parametrize(
    "field,value", [("status", "needs_data"), ("best_candidate_ids", ["b"]), ("field_qualified", True)]
)
def test_rehashed_tamper_replayed(field, value):
    q, c, provider = example.make_case(reviewed=True)
    record = deepcopy(localize_leak(q, c, provider))
    record[field] = value
    record["record_sha256"] = digest({k: v for k, v in record.items() if k != "record_sha256"})
    with pytest.raises(ValueError):
        verify_inverse_localization_record(record, provider)


def test_context_does_not_accept_different_event_boundary():
    q, c, provider = example.make_case(reviewed=True)
    with pytest.raises(ValueError):
        localize_leak(q.model_copy(update={"event_id": "different"}), c, provider)


def test_nan_in_model_copy_rejected():
    q, c, provider = example.make_case(reviewed=True)
    with pytest.raises(ValueError):
        localize_leak(q.model_copy(update={"indistinguishable_distance_squared": float("nan")}), c, provider)


class CommonLargeBaselineProvider(ModifiedProvider):
    def __call__(self, candidate, times, context):
        p = self.original(candidate, times, context)
        p = p.model_copy(
            update={
                "values": {
                    "pressure_0": tuple(1e300 for _ in times),
                    "pressure_1": tuple(1e300 for _ in times),
                }
            }
        )
        return p.model_copy(
            update={"evaluation_digest": prediction_binding(self.original.request, candidate, context, p)}
        )


def test_identifiability_whitens_prediction_difference_not_absolute_baseline():
    q, c, _ = example.make_case(reviewed=True)
    q, c = rebound(
        q,
        c,
        rows=tuple(row.model_copy(update={"value": 1e300}) for row in q.rows),
        covariance=((1e-40, 0.0), (0.0, 1e-40)),
        provider_configuration_sha256=digest("authored common large baseline"),
    )
    provider = CommonLargeBaselineProvider(example.AuthoredTable(q), "large_baseline")
    record = localize_leak(q, c, provider)
    assert record["status"] == "computed"
    assert record["best_candidate_ids"] == ["a", "b", "c"]
    assert all(pair["distance_squared"] == 0.0 for pair in record["pairwise_distances"])
    assert record["best_is_separated_from_every_alternative"] is False
