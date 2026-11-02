"""Original static inventory example; install engineering/thermo extras for the native shape and EOS."""

import json

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.tanks import (
    AxisymmetricTank,
    PropertyTankDensity,
    TankHead,
    TankInventoryRequest,
    TankObservation,
    evaluate_tank_inventory,
)
from pipeline_integrity.thermo import StateRequest


def make_case():
    context = RunContext(
        asset_id="authored_tank",
        scenario_id="static_inventory",
        clock_id="elapsed",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="reference",
                citation="Authored geometry, level and uniform state assumptions",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    head = TankHead(kind="ellipsoidal", depth_m=0.4)
    request = TankInventoryRequest(
        geometry=AxisymmetricTank(
            inner_diameter_m=2.0,
            straight_length_m=4.0,
            horizontal=True,
            side_a=head,
            side_b=head,
            evidence_id="reference",
        ),
        observation=TankObservation(
            tank_id=context.asset_id,
            clock_id=context.clock_id,
            time_s=0.0,
            quantity="level_from_internal_bottom",
            value=0.8,
            unit="m",
            evidence_id="reference",
        ),
        liquid_density=PropertyTankDensity(
            state=StateRequest(fluid="Water", inputs={"P": 101325.0, "T": 293.15}), evidence_id="reference"
        ),
        headspace_density=PropertyTankDensity(
            state=StateRequest(fluid="Air", inputs={"P": 101325.0, "T": 293.15}), evidence_id="reference"
        ),
    )
    return request, context


if __name__ == "__main__":
    print(json.dumps(evaluate_tank_inventory(*make_case()), indent=2))
