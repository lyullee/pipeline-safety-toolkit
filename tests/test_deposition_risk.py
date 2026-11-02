"""Prepared for HPC; no local numerical or native execution is asserted."""

import sys
from copy import deepcopy
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import ModuleType

import pytest

from pipeline_integrity.contracts import digest
from pipeline_integrity.deposition_risk import (
    EnvelopeKnot,
    ThermopackHydrateConfig,
    ThermopackHydrateProvider,
    assess_deposition,
    boundary_metadata,
    envelope_binding,
    envelope_payload_sha256,
    verify_deposition_record,
)

spec = spec_from_file_location(
    "deposition_example", Path(__file__).parents[1] / "examples/deposition_risk.py"
)
example = module_from_spec(spec)
spec.loader.exec_module(example)


def rebind(q, c, envelope, **updates):
    envelope = type(envelope).model_validate(envelope.model_dump(mode="python"))
    updates["expected_envelope_payload_sha256"] = envelope_payload_sha256(envelope)
    q = type(q).model_validate(q.model_copy(update=updates).model_dump(mode="python"))
    c = c.model_copy(update={"metadata": {"deposition_boundary": boundary_metadata(q)}})
    return q, c, envelope.model_copy(update={"evaluation_sha256": envelope_binding(q, c, envelope)})


def test_reviews_withhold_before_provider():
    q, c, _ = example.make_case()
    result = assess_deposition(q, c)
    assert result["status"] == "needs_data" and result["envelope"] is None
    assert verify_deposition_record(result)


def test_linear_boundary_and_sampled_window_not_kinetics():
    q, c, e = example.make_case(reviewed=True)
    result = assess_deposition(q, c, envelope=e)
    assert result["status"] == "computed"
    assert result["point_comparisons"][0]["temperature_margin_bounds_k"] == pytest.approx([4.0, 6.0])
    assert result["point_comparisons"][1]["temperature_margin_bounds_k"] == pytest.approx([-6.0, -4.0])
    assert result["sampled_below_boundary_windows"][0]["sample_span_s"] == 2.0
    assert result["kinetics_calculated"] is False
    assert result["continuous_exposure_duration_s"] is None and result["plugging_probability"] is None
    assert verify_deposition_record(result)


def test_uncertain_pressure_checks_interior_nonmonotone_knots():
    q, c, e = example.make_case(reviewed=True)
    e = e.model_copy(
        update={
            "knots": (
                e.knots[0],
                EnvelopeKnot(
                    pressure_pa_abs=1.5e6,
                    boundary_temperature_lower_k=299.0,
                    boundary_temperature_upper_k=301.0,
                ),
                e.knots[-1],
            )
        }
    )
    observations = tuple(
        r.model_copy(update={"pressure_bounds_pa_abs": (1e6, 2e6), "temperature_bounds_k": (280.0, 280.0)})
        for r in q.observations
    )
    q, c, e = rebind(q, c, e, observations=observations)
    result = assess_deposition(q, c, envelope=e)
    assert result["point_comparisons"][0]["temperature_margin_bounds_k"] == pytest.approx([-21.0, 11.0])
    assert result["point_comparisons"][0]["classification"] == "boundary_or_uncertainty_overlap"
    assert result["sampled_below_boundary_windows"] == []


@pytest.mark.parametrize("bounds", [(0.9e6, 1.1e6), (1.9e6, 2.1e6)])
def test_no_pressure_extrapolation(bounds):
    q, c, e = example.make_case(reviewed=True)
    observations = tuple(r.model_copy(update={"pressure_bounds_pa_abs": bounds}) for r in q.observations)
    q, c, e = rebind(q, c, e, observations=observations)
    result = assess_deposition(q, c, envelope=e)
    assert result["status"] == "out_of_scope" and result["sampled_below_boundary_windows"] == []


def test_unknown_invalid_or_gap_breaks_window():
    q, c, e = example.make_case(reviewed=True)
    q, c, e = rebind(q, c, e, maximum_sample_gap_s=0.5)
    assert assess_deposition(q, c, envelope=e)["sampled_below_boundary_windows"] == []
    observations = tuple(
        r.model_copy(update={"temperature_bounds_k": None}) if i == 2 else r
        for i, r in enumerate(q.observations)
    )
    q, c, e = rebind(q, c, e, observations=observations)
    result = assess_deposition(q, c, envelope=e)
    assert result["status"] == "needs_data" and result["sampled_below_boundary_windows"] == []


def test_exact_boundary_is_unresolved_not_safe_or_deposit():
    q, c, e = example.make_case(reviewed=True)
    observations = tuple(
        r.model_copy(update={"temperature_bounds_k": (274.0, 274.0)}) for r in q.observations
    )
    q, c, e = rebind(q, c, e, observations=observations)
    assert all(
        r["classification"] == "boundary_or_uncertainty_overlap"
        for r in assess_deposition(q, c, envelope=e)["point_comparisons"]
    )


def test_unknown_wax_characterization_preserved():
    q, c, e = example.make_case(reviewed=True)
    q, c, e = rebind(q, c, e, phenomenon="wax")
    assert assess_deposition(q, c, envelope=e)["status"] == "needs_data"


def test_reversed_knots_and_unknown_boundary_not_repaired():
    q, c, e = example.make_case(reviewed=True)
    with pytest.raises(ValueError):
        rebind(q, c, e.model_copy(update={"knots": tuple(reversed(e.knots))}))
    e = e.model_copy(
        update={"knots": (e.knots[0].model_copy(update={"boundary_temperature_lower_k": None}), e.knots[1])}
    )
    q, c, e = rebind(q, c, e)
    assert assess_deposition(q, c, envelope=e)["status"] == "needs_data"


