"""Independent flow identities, actual HEOS states and strict meter boundaries."""

import builtins
import json
import math
import runpy
from copy import deepcopy
from pathlib import Path

import pytest

from pipeline_integrity.contracts import digest
from pipeline_integrity.gas_meter import (
    GasComponent,
    boundary_metadata,
    evaluate_gas_meter,
    to_signal_frame,
    to_signal_frames,
    verify_gas_meter_record,
)

MAKE_CASE = runpy.run_path(str(Path(__file__).resolve().parents[1] / "examples/gas_meter.py"))["make_case"]


def recontext(request, context):
    context = context.model_copy(deep=True)
    context.metadata["gas_meter_boundary"] = boundary_metadata(request)
    return context


@pytest.fixture(scope="module")
def pair():
    request, context = MAKE_CASE(reviewed=True)
    return request, context, evaluate_gas_meter(request, context)


def test_unreviewed_and_its_verifier_do_not_import_optional_libraries(monkeypatch):
    original = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name.startswith(("fluids", "CoolProp")):
            pytest.fail("optional library called for unreviewed meter")
        return original(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    request, context = MAKE_CASE()
    record = evaluate_gas_meter(request, context)
    assert record["status"] == "needs_data" and not record["native_called"]
    assert verify_gas_meter_record(record, request, context)["valid"]


@pytest.mark.parametrize(
    "field",
    [
        "geometry",
        "components",
        "volume_basis",
        "upstream_pressure_pa_abs",
        "downstream_pressure_pa_abs",
        "upstream_temperature_K",
        "reference",
        "support",
        "time_s",
    ],
)
def test_unknown_source_fields_prevent_backend(monkeypatch, field):
    request, context = MAKE_CASE(reviewed=True)
    request = request.model_copy(update={field: None})
    context = recontext(request, context)
    monkeypatch.setattr(
        "pipeline_integrity.gas_meter._backend",
        lambda: pytest.fail("missing source input must not inspect backend"),
    )
    record = evaluate_gas_meter(request, context)
    assert record["status"] == "needs_data" and not record["native_called"]


def test_real_gas_fixed_pair_and_reference_mass_round_trip(pair):
    request, context, record = pair
    assert record["status"] == "computed"
    values = record["values"]
    rho = values["upstream_density_kg_m3"]
    area = math.pi * 0.05**2 / 4
    independent = 0.7 * 0.99 * area * math.sqrt(2 * rho * 5000 / (1 - 0.5**4))
    assert math.isclose(values["signed_mass_flow_kg_s"], independent, rel_tol=1e-12)
    assert math.isclose(independent, 0.33316026403003635, rel_tol=1e-10)
    assert math.isclose(values["signed_actual_volume_flow_m3_s"] * rho, independent, rel_tol=1e-13)
    assert math.isclose(
        values["signed_reference_volume_flow_m3_s"] * values["reference_density_kg_m3"],
        independent,
        rel_tol=1e-13,
    )
    assert math.isclose(
        record["audits"]["density_compensation_factor"],
        record["audits"]["PTZ_compensation_factor"],
        rel_tol=1e-10,
    )
    assert verify_gas_meter_record(json.loads(json.dumps(record)), request, context)["valid"]


def test_native_reynolds_dependent_flow_matches_independent_bracket_solution():
    from fluids.flow_meter import differential_pressure_meter_C_epsilon
    from scipy.optimize import brentq

    request, context = MAKE_CASE(reviewed=True, mode="native_correlation", meter_type="ISO 5167 orifice")
    record = evaluate_gas_meter(request, context)
    values = record["values"]
    assert record["status"] == "computed"

    def residual(mass):
        coefficient, epsilon = differential_pressure_meter_C_epsilon(
            D=0.1,
            D2=0.05,
            m=mass,
            P1=500000.0,
            P2=495000.0,
            rho=values["upstream_density_kg_m3"],
            mu=next(
                c["output"]["viscosity_pa_s"]
                for c in record["native_calls"]
                if c["function"] == "CoolPropProvider.resolve"
            ),
            k=values["exponent_supplied_to_native"],
            meter_type="ISO 5167 orifice",
            taps="corner",
        )
        relation = (
            coefficient
            * epsilon
            * (math.pi * 0.05**2 / 4)
            * math.sqrt(2 * values["upstream_density_kg_m3"] * 5000 / (1 - 0.5**4))
        )
        return mass - relation

    assert math.isclose(values["signed_mass_flow_kg_s"], brentq(residual, 1e-5, 2.0), rel_tol=1e-10)


@pytest.mark.parametrize(
    "name",
    [
        "long radius nozzle",
        "ISA 1932 nozzle",
        "machined convergent venturi tube",
        "cone meter",
        "wedge meter",
    ],
)
def test_actual_native_meter_geometry_families(name):
    request, context = MAKE_CASE(reviewed=True, mode="native_correlation", meter_type=name)
    record = evaluate_gas_meter(request, context)
    assert record["status"] == "computed" and record["meter_called"]
    assert abs(record["audits"]["native_forward_mass_residual_kg_s"]) < 1e-10
    assert verify_gas_meter_record(record, request, context)["valid"]


@pytest.mark.parametrize(
    "components",
    [
        (GasComponent(fluid="Methane", mole_fraction=1.0),),
        (GasComponent(fluid="Air", mole_fraction=1.0),),
        (GasComponent(fluid="Hydrogen", mole_fraction=1.0),),
        (GasComponent(fluid="Methane", mole_fraction=0.9), GasComponent(fluid="Nitrogen", mole_fraction=0.1)),
    ],
)
def test_actual_pure_and_mixture_heos_compensation(components):
    request, context = MAKE_CASE(reviewed=True, components=components)
    record = evaluate_gas_meter(request, context)
    assert record["status"] == "computed"
    assert abs(record["audits"]["reference_volume_mass_residual_kg_s"]) < 1e-12
    assert verify_gas_meter_record(record, request, context)["valid"]


def test_cp_cv_is_explicitly_distinct_from_local_real_gas_exponent(pair):
    request, context, local = pair
    assert abs(local["values"]["cp_cv_ratio"] - local["values"]["local_isentropic_exponent"]) > 1e-5
    request = request.model_copy(update={"exponent_basis": "reviewed_cp_cv_approximation"})
    other = evaluate_gas_meter(request, context)
    assert other["status"] == "computed"
    assert other["values"]["exponent_supplied_to_native"] == other["values"]["cp_cv_ratio"]
    assert local["values"]["exponent_supplied_to_native"] == local["values"]["local_isentropic_exponent"]


@pytest.mark.parametrize(
    "mode", ["backpressure", "large_drop", "reverse_unreviewed", "native_reverse", "wet_to_dry"]
)
def test_unsupported_boundary_is_refused_before_native(monkeypatch, mode):
    request, context = MAKE_CASE(reviewed=True)
    if mode == "backpressure":
        request = request.model_copy(update={"downstream_pressure_pa_abs": 510000.0})
    elif mode == "large_drop":
        request = request.model_copy(update={"downstream_pressure_pa_abs": 100000.0})
    elif mode in {"reverse_unreviewed", "native_reverse"}:
        update = {"upstream_port_id": "port-B", "downstream_port_id": "port-A"}
        if mode == "native_reverse":
            update.update(
                coefficient_mode="native_correlation",
                specified_discharge_coefficient=None,
                specified_expansibility_factor=None,
            )
        request = request.model_copy(update=update)
    else:
        request = request.model_copy(
            update={
                "reference": request.reference.model_copy(update={"volume_basis": "same_composition_wet"})
            }
        )
    context = recontext(request, context)
    monkeypatch.setattr(
        "pipeline_integrity.gas_meter._backend",
        lambda: pytest.fail("unsupported boundary must not call native"),
    )
    record = evaluate_gas_meter(request, context)
    assert record["status"] == "out_of_scope" and not record["native_called"]


def test_explicit_reverse_pair_retains_nominal_sign(pair):
    request, context, forward = pair
    request = request.model_copy(
        update={
            "upstream_port_id": "port-B",
            "downstream_port_id": "port-A",
            "reversed_geometry_reviewed": True,
            "reversed_geometry_evidence_id": "authored",
        }
    )
    record = evaluate_gas_meter(request, context)
    assert record["status"] == "computed"
    for key in (
        "signed_mass_flow_kg_s",
        "signed_actual_volume_flow_m3_s",
        "signed_reference_volume_flow_m3_s",
    ):
        assert record["values"][key] == -forward["values"][key]


def test_zero_pair_is_a_limit_and_zero_native_correlation_is_withheld():
    for mode, meter in (("specified_pair", "unspecified meter"), ("native_correlation", "ISO 5167 orifice")):
        request, context = MAKE_CASE(reviewed=True, mode=mode, meter_type=meter)
        request = request.model_copy(update={"downstream_pressure_pa_abs": 500000.0})
        record = evaluate_gas_meter(request, context)
        if mode == "specified_pair":
            assert record["status"] == "computed" and record["values"]["signed_mass_flow_kg_s"] == 0
        else:
            assert record["status"] == "out_of_scope" and record["meter_called"] is False


def test_liquid_and_fully_supercritical_states_do_not_enter_gas_correlation():
    request, context = MAKE_CASE(reviewed=True, components=(GasComponent(fluid="Water", mole_fraction=1.0),))
    request = request.model_copy(
        update={
            "volume_basis": "same_composition_wet",
            "reference": request.reference.model_copy(update={"volume_basis": "same_composition_wet"}),
        }
    )
    context = recontext(request, context)
    record = evaluate_gas_meter(request, context)
    assert record["status"] == "out_of_scope" and record["native_called"] and not record["meter_called"]
    request, context = MAKE_CASE(
        reviewed=True, components=(GasComponent(fluid="Methane", mole_fraction=1.0),)
    )
    request = request.model_copy(
        update={
            "upstream_pressure_pa_abs": 5000000.0,
            "downstream_pressure_pa_abs": 4995000.0,
            "support": request.support.model_copy(update={"upstream_pressure_range_pa_abs": (50000.0, 1e7)}),
        }
    )
    record = evaluate_gas_meter(request, context)
    assert record["status"] == "out_of_scope" and record["native_called"] and not record["meter_called"]


def test_geometry_meaning_and_fit_extrapolation_are_refused():
    request, context = MAKE_CASE(reviewed=True, mode="native_correlation", meter_type="cone meter")
    request = request.model_copy(
        update={
            "geometry": request.geometry.model_copy(
                update={"secondary_dimension_meaning": "circular_throat_diameter"}
            )
        }
    )
    assert evaluate_gas_meter(request, context)["status"] == "out_of_scope"
    request, context = MAKE_CASE(reviewed=True, mode="native_correlation", meter_type="Hollingshead orifice")
    request = request.model_copy(
        update={"geometry": request.geometry.model_copy(update={"secondary_dimension_m": 0.04})}
    )
    record = evaluate_gas_meter(request, context)
    assert record["status"] == "out_of_scope" and "extrapolate" in str(record["notes"])


@pytest.mark.parametrize("field", ["upstream_pressure_pa_abs", "upstream_temperature_K", "time_s"])
def test_nonfinite_copied_inputs_do_not_turn_into_missing(monkeypatch, field):
    request, context = MAKE_CASE(reviewed=True)
    request = request.model_copy(update={field: float("nan")})
    monkeypatch.setattr(
        "pipeline_integrity.gas_meter._backend", lambda: pytest.fail("invalid input must not call backend")
    )
    with pytest.raises(ValueError):
        evaluate_gas_meter(request, context)


@pytest.mark.parametrize("mode", ["clock", "asset", "missing_evidence", "reference_metadata"])
def test_common_context_boundary_rejects_mismatch(mode):
    request, context = MAKE_CASE(reviewed=True)
    if mode in {"clock", "asset"}:
        context = context.model_copy(update={mode + "_id": "other"})
    elif mode == "missing_evidence":
        context = context.model_copy(update={"evidence": ()})
    else:
        context = context.model_copy(deep=True)
        context.metadata["gas_meter_boundary"]["reference"]["pressure_pa_abs"] = 100000.0
    with pytest.raises(ValueError):
        evaluate_gas_meter(request, context)


@pytest.mark.parametrize(
    "field", ["flow", "reference", "gamma", "phase", "native_call", "backend", "producer", "status"]
)
def test_rehashed_corruptions_fail_actual_numerical_replay(pair, field):
    request, context, original = pair
    record = deepcopy(original)
    if field == "flow":
        record["values"]["signed_mass_flow_kg_s"] *= 1.1
    elif field == "reference":
        record["values"]["signed_reference_volume_flow_m3_s"] *= 1.1
    elif field == "gamma":
        record["values"]["local_isentropic_exponent"] = 1.4
    elif field == "phase":
        next(call for call in record["native_calls"] if call["function"] == "CoolPropProvider.resolve")[
            "output"
        ]["phase"] = "supercritical"
    elif field == "native_call":
        record["native_calls"][-1]["inputs"]["P2"] -= 100
    elif field == "backend":
        record["backend"]["CoolProp_revision"] = "other"
    elif field == "producer":
        record["adapter_sha256"] = "0" * 64
    else:
        record["status"] = "needs_data"
    record["record_digest"] = digest({k: v for k, v in record.items() if k != "record_digest"})
    assert not verify_gas_meter_record(record, request, context)["valid"]
    with pytest.raises(ValueError):
        to_signal_frame(record, context)


def test_reference_not_requested_remains_unknown_and_signal_clock_is_explicit():
    request, context = MAKE_CASE(reviewed=True)
    request = request.model_copy(update={"require_reference_volume": False, "reference": None})
    context = recontext(request, context)
    records = []
    for time in (0.0, 2.0):
        records.append(evaluate_gas_meter(request.model_copy(update={"time_s": time}), context))
    frames = to_signal_frames(records, context)
    assert [f.time_s for f in frames] == [10.0, 12.0]
    assert frames[0].clock_id == context.clock_id and frames[0].origin == "synthetic"
    assert frames[0].values["signed_reference_volume_flow_m3_s"] is None
    assert not frames[0].valid["signed_reference_volume_flow_m3_s"]
    with pytest.raises(ValueError):
        to_signal_frames(records[::-1], context)


def test_backend_failure_is_preserved_as_an_attempt(monkeypatch):
    import fluids.flow_meter

    def failure(**kwargs):
        raise ValueError("authored native solver rejection")

    monkeypatch.setattr(fluids.flow_meter, "differential_pressure_meter_solver", failure)
    request, context = MAKE_CASE(reviewed=True, mode="native_correlation", meter_type="ISO 5167 orifice")
    record = evaluate_gas_meter(request, context)
    assert record["status"] == "out_of_scope" and record["meter_called"] is True
    assert record["native_calls"][-1]["status"] == "failed"
    assert "authored native solver rejection" in record["native_calls"][-1]["error"]


@pytest.mark.parametrize("water", ["Water", "H2O", "R718"])
def test_dry_basis_water_component_and_alias_are_not_accepted(water):
    request, context = MAKE_CASE(reviewed=True, components=(GasComponent(fluid=water, mole_fraction=1.0),))
    record = evaluate_gas_meter(request, context)
    assert record["status"] == "out_of_scope" and not record["meter_called"]
    assert "water" in str(record["notes"]).lower()


def test_canonical_duplicate_components_are_not_implicitly_combined():
    request, context = MAKE_CASE(
        reviewed=True,
        components=(
            GasComponent(fluid="Nitrogen", mole_fraction=0.5),
            GasComponent(fluid="N2", mole_fraction=0.5),
        ),
    )
    record = evaluate_gas_meter(request, context)
    assert record["status"] == "out_of_scope" and not record["meter_called"]
    assert "duplicate canonical" in str(record["notes"])
