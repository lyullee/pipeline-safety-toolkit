"""Authored correlated pressure-design inputs; reviews are off by default."""

from pathlib import Path

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.datasets import file_digest
from pipeline_integrity.metrology import MeasurementBasis
from pipeline_integrity.scheduling import PlanningResource
from pipeline_integrity.sensor_placement import (
    REVIEWS,
    CandidateSensor,
    DesignLinearization,
    DesignMeasurement,
    DesignParameter,
    JointDesignNoise,
    SensorPlacementRequest,
    boundary_metadata,
)


def make_case(*, reviewed=False):
    source = "authored"
    parameter = DesignParameter(
        id="leak_rate",
        basis=MeasurementBasis(
            quantity="mass_flow", unit="kg/s", definition_id="authored_local_leak_rate", evidence_id=source
        ),
        reference_scale=0.001,
    )
    candidates = tuple(
        CandidateSensor(
            id=name,
            location_id="location_" + name,
            coordinate_frame_id="authored_local_xyz",
            position_m=(float(i) * 100.0, 0.0, 0.0),
            physical_port_id="port_" + name,
            budget_use={"capital_count": 1},
            resource_use={"channels": 1},
            evidence_id=source,
        )
        for i, name in enumerate(("a", "b", "c"))
    )
    measurements = tuple(
        DesignMeasurement(
            id="pressure_" + s.id,
            sensor_id=s.id,
            basis=MeasurementBasis(
                quantity="pressure",
                unit="Pa",
                definition_id="authored_pressure_port_" + s.id,
                evidence_id=source,
                pressure_reference="absolute",
            ),
        )
        for s in candidates
    )
    model = DesignLinearization(
        model_id="authored_local_pressure_sensitivity",
        model_version="1",
        source_sha256=file_digest(Path(__file__)),
        configuration_sha256=digest({"definition": "authored same local derivative at three ports"}),
        linearization_point_sha256=digest({"operating_state": "fictional local point; no field model"}),
        measurement_ids=tuple(m.id for m in measurements),
        parameter_ids=(parameter.id,),
        jacobian=((100000.0,), (100000.0,), (100000.0,)),
        evidence_id=source,
    )
    noise = JointDesignNoise(
        measurement_ids=model.measurement_ids,
        covariance=((10000.0, 9000.0, 0.0), (9000.0, 10000.0, 0.0), (0.0, 0.0, 40000.0)),
        law="parameter_independent_gaussian_at_one_window",
        evidence_id=source,
    )
    request = SensorPlacementRequest(
        id="authored_correlated_placement",
        asset_id="authored_pipe",
        clock_id="authored_design_clock",
        event_id="one_joint_observation",
        parameters=(parameter,),
        measurements=measurements,
        candidates=candidates,
        linearization=model,
        noise=noise,
        criterion="D_opt",
        budgets={"capital_count": 2},
        budget_definition_ids={"capital_count": "authored_integer_quote_units"},
        resources=(PlanningResource(id="channels", capacity=2, evidence_id=source),),
        observation_start_s=0.0,
        observation_duration_s=10.0,
        evidence_id=source,
        reviews=dict.fromkeys(REVIEWS, True) if reviewed else {},
        review_evidence_ids=dict.fromkeys(REVIEWS, source) if reviewed else {},
    )
    context = RunContext(
        asset_id=request.asset_id,
        scenario_id="authored_static_design",
        clock_id=request.clock_id,
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id=source,
                citation="Original three-port local Gaussian design example; fictional geometry, sensitivities, covariance and quotes",
                origin="synthetic",
                limitations=("Not physical leak data, POD or a field placement recommendation",),
            ),
        ),
        metadata={"sensor_placement_boundary": boundary_metadata(request)},
    )
    return request, context
