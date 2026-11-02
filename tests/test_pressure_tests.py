"""Pressure/temperature scope and inventory transfer guards, separate from native numerical checks."""

import runpy

import pytest
from pint import DimensionalityError

from pipeline_integrity.pressure_tests import (
    REVIEWS,
    MolarGasState,
    evaluate_gas_decay_series,
    evaluate_pressure_test,
    load_pe100_pressure_decay,
)


def base():
    return runpy.run_path("examples/pressure_tests.py")["make_cases"]()


def test_injected_provider_cannot_be_silently_ignored_in_declared_mode():
    _, frames, spec, context = base()
    with pytest.raises(ValueError, match="provider compressibility mode"):
        evaluate_gas_decay_series(frames, spec, context, provider=object())


@pytest.mark.parametrize("method", list(REVIEWS))
def test_unreviewed_native_methods_withhold_without_owner(method):
    cases, _, _, context = base()
    assert (
        evaluate_pressure_test(cases[method].model_copy(update={"applicability_reviews": {}}), context)[
            "status"
        ]
        == "needs_data"
    )


def test_unknown_parameters_and_context_or_evidence_do_not_gain_defaults():
    cases, _, _, context = base()
    r = cases["inspection.gas_pressure_decay"]
    assert (
        evaluate_pressure_test(
            r.model_copy(update={"parameters": r.parameters | {"rigid_volume_m3": None}}), context
        )["status"]
        == "needs_data"
    )
    with pytest.raises(ValueError, match="asset/clock"):
        evaluate_pressure_test(r, context.model_copy(update={"clock_id": "other"}))
    with pytest.raises(ValueError, match="evidence"):
        evaluate_pressure_test(r.model_copy(update={"evidence_ids": ("missing",)}), context)


@pytest.mark.parametrize(
    "update",
    [
        {"rigid_volume_m3": None},
        {"reference_pressure_pa_abs": None},
        {"reference_temperature_k": None},
        {"gas_temperature_representative_reviewed": False},
        {"isolated_rigid_fixed_composition_reviewed": False},
        {"compressibility_factor": None},
        {"reference_compressibility_factor": None},
        {"pressure_reference": "gauge", "ambient_pa_abs": None},
        {
            "compressibility_source": "provider",
            "compressibility_factor": None,
            "reference_compressibility_factor": None,
            "fluid": None,
        },
    ],
)
def test_missing_gas_boundary_information_withholds_before_optional_imports(update):
    _, frames, spec, context = base()
    assert (
        evaluate_gas_decay_series(frames, spec.model_copy(update=update), context)["status"] == "needs_data"
    )


def test_missing_interior_or_invalid_values_cannot_be_dropped_to_endpoints():
    _, frames, spec, context = base()
    missing = frames[1].model_copy(update={"values": dict(p=None, T=300.0), "valid": dict(p=False, T=True)})
    r = evaluate_gas_decay_series((frames[0], missing, frames[-1]), spec, context)
    assert r["invalid_sample_indices"] == [1] and r["values"] == {} and r["endpoint_result"] is None
    invalid = frames[1].model_copy(update={"valid": dict(p=False, T=True)})
    assert (
        evaluate_gas_decay_series((frames[0], invalid, frames[-1]), spec, context)["status"] == "needs_data"
    )


def test_clock_gaps_reorder_and_units_are_not_repaired():
    _, frames, spec, context = base()
    assert evaluate_gas_decay_series((frames[0], frames[-1]), spec, context)["uncovered_intervals"] == [0]
    with pytest.raises(ValueError):
        evaluate_gas_decay_series(tuple(reversed(frames)), spec, context)
    wrong = tuple(f.model_copy(update={"clock_id": "other"}) for f in frames)
    with pytest.raises(ValueError):
        evaluate_gas_decay_series(wrong, spec, context)
    wrong = tuple(f.model_copy(update={"units": dict(p="m", T="K")}) for f in frames)
    with pytest.raises(DimensionalityError):
        evaluate_gas_decay_series(wrong, spec, context)