@pytest.mark.parametrize(
    "field,value", [("kinetics_calculated", True), ("plugging_probability", 0.1), ("point_comparisons", [])]
)
def test_rehashed_tamper_replayed(field, value):
    q, c, e = example.make_case(reviewed=True)
    r = deepcopy(assess_deposition(q, c, envelope=e))
    r[field] = value
    r["record_sha256"] = digest({k: v for k, v in r.items() if k != "record_sha256"})
    with pytest.raises(ValueError):
        verify_deposition_record(r)


def test_changed_composition_does_not_reuse_old_envelope():
    q, c, e = example.make_case(reviewed=True)
    basis = q.basis.model_copy(update={"composition_mole_fractions": {"C1": 0.5, "H2O": 0.5}})
    q, c, e = rebind(q, c, e, basis=basis)
    with pytest.raises(ValueError):
        assess_deposition(q, c, envelope=e)


def test_original_provider_replayed():
    q, c, e = example.make_case(reviewed=True)
    q, c, _ = rebind(q, c, e, source_mode="provider")
    calls = []

    def provider(request, context):
        calls.append(1)
        return e.model_copy(update={"evaluation_sha256": envelope_binding(request, context, e)})

    r = assess_deposition(q, c, provider=provider)
    assert verify_deposition_record(r, provider=provider) and len(calls) == 2


def test_thermopack_call_signature_uses_owner_only(monkeypatch):
    q, c, _ = example.make_case(reviewed=True)
    cfg = ThermopackHydrateConfig(
        eos="SRK",
        mixing="vdW",
        alpha="Classic",
        maximum_fluid_phases=3,
        hydrate_parameter_reference="Default",
        minimum_pressure_pa_abs=1e6,
        maximum_pressure_pa_abs=2e6,
        minimum_temperature_k=250.0,
        boundary_temperature_error_bound_k=1.0,
        boundary_uncertainty_basis_id="authored_error",
    )
    q = q.model_copy(
        update={
            "provider_id": "thermopack_hydrate_appearance",
            "provider_version": "authored_owner",
            "provider_source_sha256": digest("authored_installation"),
            "provider_configuration_sha256": digest(cfg),
            "source_mode": "provider",
            "expected_envelope_payload_sha256": None,
        }
    )
    q = type(q).model_validate(q.model_dump(mode="python"))
    c = c.model_copy(update={"metadata": {"deposition_boundary": boundary_metadata(q)}})
    calls = []

    class OwnerDouble:
        def init_thermo(self, *args):
            calls.append(args)

        def init_hydrate(self, **kwargs):
            calls.append(kwargs)

        def get_hydrate_apperance_curve(self, *args, **kwargs):
            calls.append((args, kwargs))
            return (270.0, 280.0), (1e6, 2e6)

        def get_model_id(self):
            return "authored_native_test_double"

    module = ModuleType("thermopack")
    native = ModuleType("thermopack.hydrate")
    native.__file__ = "authored/hydrate.py"
    native.hydrate = OwnerDouble
    module.hydrate = native
    monkeypatch.setitem(sys.modules, "thermopack", module)
    monkeypatch.setitem(sys.modules, "thermopack.hydrate", native)
    monkeypatch.setattr(
        "pipeline_integrity.deposition_risk.thermopack_source_fingerprint",
        lambda path: {"source_sha256": q.provider_source_sha256, "files": {}},
    )
    monkeypatch.setattr("pipeline_integrity.deposition_risk.version", lambda package: "authored_owner")
    r = assess_deposition(q, c, provider=ThermopackHydrateProvider(cfg))
    assert calls[0] == ("SRK", "vdW", "Classic", "C1,H2O", 3)
    assert calls[2] == ((1e6, (0.9999, 0.0001), 250.0, 2e6), {"print_to_file": False})
    assert r["envelope"]["native_receipt"]["owner"] == "ThermoPack"


@pytest.mark.parametrize("capability", ["missing_module", "incomplete_methods"])
def test_thermopack_capability_rejected_before_native_creation(monkeypatch, capability):
    q, c, _ = example.make_case(reviewed=True)
    cfg = ThermopackHydrateConfig(
        eos="SRK",
        mixing="vdW",
        alpha="Classic",
        maximum_fluid_phases=3,
        hydrate_parameter_reference="Default",
        minimum_pressure_pa_abs=1e6,
        maximum_pressure_pa_abs=2e6,
        minimum_temperature_k=250.0,
        boundary_temperature_error_bound_k=1.0,
        boundary_uncertainty_basis_id="authored_error",
    )
    q = q.model_copy(
        update={"provider_id": "thermopack_hydrate_appearance", "provider_configuration_sha256": digest(cfg)}
    )
    package = ModuleType("thermopack")
    package.__path__ = []
    monkeypatch.setitem(sys.modules, "thermopack", package)
    monkeypatch.delitem(sys.modules, "thermopack.hydrate", raising=False)
    if capability == "incomplete_methods":
        native = ModuleType("thermopack.hydrate")

        class MustNotConstruct:
            def __init__(self):
                pytest.fail("incompatible native class must not be constructed")

        native.hydrate = MustNotConstruct
        monkeypatch.setitem(sys.modules, "thermopack.hydrate", native)
    with pytest.raises(ValueError, match="API absent|method contract"):
        ThermopackHydrateProvider(cfg)(q, c)
