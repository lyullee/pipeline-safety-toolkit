"""End-to-end fluid/solid energy conservation with the pinned native thermal owner.

The closed-system reference is the first law: with an adiabatic outer surface,
no fluid throughput, and a constant heater, the summed fluid and solid stored
energy increase must equal heater power times elapsed time. This does not
validate spatial fluid transport, moisture migration, or field heat-transfer
coefficients.
"""

import importlib
import os
import runpy
import sys
from pathlib import Path

import pytest

from pipeline_integrity.adapters import MaterialClient
from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.fluid_soil_feedback import (
    REVIEWS as FEEDBACK_REVIEWS,
    FluidSoilFeedbackRequest,
    bind_fluid_soil_feedback_context,
    evaluate_fluid_soil_feedback,
    fluid_soil_feedback_metadata,
)
from pipeline_integrity.material_source import material_source_fingerprint
from pipeline_integrity.pipe_soil_transient import MODEL, PipeSoilBoundary, PipeSoilTransientRequest


def _case():
    # The regression runner selects a read-only owner checkout explicitly.
    selected = os.environ.get("PIPELINE_SELECTED_MATERIAL_SOURCE_ROOT")
    if selected:
        path = Path(selected).resolve()
        owner_source_root = path.parent if (path / "matintegrity").is_dir() else path
    else:
        # Retain the original staged HPC layout when no checkout was selected.
        owner_source_root = Path(__file__).resolve().parents[2] / "owners" / "materials"
    if not (owner_source_root / "src" / "matintegrity").is_dir():
        raise RuntimeError("the pinned matintegrity owner checkout is not staged for this HPC test")

    source_path = (owner_source_root / "src").resolve()
    loaded = sys.modules.get("matintegrity")
    loaded_path = None if loaded is None else getattr(loaded, "__file__", None)
    if loaded_path is not None and not Path(loaded_path).resolve().is_relative_to(source_path):
        for module_name in tuple(sys.modules):
            if module_name == "matintegrity" or module_name.startswith("matintegrity."):
                del sys.modules[module_name]
    pinned_source = str(source_path)
    sys.path[:] = [entry for entry in sys.path if entry != pinned_source]
    sys.path.insert(0, pinned_source)
    importlib.invalidate_caches()
    # Construct the client with factories imported from the pinned checkout.
    # MaterialClient() performs its own default imports, which can select the
    # installed dependency copy before the source guard verifies provenance.
    import matintegrity
    from matintegrity.contracts import Evidence as MaterialEvidence
    from matintegrity.contracts import RunContext as MaterialContext
    from matintegrity.registry import default_registry

    client = MaterialClient(
        evaluator=matintegrity.evaluate,
        context_factory=MaterialContext,
        evidence_factory=MaterialEvidence,
        registry_factory=default_registry,
    )
    description = client.describe(MODEL)
    source_pin = material_source_fingerprint(owner_source_root)["source_tree_sha256"]
    make_case = runpy.run_path("examples/pipe_soil_transient.py")["make_case"]
    solid, _ = make_case(
        source_sha256=source_pin,
        model_version=description["model_version"],
        schema_digest=digest(description["input_schema"]),
        count=2,
    )
    solid_data = solid.model_dump(mode="python")
    # One interval is intentional: this test checks the global coupled energy
    # identity, while a separate case performs time-step convergence.
    solid_data.update(time_s=(0.0, 1.0), maximum_step_s=1.0)
    solid_data["inner_boundary"] = solid.inner_boundary.model_copy(
        update={
            "convection_W_m2_K": (100.0, 100.0),
            "ambient_temperature_K": (300.0, 300.0),
            "emissivity": 0.0,
            "radiative_environment_temperature_K": (300.0, 300.0),
        }
    )
    solid_data["outer_boundary"] = PipeSoilBoundary(
        id="adiabatic_outer_surface", kind="adiabatic", evidence_id="authored"
    )
    solid = PipeSoilTransientRequest.model_validate(solid_data)

    evidence = (
        EvidenceRef(
            id="authored",
            citation="Authored synthetic geometry and property curves for a coupled balance case",
            origin="synthetic",
            sha256="a" * 64,
        ),
        EvidenceRef(
            id="fluid_clock_map",
            citation="Authored source-clock and interface-sign mapping for this synthetic case",
            origin="user_declared",
            sha256="b" * 64,
        ),
    )
    context = RunContext(
        asset_id=solid.asset_id,
        scenario_id=solid.event_id,
        clock_id=solid.clock_id,
        origin="synthetic",
        evidence=evidence,
    )
    request = FluidSoilFeedbackRequest(
        id="closed_fluid_solid_energy_reference",
        solid=solid,
        fluid_mass_kg=1.0,
        fluid_heat_capacity_J_kg_K=1000.0,
        initial_fluid_temperature_K=300.0,
        fluid_temperature_support_K=(250.0, 400.0),
        outlet_temperature_brackets_K=((300.001, 300.15),),
        mass_flow_in_equals_out_kg_s=(0.0,),
        inlet_temperature_K=(300.0,),
        heater_into_fluid_W=(100.0,),
        fluid_boundary_mode="iterated_linear_fluid_temperature_ramp",
        fluid_boundary_mapping_evidence_id="fluid_clock_map",
        evidence_ids=("authored", "fluid_clock_map"),
        reviews=dict.fromkeys(FEEDBACK_REVIEWS, True),
        review_evidence_ids=dict.fromkeys(FEEDBACK_REVIEWS, "fluid_clock_map"),
        coupling_owner_pins=fluid_soil_feedback_metadata(),
        fluid_energy_absolute_tolerance_J=1e-3,
        coupled_energy_absolute_tolerance_J=1e-3,
        energy_relative_tolerance=1e-7,
    )
    return request, bind_fluid_soil_feedback_context(request, context), client, owner_source_root


