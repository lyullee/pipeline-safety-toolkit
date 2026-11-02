"""Synthetic closed-form oracle for repeated inspection state evolution.

This fixture checks orchestration against an independently calculated linear-loss
trajectory and Bayes updates. It does not validate field corrosion or POD models.
"""

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from pipeline_integrity import inspection_evolution as evolution
from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.inspection_planning import DeclaredInspectionPOD


class _Receipt:
    def __init__(self, model, values):
        self.native = SimpleNamespace(status="computed", payload={"values": values})
        self.model = model

    def check_integrity(self):
        return None

    def model_dump(self, *, mode):
        return {"model": self.model, "values": self.native.payload["values"]}


def _advance(thickness, rate, dt):
    """Independent analytic solution for the fixture's constant loss rate."""
    return thickness - rate * dt


def _bayes_no_indication(prior, pod, coverage):
    likelihoods = [1.0 - coverage * probability for probability in pod]
    marginal = sum(w * likelihood for w, likelihood in zip(prior, likelihoods, strict=True))
    return [w * likelihood / marginal for w, likelihood in zip(prior, likelihoods, strict=True)], marginal


@pytest.fixture
def oracle(monkeypatch):
    @contextmanager
    def source_guard(*args, **kwargs):
        yield {"before": {"source_tree_sha256": "a" * 64, "source_tree_files": 1},
               "after": {"source_tree_sha256": "a" * 64, "source_tree_files": 1},
               "source_unchanged": True}

    monkeypatch.setattr(evolution, "material_source_guard", source_guard)
    monkeypatch.setattr(evolution, "_sources", lambda: {"fixture": "stable"})
    monkeypatch.setattr(evolution, "prepare_material_job",
                        lambda model, parameters, *args, **kwargs: (model, parameters))

    def evaluate(job, context, *, client):
        model, p = job
        if model == "corrosion.uniform_forecast":
            values = {"predicted_thickness_m": _advance(
                p["current_thickness_m"], p["rate_m_s"], p["horizon_s"])}
        elif model == "inspection.no_indication_update":
            posterior, probability = _bayes_no_indication(
                p["prior_weights"], p["probabilities_of_detection"], p["coverage_fraction"])
            values = {"posterior_weights": posterior,
                      "probability_of_no_indication": probability}
        else:
            raise AssertionError(f"unexpected owner calculation: {model}")
        return _Receipt(model, values)

    monkeypatch.setattr(evolution, "evaluate_material_job", evaluate)
    return SimpleNamespace(evaluate=evaluate)


