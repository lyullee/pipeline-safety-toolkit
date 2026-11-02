"""Independent ODE reference for lumped-fluid/radial-solid heat feedback.

A two-cell cylindrical assembly with constant properties and an adiabatic
outer surface reduces to three thermal capacities joined by known radial
conductances. The 3x3 linear system below is solved by a matrix exponential;
the coupled production path is checked for grid convergence and energy closure.
All fixture values are authored and synthetic.
"""

import hashlib
import importlib
import math
import os
import runpy
from pathlib import Path
import sys

import numpy as np
import pytest
from scipy.linalg import expm

from pipeline_integrity.adapters import MaterialClient
from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.fluid_soil_feedback import (
    REVIEWS as COUPLING_REVIEWS,
    FluidSoilFeedbackRequest,
    bind_fluid_soil_feedback_context,
    evaluate_fluid_soil_feedback,
    fluid_soil_feedback_metadata,
)
from pipeline_integrity.material_source import material_source_fingerprint
from pipeline_integrity.pipe_soil_transient import MODEL, pipe_soil_boundary

_MAPPING_SOURCE = b"Authored mapping: linear fluid temperature boundary follows the coupled fluid endpoint over each interval."


def _owner_root():
    selected = os.environ.get('PIPELINE_SELECTED_MATERIAL_SOURCE_ROOT')
    if selected:
        path = Path(selected).resolve()
        root = path.parent if (path / 'matintegrity').is_dir() else path
        if not (root / 'src/matintegrity').is_dir():
            raise RuntimeError('Selected material owner source root is invalid')
        return root
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "owners" / "matintegrity"
        if (candidate / "src" / "matintegrity").is_dir():
            return candidate
    raise RuntimeError("A pinned matintegrity source checkout could not be located")


def _coupled_case(maximum_step_s):
    owner_root = _owner_root()
    source_fingerprint = material_source_fingerprint(owner_root)
    source_path = (owner_root / "src").resolve()
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
    # Bind every owner factory to the pinned source tree so default imports
    # cannot silently resolve the separately installed dependency copy.
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

    make_solid = runpy.run_path("examples/pipe_soil_transient.py")["make_case"]
    original, _ = make_solid(
        source_sha256=source_fingerprint["source_tree_sha256"],
        model_version=description["model_version"],
        schema_digest=digest(description["input_schema"]),
        count=1,
    )
    duration_s = 20.0
    solid = original.model_copy(
        update={
            "time_s": (0.0, duration_s),
            "maximum_step_s": maximum_step_s,
            "inner_boundary": original.inner_boundary.model_copy(
                update={
                    "convection_W_m2_K": (50.0, 50.0),
                    "ambient_temperature_K": (300.0, 300.0),
                    "radiative_environment_temperature_K": (300.0, 300.0),
                }
            ),
            "outer_boundary": original.outer_boundary.model_copy(
                update={"kind": "adiabatic", "temperature_K": None, "evidence_id": "authored"}
            ),
        }
    )
    coupling_mapping_id = "fluid_solid_mapping"
    coupling = FluidSoilFeedbackRequest(
        id="one_interval_heated_lumped_fluid",
        solid=solid,
        fluid_mass_kg=2.0,
        fluid_heat_capacity_J_kg_K=4000.0,
        initial_fluid_temperature_K=300.0,
        fluid_temperature_support_K=(290.0, 350.0),
        outlet_temperature_brackets_K=((295.0, 320.0),),
        mass_flow_in_equals_out_kg_s=(0.0,),
        inlet_temperature_K=(300.0,),
        heater_into_fluid_W=(1000.0,),
        fluid_boundary_mode="iterated_linear_fluid_temperature_ramp",
        fluid_boundary_mapping_evidence_id=coupling_mapping_id,
        evidence_ids=("authored", coupling_mapping_id),
        reviews=dict.fromkeys(COUPLING_REVIEWS, True),
        review_evidence_ids=dict.fromkeys(COUPLING_REVIEWS, coupling_mapping_id),
        coupling_owner_pins=fluid_soil_feedback_metadata(),
        maximum_trials_per_interval=80,
        maximum_total_owner_evaluations=500,
        maximum_total_owner_work_units=100_000_000,
        maximum_total_native_steps=200_000,
        fluid_energy_absolute_tolerance_J=1e-5,
        coupled_energy_absolute_tolerance_J=1e-5,
        energy_relative_tolerance=1e-9,
    )
    base_context = RunContext(
        asset_id=solid.asset_id,
        scenario_id=solid.event_id,
        clock_id=solid.clock_id,
        origin="simulation",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Authored constant-property radial assembly fixture",
                origin="synthetic",
                license_id="MIT",
            ),
            EvidenceRef(
                id=coupling_mapping_id,
                citation="Authored fluid/solid boundary mapping for an analytic coupling fixture",
                origin="user_declared",
                sha256=hashlib.sha256(_MAPPING_SOURCE).hexdigest(),
            ),
        ),
        metadata={"pipe_soil_boundary": pipe_soil_boundary(solid)},
    )
    context = bind_fluid_soil_feedback_context(coupling, base_context)
    return coupling, context, client, owner_root