def test_pinned_native_owner_closes_fluid_and_solid_energy_under_heating():
    request, context, client, owner_source_root = _case()
    record = evaluate_fluid_soil_feedback(
        request, context, client=client, owner_source_root=owner_source_root
    )
    if record["status"] != "computed_diagnostic":
        withheld = record.get("withheld_native_trial") or {}
        native_receipt = withheld.get("native_receipt") or {}
        native_payload = native_receipt.get("native", {}).get("payload", {})
        pytest.fail(
            "fluid/soil owner did not accept the analytic energy case: "
            + repr(
                {
                    "feedback_issues": record["issues"],
                    "solid_status": withheld.get("status"),
                    "solid_issues": withheld.get("issues"),
                    "solid_audit": withheld.get("audit"),
                    "native_status": native_payload.get("status"),
                    "native_maximum_relative_energy_error": native_payload.get("values", {}).get(
                        "maximum_relative_discrete_energy_balance_error"
                    ),
                }
            )
        )
    assert record["owner_source_pin_report"]["source_unchanged"] is True
    assert record["owner_source_pin_report"]["before"] == record["owner_source_pin_report"]["after"]
    assert len(record["intervals"]) == 1

    interval = record["intervals"][0]
    # Independent global balance: closed outer boundary, no mass throughput,
    # and 100 W applied for one second yield exactly 100 J stored in total.
    assert interval["heater_into_fluid_J"] == pytest.approx(100.0, abs=1e-12)
    assert interval["advective_net_into_fluid_J"] == pytest.approx(0.0, abs=1e-12)
    assert interval["outer_boundary_energy_into_solid_J"] == pytest.approx(0.0, abs=1e-12)
    assert interval["inner_boundary_energy_into_solid_J"] > 0.0
    assert interval["solid_energy_change_J"] > 0.0
    assert record["values"]["final_fluid_temperature_K"] > 300.0
    stored_energy_change = interval["fluid_energy_change_J"] + interval["solid_energy_change_J"]
    assert stored_energy_change == pytest.approx(100.0, abs=1e-2)
    assert record["values"]["global_coupled_energy_residual_J"] == pytest.approx(0.0, abs=1e-2)
