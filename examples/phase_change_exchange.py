"""Authored phase/thermal cases; native calls require explicit reviewed owner sources."""

import argparse
import json
import sys
from pathlib import Path

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.phase_change_exchange import (
    REVIEWS,
    CryogenicInsulationCase,
    DynamicThermalCase,
    HeatPenetration,
    KineticInterfaceCase,
    PhaseChangeRequest,
    PhasePropertyBasis,
    SteadyThermalCase,
    UnsupportedPhaseCase,
    boundary_metadata,
    evaluate_phase_change,
    native_backend_metadata,
)
from pipeline_integrity.thermo import StateRequest

KINDS = (
    "kinetic_interface",
    "steady_vaporizer",
    "steady_reliquefier",
    "dynamic_vaporizer",
    "dynamic_reliquefier",
    "vacuum_insulation",
    "bulk_freezing",
    "ambient_frost",
)


def make_case(kind="kinetic_interface", *, reviewed=False, fluid="ParaHydrogen"):
    evidence = "authored:thermal_boundary"
    liquid = StateRequest(fluid=fluid, inputs={"P": 300000.0, "T": 20.0})
    gas = StateRequest(fluid=fluid, inputs={"P": 300000.0, "T": 30.0})
    if kind == "kinetic_interface":
        case = KineticInterfaceCase(
            interface_id="liquid-vapor-surface",
            liquid_port_id="liquid-side",
            vapor_port_id="vapor-side",
            area_m2=0.25,
            interface_temperature_K=20.3,
            vapor_state=StateRequest(fluid=fluid, inputs={"P": 100000.0, "T": 21.0}),
            mass_accommodation=0.001,
            energy_accommodation=0.5,
            maximum_relative_pressure_imbalance=0.1,
            maximum_relative_temperature_imbalance=0.1,
        )
    elif kind in {"steady_vaporizer", "steady_reliquefier"}:
        vaporizer = kind == "steady_vaporizer"
        case = SteadyThermalCase(
            kind=kind,
            inlet_port_id="inlet",
            outlet_port_id="outlet",
            inlet_state=liquid if vaporizer else gas,
            outlet_pressure_Pa_abs=280000.0,
            mass_flow_kg_s=0.002,
            capacity_W=400.0 if vaporizer else 2000.0,
            UA_W_K=50.0 if vaporizer else None,
            reservoir_temperature_K=300.0 if vaporizer else None,
            minimum_approach_K=5.0 if vaporizer else None,
            target_vapor_mass_fraction=None if vaporizer else 0.0,
        )
    elif kind in {"dynamic_vaporizer", "dynamic_reliquefier"}:
        vaporizer = kind == "dynamic_vaporizer"
        case = DynamicThermalCase(
            kind=kind,
            inlet_port_id="inlet",
            outlet_port_id="outlet",
            initial_fluid_state=liquid if vaporizer else gas,
            inlet_state=liquid if vaporizer else gas,
            initial_wall_temperature_K=25.0 if vaporizer else 22.0,
            fluid_volume_m3=0.01,
            wall_heat_capacity_J_K=10000.0,
            fluid_wall_UA_W_K=10.0,
            reservoir_temperature_K=300.0 if vaporizer else 18.0,
            reservoir_UA_W_K=0.0,
            capacity_W=2000.0,
            mass_flow_in_kg_s=0.0,
            mass_flow_out_kg_s=0.0,
            duration_s=0.01,
            maximum_reviewed_step_s=0.1,
        )
    elif kind == "vacuum_insulation":
        case = CryogenicInsulationCase(
            warm_boundary_id="ambient-side",
            cold_boundary_id="cryogenic-wall",
            area_m2=10.0,
            effective_emissivity=0.01,
            mli_solid_conductance_W_K=0.02,
            support_conductance_W_K=0.03,
            residual_gas_coefficient_W_m2_K_Pa=0.004,
            maximum_valid_vacuum_pressure_Pa_abs=10.0,
            warm_temperature_K=295.0,
            cold_temperature_K=21.0,
            vacuum_pressure_Pa_abs=0.01,
            penetrations=(HeatPenetration(id="neck", conductance_W_K=0.01, evidence_id=evidence),),
        )
    elif kind in {"bulk_freezing", "ambient_frost"}:
        case = UnsupportedPhaseCase(
            kind=kind,
            material=fluid if kind == "bulk_freezing" else "water_frost",
            requested_physics="moving solid/liquid front with a complete latent-energy ledger"
            if kind == "bulk_freezing"
            else "unclamped cryogenic humid-air deposition and latent-energy coupling",
        )
    else:
        raise ValueError("unknown example kind")
    request = PhaseChangeRequest(
        id=f"authored-{kind}",
        asset_id="thermal-pipe-asset",
        event_id="conditional-thermal-operation",
        clock_id="process-elapsed",
        time_s=12.0,
        property_basis=None
        if kind in {"vacuum_insulation", "bulk_freezing", "ambient_frost"}
        else PhasePropertyBasis(fluid=fluid, evidence_id=evidence),
        case=case,
        evidence_ids=(evidence,),
        reviews=dict.fromkeys(REVIEWS, reviewed),
        review_evidence_ids=dict.fromkeys(REVIEWS, evidence) if reviewed else {},
        owner_pins=native_backend_metadata()
        if reviewed and kind not in {"bulk_freezing", "ambient_frost"}
        else {},
    )
    context = RunContext(
        asset_id=request.asset_id,
        scenario_id="authored-thermal-operation",
        clock_id=request.clock_id,
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id=evidence,
                citation="Authored operating and geometry assumptions; no measured accuracy claim",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
        metadata={
            "phase_change_boundary": boundary_metadata(request),
            "location": "authored local thermal component",
        },
    )
    return request, context


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner", type=Path)
    parser.add_argument("--kind", choices=KINDS, default="kinetic_interface")
    parser.add_argument("--reviewed", action="store_true")
    args = parser.parse_args()
    if args.owner:
        sys.path.insert(0, str((args.owner / "src").resolve()))
    print(json.dumps(evaluate_phase_change(*make_case(args.kind, reviewed=args.reviewed)), indent=2))


if __name__ == "__main__":
    main()
