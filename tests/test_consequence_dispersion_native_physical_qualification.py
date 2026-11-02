"""Bounded local physical qualification of the native PRISM consequence path.

This test intentionally uses authored synthetic conditions.  It verifies the
source-to-cloud mass handoff and compressible-plane energy audit through the
catalogued ``PRISMConsequenceClient.evaluate`` path; it is not field validation
or a full facility-risk qualification.

External source roots are supplied by environment variables so that no local
user path is embedded in the test:

* ``PHYSICS_PRISM_SOURCE``: PRISM ``lh2-tno/src`` directory
* ``PHYSICS_LH2DT_ROOT``: local lh2dt source checkout
* ``PHYSICS_SLABX_ROOT``: pinned SLABx checkout
* ``PHYSICS_SLABX_LH2_ROOT``: pinned slabx-lh2 checkout
"""

from __future__ import annotations

import math
import os
import sys
from importlib.metadata import version
from pathlib import Path

import pytest

from pipeline_integrity.consequence_exchange import (
    CompressiblePlane,
    ConsequenceCase,
    PRISMConsequenceClient,
)
from pipeline_integrity.contracts import RunContext
from pipeline_integrity.equipment_exchange import EquipmentRequest, LH2EquipmentClient
from pipeline_integrity.release_exchange import PipeAccidentRequest, prism_release


def _roots():
    names = {
        "prism": "PHYSICS_PRISM_SOURCE",
        "lh2dt": "PHYSICS_LH2DT_ROOT",
        "slabx": "PHYSICS_SLABX_ROOT",
        "slabx_lh2": "PHYSICS_SLABX_LH2_ROOT",
    }
    roots = {}
    for key, env_name in names.items():
        value = os.environ.get(env_name)
        if not value:
            pytest.skip(f"local external source root is not set: {env_name}")
        roots[key] = Path(value).resolve()
        if not roots[key].exists():
            pytest.skip(f"local external source root is unavailable: {env_name}")
    sys.path[:0] = [str(roots["prism"]), str((roots["lh2dt"] / "src").resolve())]
    return roots


def _backend(roots):
    from lh2_tno.backend import BackendConfig, IsolatedBackend, PackagePin, tree_digest

    package_roots = {
        "prism_qra": roots["prism"] / "prism_qra",
        "prism_lh2_models": roots["prism"] / "prism_lh2_models",
        "slabx": roots["slabx"] / "src" / "slabx",
        "slabx_lh2": roots["slabx_lh2"] / "slabx_lh2",
    }
    return IsolatedBackend(
        BackendConfig(
            interpreter=sys.executable,
            timeout_s=60.0,
            packages=tuple(
                PackagePin(name=name, root=str(root), sha256=tree_digest(root))
                for name, root in package_roots.items()
            ),
            required_versions={
                name: version(name) for name in ("numpy", "scipy", "CoolProp", "pydantic")
            },
        )
    )


def _source(equipment, handle, context, step):
    history = equipment.export_accident_history(
        handle,
        PipeAccidentRequest(
            event_id="synthetic_pipe/release",
            port_id="failed_opening",
            clock_id=context.clock_id,
            start_time_s=0.0,
            source_id="authored_finite_horizon_reference",
            initial_pressure_pa_abs=1_000_000.0,
            initial_temperature_k=300.0,
            wall_temperature_k=300.0,
            ambient_pressure_pa_abs=101_325.0,
            ambient_temperature_k=288.15,
            failure_opening_diameter_m=0.001,
            discharge_coefficient=0.8,
            horizon_s=0.2,
            time_step_s=step,
            position_enu_m=(0.0, 0.0, 1.0),
            direction_enu=(1.0, 0.0, 0.0),
        ),
    )
    assert history.status == "exchange_checked", history.issues
    return prism_release(history)


