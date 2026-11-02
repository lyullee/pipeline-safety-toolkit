"""Original map knots demonstrate accounting, not an actual compressor's performance."""

import argparse
import json
import sys
from pathlib import Path

from pipeline_integrity.compressor_train import (
    REVIEWS,
    CompressorMap,
    CompressorStage,
    CompressorTrainRequest,
    IntercoolerBoundary,
    boundary_metadata,
    evaluate_compressor_train,
    native_backend_metadata,
)
from pipeline_integrity.contracts import EvidenceRef, RunContext


def make_case(
    *, reviewed=False, fluid="Hydrogen", stage_count=2, cooled=True, convention="reference_corrected"
):
    evidence = "authored"
    speeds = (1000.0, 2000.0)
    flows = (0.005, 0.06, 0.12)
    mp = CompressorMap(
        id="authored-bilinear-map",
        fluid=fluid,
        coordinate_convention=convention,
        reference_pressure_pa_abs=100000.0 if convention == "reference_corrected" else None,
        reference_temperature_K=300.0 if convention == "reference_corrected" else None,
        speed_rad_s=speeds,
        mass_flow_kg_s=flows,
        pressure_ratio=tuple(tuple(1.2 + 0.0005 * n - 2 * w for w in flows) for n in speeds),
        isentropic_efficiency=tuple(
            tuple(0.78 + 0.00001 * (n - 1500) - 0.2 * (w - 0.06) for w in flows) for n in speeds
        ),
        surge_flow_kg_s=(0.007, 0.009),
        choke_flow_kg_s=(0.11, 0.115),
        inlet_pressure_range_pa_abs=(10000.0, 2e7),
        inlet_temperature_range_K=(50.0, 1000.0),
        evidence_id=evidence,
    )
    stages = []
    inlet = "train-inlet"
    for i in range(stage_count):
        sid = f"stage-{i + 1}"
        hot = sid + ":hot"
        cool = cooled and i < stage_count - 1
        outlet = sid + ":cooled" if cool else hot
        boundary = (
            IntercoolerBoundary(
                mode="outlet_temperature",
                pressure_drop_pa=3000.0,
                outlet_total_temperature_K=310.0,
                sink_temperature_K=290.0,
                minimum_approach_K=5.0,
                available_heat_rejection_W=1e6,
                evidence_id=evidence,
            )
            if cool
            else IntercoolerBoundary(mode="none", pressure_drop_pa=0.0, evidence_id=evidence)
        )
        stages.append(
            CompressorStage(
                id=sid,
                shaft_id=f"shaft-{i + 1}",
                inlet_port_id=inlet,
                compressor_outlet_port_id=hot,
                outlet_port_id=outlet,
                speed_rad_s=1500.0,
                performance_map=mp,
                mechanical_efficiency=0.95,
                drive_efficiency=0.92,
                minimum_surge_flow_margin_fraction=0.05,
                minimum_choke_flow_margin_fraction=0.02,
                intercooler=boundary,
                evidence_id=evidence,
            )
        )
        inlet = outlet
    q = CompressorTrainRequest(
        train_id="authored-train",
        asset_id="compressor-train",
        clock_id="process-elapsed",
        event_id="authored-operating-point",
        time_s=2.0,
        clock_offset_s=10.0,
        source_port_id="train-inlet",
        target_port_id=inlet,
        fluid=fluid,
        energy_reference=f"CoolProp-HEOS:{fluid}:native",
        inlet_total_pressure_pa_abs=100000.0,
        inlet_total_temperature_K=300.0,
        mass_flow_kg_s=0.04,
        stages=tuple(stages),
        evidence_ids=(evidence,),
        reviews={key: True for key in REVIEWS} if reviewed else {},
        review_evidence_ids={key: evidence for key in REVIEWS} if reviewed else {},
        owner_pins=native_backend_metadata() if reviewed else {},
    )
    c = RunContext(
        asset_id=q.asset_id,
        scenario_id="authored-steady-train",
        clock_id=q.clock_id,
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id=evidence,
                citation="Original rectilinear map and declared total-state/cooling conditions; no manufacturer or field qualification",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
        metadata={"compressor_train_boundary": boundary_metadata(q)},
    )
    return q, c


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--owner-root", type=Path)
    parser.add_argument("--reviewed", action="store_true")
    args = parser.parse_args()
    if args.owner_root is not None:
        sys.path.insert(0, str((args.owner_root / "src").resolve()))
    print(json.dumps(evaluate_compressor_train(*make_case(reviewed=args.reviewed)), indent=2))
