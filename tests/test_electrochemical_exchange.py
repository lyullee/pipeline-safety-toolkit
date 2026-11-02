"""Current partition, source binding and native-job boundary tests; native laws verified separately."""

import copy
import runpy
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from pipeline_integrity.contracts import SignalFrame, digest
from pipeline_integrity.electrochemical_exchange import (
    ElectrochemicalRequest,
    SurfaceCurrentReduction,
    evaluate_electrochemical,
    reduce_surface_current,
)


def base():
    return runpy.run_path("examples/electrochemical_methods.py")["make_cases"]()


def current(values=(1.0, 3.0), *, meaning="anodic_dissolution", unit="A/m2", area_basis="bare_surface"):
    cases, context = base()
    req = cases["corrosion.charge_to_loss"]
    spec = SurfaceCurrentReduction(
        asset_id=context.asset_id,
        location_id=req.location_id,
        clock_id=context.clock_id,
        condition_id=req.condition_id,
        channel="j",
        exposed_area_m2=2.0,
        density_area_basis=area_basis,
        current_meaning=meaning,
        maximum_interval_s=4.0,
        sign_and_surface_reviewed=True,
        evidence_ids=("reference",),
    )
    frames = tuple(
        SignalFrame(
            stream_id="current",
            clock_id=context.clock_id,
            time_s=float(i * 4),
            values={"j": value},
            units={"j": unit},
            valid={"j": value is not None},
            origin="synthetic",
        )
        for i, value in enumerate(values)
    )
    return frames, spec, context


@pytest.mark.parametrize("method", list(base()[0]))
def test_missing_review_is_withheld_before_owner_import(method):
    cases, context = base()
    result = evaluate_electrochemical(cases[method].model_copy(update={"applicability_reviews": {}}), context)
    assert result["status"] == "needs_data" and result["native_receipt"] is None


@pytest.mark.parametrize(
    "method,meaning",
    [
        ("corrosion.faraday", "protective_demand"),
        ("corrosion.charge_to_loss", "net_external_electrode_current"),
        ("protection.cathodic_budget", "anodic_dissolution"),
        ("protection.coating_coverage", "protective_demand"),
    ],
)
def test_wrong_current_meaning_cannot_supply_native_model(method, meaning):
    cases, context = base()
    result = evaluate_electrochemical(cases[method].model_copy(update={"current_meaning": meaning}), context)
    assert result["status"] == "out_of_scope" and result["values"] == {}


def test_galvanic_potentials_need_explicit_common_reference_and_scope():
    cases, context = base()
    request = cases["corrosion.galvanic_linear"]
    for change in (
        {"potential_reference_id": None},
        {"material_id": None},
        {"environment_id": None},
        {"current_meaning": "unresolved"},
    ):
        assert evaluate_electrochemical(request.model_copy(update=change), context)["status"] == "needs_data"


def test_current_reduction_preserves_signed_integral_and_units_without_area_inference():
    frames, spec, context = current()
    result = reduce_surface_current(frames, spec, context)
    assert result["status"] == "computed"
    assert result["density_charge_C_m2_on_known_intervals"] == 8.0
    assert result["charge_C_on_known_intervals"] == 16.0
    other, _, _ = current((0.1, 0.3), unit="mA/cm2")
    assert reduce_surface_current(other, spec, context)["charge_C_on_known_intervals"] == pytest.approx(16.0)
    result = reduce_surface_current(frames, spec.model_copy(update={"exposed_area_m2": None}), context)
    assert result["status"] == "needs_data" and result["charge_C_on_known_intervals"] is None


def test_signed_net_and_negative_declared_dissolution_are_never_absolute_valued():
    frames, spec, context = current((-1.0, 1.0), meaning="net_external_electrode_current")
    result = reduce_surface_current(frames, spec, context)
    assert result["status"] == "computed" and result["density_charge_C_m2_on_known_intervals"] == 0.0
    assert not result["total_metal_dissolution_identified"]
    result = reduce_surface_current(
        frames, spec.model_copy(update={"current_meaning": "anodic_dissolution"}), context
    )
    assert result["status"] == "out_of_scope" and result["current_density_A_m2"] == [-1.0, 1.0]


def test_gap_missing_quality_and_order_are_not_repaired():
    frames, spec, context = current((1.0, None, 2.0))
    result = reduce_surface_current(frames, spec, context)
    assert result["status"] == "needs_data" and result["unresolved_interval_indices"] == [0, 1]
    frames, spec, context = current()
    assert (
        reduce_surface_current(frames, spec.model_copy(update={"maximum_interval_s": 1.0}), context)["status"]
        == "needs_data"
    )
    with pytest.raises(ValueError, match="strictly increase"):
        reduce_surface_current(tuple(reversed(frames)), spec, context)