def test_native_source_to_dispersion_chain_mass_energy_and_source_step_convergence():
    roots = _roots()
    context = RunContext(
        asset_id="synthetic_pipe",
        scenario_id="finite_horizon_dispersion",
        clock_id="elapsed",
        origin="synthetic",
    )
    equipment = LH2EquipmentClient(
        fluid="Hydrogen", property_basis="CoolProp8_HEOS_Hydrogen_default_reference"
    )
    handle = equipment.build(
        EquipmentRequest(
            model_id="dynamic_hem_pipe",
            parameter_source="authored_finite_horizon_reference",
            parameters={
                "length_m": 1.0,
                "inner_diameter_m": 0.05,
                "roughness_m": 1e-6,
                "wall_heat_capacity_J_K": 1e4,
                "local_loss_coefficient": 0.0,
                "fluid_wall_UA_W_K": 0.0,
                "ambient_temperature_K": 288.15,
                "ambient_UA_W_K": 0.0,
            },
        ),
        context,
    )
    coarse = _source(equipment, handle, context, 0.05)
    fine = _source(equipment, handle, context, 0.025)
    packet = coarse.native
    facility = {
        "id": "synthetic_consequence_facility",
        "configuration_origin": "synthetic",
        "environment": "outdoor",
        "components": [
            {
                "id": context.asset_id,
                "kind": "pipe",
                "position_enu_m": [0.0, 0.0, 1.0],
                "property_basis": packet.property_basis.model_dump(mode="json"),
                "parameters_si": {"length_m": 1.0, "diameter_m": 0.05},
            }
        ],
        "ports": [
            {
                "id": packet.port_id,
                "component_id": context.asset_id,
                "phase": "vapour",
                "direction": "out",
                "position_enu_m": [0.0, 0.0, 1.0],
            }
        ],
        "edges": [],
        "receptors": [{"id": "near", "position_enu_m": [2.0, 0.0, 1.0]}],
    }
    defaults = {
        "weather": {"wind_speed_m_s": 2.0, "wind_from_bearing_deg": 270.0},
        "ignition": {
            "direct_probability": 0.2,
            "delayed_probability_given_no_direct": 0.1,
            "explosion_probability_given_delayed": 0.25,
            "delayed_time_s": 0.1,
            "burn_back_duration_s": 0.1,
            "obstructed_energy_fraction": 0.05,
            "provenance": "authored synthetic conditional scenario assumptions",
        },
        "domain": {
            "downwind_bounds_m": [-1.0, 5.0],
            "crosswind_bounds_m": [-2.0, 2.0],
            "height_bounds_m": [0.0, 3.0],
            "cell_size_m": 1.0,
        },
        "evaluation_times_s": [0.0, 0.1, 0.2, 0.3],
        "radiative_fraction_range": [0.04, 0.08],
        "radiation_provenance": "authored uncalibrated sensitivity bounds",
        "release_termination": {
            "basis": "synthetic_diagnostic_horizon",
            "time_s": 0.2,
            "provenance": "finite source horizon; physical cessation is not inferred",
        },
        "source_operator": "integrated_transient",
        "dispersion_x_max_m": 5.0,
        "transition_dx_m": 0.25,
        "transition_x_max_m": 2.0,
        "n_puff_steps": 20,
    }
    case = ConsequenceCase(
        id="wind_270_small_domain",
        initiating_condition_id="release_initiated",
        facility=facility,
        defaults=defaults,
        evidence_ids=("authored:synthetic_geometry_and_boundary",),
        compressible_plane=CompressiblePlane(evidence_id="authored:plane_energy_reference"),
        maximum_cells=100,
        maximum_field_samples=1000,
        maximum_receptors=2,
    )
    client = PRISMConsequenceClient(_backend(roots))
    result = client.evaluate(coarse, case)
    fine_result = client.evaluate(fine, case.model_copy(update={"id": "wind_270_fine_source_step"}))

    assert result.status == "computed_diagnostic", result.issues
    assert fine_result.status == "computed_diagnostic", fine_result.issues
    assert result.physical_validation_claim is False
    assert result.native_result["data"]["full_chain_executed"] is True
    assert result.native_result["data"]["physical_validation_claim"] is False

    # Independent source inventory oracle: integrate the authored HEM mass
    # outlet and require the downstream cloud ledger to match the exact packet.
    expected_mass = coarse.native.cumulative_mass_out_kg
    fine_mass = fine.native.cumulative_mass_out_kg
    assert expected_mass > 0.0
    assert math.isclose(
        result.native_result["data"]["mass_ledger"]["released_kg"],
        expected_mass,
        rel_tol=1e-8,
        abs_tol=0.0,
    )
    residual = result.native_result["data"]["mass_ledger"]["end_to_end_residual_kg"]
    assert abs(residual) <= 1e-8 * expected_mass
    assert math.isclose(expected_mass, fine_mass, rel_tol=0.01, abs_tol=0.0)

    # The separately constructed compressible plane carries its independent
    # energy audit into the exact native consequence request.
    plane_audit = result.plane_evidence["generation_audit"]
    assert abs(plane_audit["max_energy_residual_j_per_kg"]) <= 1e-6

    receptor = result.native_result["data"]["receptors"][0]
    samples = receptor["concentration_history"]
    assert [row["time_s"] for row in samples] == sorted(row["time_s"] for row in samples)
    assert all(
        isinstance(row.get("h2_mole_fraction"), (int, float))
        and math.isfinite(row["h2_mole_fraction"])
        and 0.0 <= row["h2_mole_fraction"] <= 1.0
        for row in samples
    )