def test_linear_loss_sequential_bayes_performed_repair_and_round_replay(oracle):
    ids = ("target", "h1", "h2", "obs1", "pod1", "review1", "obs2", "pod2",
           "review2", "repair-source", "repair-review")
    context = RunContext(
        asset_id="synthetic-pipe", scenario_id="two-inspections", clock_id="elapsed-s",
        origin="synthetic",
        evidence=tuple(EvidenceRef(id=key, citation="Authored synthetic fixture", origin="synthetic",
                                   sha256="b" * 64 if key in {"obs1", "obs2", "repair-source"} else None)
                       for key in ids),
    )
    target = evolution.EvolutionTarget(
        id="target", location_id="section-1", initial_time_s=0, nominal_wall_m=0.012,
        required_thickness_m=0.006, evidence_id="target",
        hypotheses=(
            evolution.EvolutionHypothesis(id="h1", prior_mass=0.6,
                initial_remaining_thickness_m=0.010, rate_m_s=1e-5, evidence_id="h1"),
            evolution.EvolutionHypothesis(id="h2", prior_mass=0.4,
                initial_remaining_thickness_m=0.009, rate_m_s=2e-5, evidence_id="h2"),
        ),
    )

    def observation(n, pods):
        return evolution.EvolutionObservation(
            target_id="target", coverage_fraction=0.5 if n == 1 else 0.75,
            detection=DeclaredInspectionPOD(probabilities={"h1": pods[0], "h2": pods[1]},
                                             evidence_id=f"pod{n}"),
            source_evidence_id=f"obs{n}",
            conditional_independence_given_hypothesis_reviewed=True,
            coverage_random_and_hypothesis_independent_reviewed=True,
            review_evidence_id=f"review{n}",
        )

    request = evolution.InspectionEvolutionRequest(
        id="linear-repair-case", asset_id=context.asset_id, clock_id=context.clock_id,
        targets=(target,),
        rounds=(evolution.EvolutionRound(id="round-1", time_s=100,
                                         observations=(observation(1, (0.2, 0.8)),)),
                evolution.EvolutionRound(id="round-2", time_s=250,
                                         observations=(observation(2, (0.1, 0.7)),))),
        repair_resets=(evolution.PerformedRepairReset(
            id="repair-1", target_id="target", time_s=150,
            new_nominal_wall_m=0.011, new_required_thickness_m=0.006,
            remaining_thickness_by_hypothesis_m={"h1": 0.0105, "h2": 0.0098},
            subsequent_rate_by_hypothesis_m_s={"h1": 2e-5, "h2": 4e-5},
            source_evidence_id="repair-source", review_evidence_id="repair-review",
            actually_performed_geometry_and_regime_reviewed=True),),
        material_source_sha256="a" * 64,
    )

    record = evolution.evaluate_inspection_evolution(request, context, client=oracle,
                                                       source_root="synthetic-pinned-checkout")

    # Round one: exact constant-rate thicknesses, sizes, and first no-call update.
    round1_h1 = _advance(0.010, 1e-5, 100)
    round1_h2 = _advance(0.009, 2e-5, 100)
    posterior1, likelihood1 = _bayes_no_indication([0.6, 0.4], [0.2, 0.8], 0.5)
    row1 = record["rounds"][0]
    assert row1["states"]["target"]["hypotheses"]["h1"]["thickness_m"] == pytest.approx(round1_h1)
    assert row1["states"]["target"]["hypotheses"]["h2"]["thickness_m"] == pytest.approx(round1_h2)
    assert row1["observations"][0]["sizes_m"] == pytest.approx(
        {"h1": 0.012 - round1_h1, "h2": 0.012 - round1_h2})
    assert row1["observations"][0]["prior_masses"] == pytest.approx([0.6, 0.4])
    assert row1["observations"][0]["posterior_masses"] == pytest.approx(posterior1)
    assert row1["observations"][0]["conditional_no_indication_probability"] == pytest.approx(likelihood1)

    # Repair is an explicit state reset at t=150, followed by new linear rates.
    repair_h1 = _advance(0.0105, 2e-5, 100)
    repair_h2 = _advance(0.0098, 4e-5, 100)
    posterior2, likelihood2 = _bayes_no_indication(posterior1, [0.1, 0.7], 0.75)
    row2 = record["rounds"][1]
    assert row2["states"]["target"]["hypotheses"]["h1"]["thickness_m"] == pytest.approx(repair_h1)
    assert row2["states"]["target"]["hypotheses"]["h2"]["thickness_m"] == pytest.approx(repair_h2)
    assert row2["states"]["target"]["wall_m"] == pytest.approx(0.011)
    assert row2["observations"][0]["sizes_m"] == pytest.approx(
        {"h1": 0.011 - repair_h1, "h2": 0.011 - repair_h2})
    assert row2["observations"][0]["prior_masses"] == pytest.approx(posterior1)
    assert row2["observations"][0]["posterior_masses"] == pytest.approx(posterior2)
    assert row2["observations"][0]["conditional_no_indication_probability"] == pytest.approx(likelihood2)
    assert record["observed_path_likelihood_by_target"]["target"] == pytest.approx(likelihood1 * likelihood2)
    assert record["applied_repair_ids"] == ["repair-1"]
    assert record["final_states"]["target"]["hypotheses"]["h1"]["posterior_mass"] == pytest.approx(posterior2[0])
    assert evolution.verify_inspection_evolution_record(
        record, request=request, context=context, client=oracle,
        source_root="synthetic-pinned-checkout")