def _independent_exact_endpoint(duration_s=20.0, heater_W=1000.0):
    # Cylindrical one-cell wall and one-cell soil from the public analytic
    # fixture: wall [0.10,0.12] m, soil [0.12,0.20] m, axial length 1 m.
    length = 1.0
    r_inner, r_interface, r_outer = 0.10, 0.12, 0.20
    k_wall, cv_wall = 10.0, 2.0e6
    k_soil, cv_soil = 1.0, 1.0e6
    wall_center = math.sqrt((r_inner**2 + r_interface**2) / 2.0)
    soil_center = math.sqrt((r_interface**2 + r_outer**2) / 2.0)
    area_inner = 2.0 * math.pi * r_inner * length
    film_resistance = 1.0 / (50.0 * area_inner)
    wall_half_resistance = math.log(wall_center / r_inner) / (2.0 * math.pi * length * k_wall)
    fluid_wall_conductance = 1.0 / (film_resistance + wall_half_resistance)
    interfacial_resistance = (
        math.log(r_interface / wall_center) / (2.0 * math.pi * length * k_wall)
        + math.log(soil_center / r_interface) / (2.0 * math.pi * length * k_soil)
    )
    wall_soil_conductance = 1.0 / interfacial_resistance
    fluid_capacity = 2.0 * 4000.0
    wall_capacity = cv_wall * math.pi * length * (r_interface**2 - r_inner**2)
    soil_capacity = cv_soil * math.pi * length * (r_outer**2 - r_interface**2)
    conductance = np.array(
        [
            [-fluid_wall_conductance, fluid_wall_conductance, 0.0],
            [fluid_wall_conductance, -(fluid_wall_conductance + wall_soil_conductance), wall_soil_conductance],
            [0.0, wall_soil_conductance, -wall_soil_conductance],
        ]
    )
    capacities = np.diag([fluid_capacity, wall_capacity, soil_capacity])
    operator = np.zeros((4, 4))
    operator[:3, :3] = np.linalg.solve(capacities, conductance)
    operator[0, 3] = heater_W / fluid_capacity
    return expm(operator * duration_s) @ np.array([300.0, 300.0, 300.0, 1.0])


def _endpoint_error(maximum_step_s):
    request, context, client, owner_root = _coupled_case(maximum_step_s)
    record = evaluate_fluid_soil_feedback(
        request, context, client=client, owner_source_root=owner_root
    )
    assert record["status"] == "computed_diagnostic", record.get("issues")
    assert record["values"] is not None
    assert len(record["intervals"]) == 1

    exact = _independent_exact_endpoint()
    final_fluid = record["values"]["final_fluid_temperature_K"]
    shells = record["values"]["final_solid_shells"]
    final_wall, final_soil = (shell["initial_cell_temperature_K"][0] for shell in shells)
    actual = np.array([final_fluid, final_wall, final_soil])
    error = float(np.max(np.abs(actual - exact[:3])))

    # Coupling must carry nonzero heater energy into the solid as fluid cools
    # below its uncoupled heater-only endpoint; total external heat remains 20 kJ.
    interval = record["intervals"][0]
    assert interval["inner_boundary_energy_into_solid_J"] > 0.0
    assert final_fluid < 300.0 + 1000.0 * 20.0 / (2.0 * 4000.0)
    assert interval["fluid_energy_change_J"] + interval["solid_energy_change_J"] == pytest.approx(
        1000.0 * 20.0, abs=2e-4
    )
    assert record["values"]["global_coupled_energy_residual_J"] == pytest.approx(0.0, abs=2e-4)
    assert error > 0.0
    assert error < 0.08
    return error


def test_lumped_fluid_and_radial_solid_feedback_converges_to_independent_three_capacity_ode():
    errors = [_endpoint_error(step) for step in (1.0, 0.5, 0.25)]
    assert errors[0] > errors[1] > errors[2]
    assert errors[-1] < errors[0] / 10.0