def test_foreign_asset_clock_evidence_and_irrelevant_reviews_fail():
    cases, context = base()
    request = cases["corrosion.charge_to_loss"]
    for change in ({"asset_id": "other"}, {"clock_id": "other"}, {"evidence_ids": ("missing",)}):
        with pytest.raises(ValueError):
            evaluate_electrochemical(request.model_copy(update=change), context)
    with pytest.raises(ValidationError, match="review keys"):
        ElectrochemicalRequest.model_validate_json(
            request.model_copy(update={"applicability_reviews": {"unrelated": True}}).model_dump_json()
        )


def test_source_charge_change_incomplete_history_and_wrong_location_are_refused():
    cases, context = base()
    frames, spec, _ = current()
    history = reduce_surface_current(frames, spec, context)
    request = cases["corrosion.charge_to_loss"]
    with pytest.raises(ValueError, match="charge differs"):
        evaluate_electrochemical(request, context, history_record=history)
    with pytest.raises(ValueError, match="location"):
        evaluate_electrochemical(
            request.model_copy(update={"location_id": "other"}), context, history_record=history
        )
    modified = copy.deepcopy(history)
    modified["charge_C_on_known_intervals"] = 0.0
    with pytest.raises(ValueError, match="changed after sealing"):
        evaluate_electrochemical(request, context, history_record=modified)
    frames, _, _ = current((1.0, None))
    missing = reduce_surface_current(frames, spec, context)
    assert evaluate_electrochemical(request, context, history_record=missing)["status"] == "needs_data"


def test_full_surface_demand_is_not_multiplied_by_bare_fraction_twice():
    cases, context = base()
    frames, spec, _ = current(meaning="protective_demand", area_basis="full_exposed_surface")
    history = reduce_surface_current(frames, spec, context)
    template = cases["protection.cathodic_budget"]
    p = template.parameters | dict(
        time_s=history["time_s"],
        protected_area_m2=2.0,
        bare_surface_current_density_A_m2=history["current_density_A_m2"],
        bare_surface_fraction=[0.5, 0.5],
    )
    request = template.model_copy(update={"parameters": p})
    assert evaluate_electrochemical(request, context, history_record=history)["status"] == "out_of_scope"


def test_native_job_is_reused_without_changing_inputs_units_or_status(monkeypatch):
    import pipeline_integrity.electrochemical_exchange as exchange

    cases, context = base()
    request = cases["corrosion.coupon_loss"]
    calls = []

    def prepare(method, parameters, ctx, **kwargs):
        calls.append((method, parameters, ctx, kwargs))
        return "pinned_job"

    def evaluate(job, ctx, **kwargs):
        assert job == "pinned_job" and ctx == context
        return SimpleNamespace(
            check_integrity=lambda: None,
            native=SimpleNamespace(
                payload={
                    "status": "out_of_scope",
                    "values": {"declared_raw": 4.0},
                    "units": {"declared_raw": "m"},
                    "notes": ("Native scope decision",),
                }
            ),
            model_dump=lambda **kwargs: {"native_original": True},
        )

    monkeypatch.setattr(exchange, "prepare_material_job", prepare)
    monkeypatch.setattr(exchange, "evaluate_material_job", evaluate)
    result = evaluate_electrochemical(request, context, client="explicit_owner")
    assert result["status"] == "out_of_scope" and result["values"] == {"declared_raw": 4.0}
    assert calls[0][0] == request.method and calls[0][1] == request.parameters
    assert calls[0][3]["client"] == "explicit_owner"
    assert result["record_digest"] == digest({k: v for k, v in result.items() if k != "record_digest"})


def test_surface_current_unit_and_arithmetic_failures_are_not_reinterpreted():
    from pint.errors import DimensionalityError

    frames, spec, context = current(unit="W")
    with pytest.raises(DimensionalityError):
        reduce_surface_current(frames, spec, context)
    frames, spec, context = current((1e308, 1e308))
    result = reduce_surface_current(frames, spec, context)
    assert result["status"] == "out_of_scope"
    assert result["charge_C_on_known_intervals"] is None


def test_current_gap_tolerance_is_explicit_and_can_be_disabled():
    frames, spec, context = current()
    spec = spec.model_copy(update={"maximum_interval_s": 4.0 - 1e-10})
    assert reduce_surface_current(frames, spec, context)["status"] == "computed"
    assert (
        reduce_surface_current(frames, spec.model_copy(update={"time_tolerance_s": 0.0}), context)["status"]
        == "needs_data"
    )


def test_supplier_workbook_checksum_is_required_before_interpretation(tmp_path):
    from pipeline_integrity.electrochemical_exchange import load_galvanized_tafel_summary

    path = tmp_path / "changed.xlsx"
    path.write_bytes(b"not the pinned supplier workbook")
    with pytest.raises(ValueError, match="supplier checksum"):
        load_galvanized_tafel_summary(path)