def test_gauge_pressure_has_explicit_ambient_in_the_native_job(monkeypatch):
    import pipeline_integrity.pressure_tests as module

    _, frames, spec, context = base()
    calls = []

    def fake(request, ctx, **kwargs):
        calls.append(request.parameters)
        return dict(
            status="computed", values=dict(net_molar_outflow_mol_s=-0.2, reference_volume_outflow_m3_s=-0.004)
        )

    monkeypatch.setattr(module, "evaluate_pressure_test", fake)
    result = evaluate_gas_decay_series(
        frames, spec.model_copy(update={"pressure_reference": "gauge", "ambient_pa_abs": 1e5}), context
    )
    assert result["status"] == "computed" and len(calls) == 3
    assert (
        calls[0]["initial_absolute_pressure_Pa"] == 2e5 and calls[0]["final_absolute_pressure_Pa"] == 1.95e5
    )
    assert result["values"]["total_net_moles_out"] == -2.0
    assert (
        not result["net_inventory_identifies_leak_path"]
        and not result["inspection_acceptance_or_field_validation"]
    )


def test_provider_molar_density_is_converted_to_the_native_R_basis(monkeypatch):
    import pipeline_integrity.pressure_tests as module

    _, frames, spec, context = base()
    calls = []

    class Provider:
        def resolve(self, req):
            return MolarGasState(
                fluid=req.fluid,
                backend=req.backend,
                pressure_pa_abs=req.inputs["P"],
                temperature_k=req.inputs["T"],
                molar_density_mol_m3=40.0,
                phase="gas",
                provider=dict(id="authored", version="1"),
            )

    def fake(req, ctx, **kwargs):
        calls.append(req.parameters)
        return dict(
            status="computed", values=dict(net_molar_outflow_mol_s=0.0, reference_volume_outflow_m3_s=0.0)
        )

    monkeypatch.setattr(module, "evaluate_pressure_test", fake)
    updated = spec.model_copy(
        update={
            "compressibility_source": "provider",
            "compressibility_factor": None,
            "reference_compressibility_factor": None,
            "fluid": "declared_gas",
        }
    )
    result = evaluate_gas_decay_series(frames, updated, context, provider=Provider())
    z = 1e5 / (40 * module.R_NATIVE * 300)
    assert calls[0]["initial_compressibility_factor"] == pytest.approx(z)
    assert result["states"][0]["molar_density_mol_m3"] == 40


@pytest.mark.parametrize(
    "change,status",
    [
        ({"phase": "liquid"}, "out_of_scope"),
        ({"fluid": "other"}, "error"),
        ({"pressure_pa_abs": 1.0}, "error"),
    ],
)
def test_property_provider_phase_identity_and_state_are_checked(change, status):
    _, frames, spec, context = base()

    class Provider:
        def resolve(self, req):
            return MolarGasState(
                **(
                    dict(
                        fluid=req.fluid,
                        backend=req.backend,
                        pressure_pa_abs=req.inputs["P"],
                        temperature_k=req.inputs["T"],
                        molar_density_mol_m3=40.0,
                        phase="gas",
                        provider=dict(id="test"),
                    )
                    | change
                )
            )

    updated = spec.model_copy(
        update={
            "compressibility_source": "provider",
            "compressibility_factor": None,
            "reference_compressibility_factor": None,
            "fluid": "declared_gas",
        }
    )
    if status == "error":
        with pytest.raises(ValueError):
            evaluate_gas_decay_series(frames, updated, context, provider=Provider())
    else:
        result = evaluate_gas_decay_series(frames, updated, context, provider=Provider())
        assert result["status"] == status
        assert result["states"][0]["phase"] == "liquid"


def test_source_parser_checks_supplier_digest_before_optional_reader(tmp_path):
    path = tmp_path / "changed.xlsx"
    path.write_bytes(b"not the pinned asset")
    with pytest.raises(ValueError, match="supplier checksum"):
        load_pe100_pressure_decay(path)
