"""Independent closed-form checks for finite-sample Findley plane screening.

These cases qualify only the discrete shear-chord and normal-maximum arithmetic
for caller-specified candidate planes; they do not validate calibration or
fatigue-life transfer.
"""

import math

from matintegrity.contracts import Evidence
from matintegrity.models.fatigue import CriticalPlaneCandidate
from matintegrity.models.fatigue_findley import (
    CriticalPlaneFindleyStressHistory,
    critical_plane_findley_parameter,
)


def _history(tensors, *, planes=None, closure_limit=1e-9, budget=50_000_000):
    tensor_sha = "a" * 64
    calibration_sha = "b" * 64
    evidence = (
        Evidence(source_id="tensor-fixture", origin="synthetic", citation="synthetic tensor oracle", sha256=tensor_sha),
        Evidence(source_id="calibration-fixture", origin="user_declared", citation="explicit test calibration", sha256=calibration_sha),
    )
    return CriticalPlaneFindleyStressHistory(
        assessment_id="assessment",
        asset_id="asset",
        location_id="location",
        time_basis_id="cycle",
        material_condition_id="material",
        load_case_id="load",
        cycle_id="representative-cycle",
        tensor_source_id="tensor-fixture",
        tensor_provider_id="tensor-fixture",
        tensor_provider_revision="1",
        tensor_record_id="tensor-record",
        tensor_content_sha256=tensor_sha,
        coordinate_frame_id="cartesian",
        stress_measure="cauchy",
        component_order="xx,yy,zz,xy,yz,zx",
        sample_ids=tuple(f"s{i}" for i in range(len(tensors))),
        stress_tensor_components_Pa=tuple(tuple(row) for row in tensors),
        candidate_planes=tuple(planes or (
            CriticalPlaneCandidate(plane_id="z-normal-x-shear", normal=[0, 0, 1], in_plane_direction=[1, 0, 0]),
        )),
        representative_cycle_declared=True,
        maximum_stress_closure_error_Pa=closure_limit,
        findley_coefficient=0.5,
        reference_parameter_strength_Pa=10.0,
        reference_life_cycles=1000.0,
        calibration_provider_id="calibration-fixture",
        calibration_provider_revision="1",
        calibration_record_id="calibration-record",
        calibration_sha256=calibration_sha,
        evidence=evidence,
        usage_rights_evidence=(Evidence(source_id="synthetic-fixture", origin="synthetic", citation="test-only generated inputs", license_id="CC0-1.0"),),
        maximum_shear_pair_evaluations=budget,
        tensor_units_and_component_order_reviewed=True,
        candidate_plane_set_and_coverage_reviewed=True,
        cycle_definition_and_closure_reviewed=True,
        findley_coefficient_and_calibration_basis_reviewed=True,
        calibration_same_material_and_reference_life_reviewed=True,
        usage_rights_reviewed=True,
        computational_use_authorized=True,
    )


def test_uniaxial_normal_and_shear_match_findley_closed_form():
    result = critical_plane_findley_parameter(_history([
        (0, 0, 2, 0, 0, 0),
        (0, 0, 4, 0, 0, 10),
        (0, 0, 2, 0, 0, 0),
    ]))
    assert result.status == "computed"
    assert result.values["maximum_findley_parameter_Pa"] == 7.0  # 10/2 + 0.5*4
    assert result.values["governing_plane_id"] == "z-normal-x-shear"
    assert result.values["candidate_plane_results"][0]["shear_amplitude_endpoint_sample_ids"] == ["s0", "s1"]


def test_two_component_shear_chord_uses_euclidean_diameter():
    result = critical_plane_findley_parameter(_history([
        (0, 0, 0, 0, 0, 0),
        (0, 0, 0, 0, 0, 4),
        (0, 0, 0, 0, 4, 0),
        (0, 0, 0, 0, 0, 0),
    ]))
    assert result.status == "computed"
    # On the z-normal plane, the shear trajectory reaches orthogonal vectors
    # (4,0) and (0,-4), whose diameter is 4*sqrt(2).
    assert math.isclose(result.values["candidate_plane_results"][0]["shear_stress_amplitude_Pa"], 2 * math.sqrt(2), rel_tol=1e-14)


def test_declared_planes_are_compared_and_governing_margin_is_consistent():
    planes = (
        CriticalPlaneCandidate(plane_id="x", normal=[1, 0, 0], in_plane_direction=[0, 1, 0]),
        CriticalPlaneCandidate(plane_id="z", normal=[0, 0, 1], in_plane_direction=[1, 0, 0]),
    )
    result = critical_plane_findley_parameter(_history([
        (0, 0, 0, 0, 0, 0),
        (0, 0, 4, 0, 0, 10),
        (0, 0, 0, 0, 0, 0),
    ], planes=planes))
    assert result.status == "computed"
    assert result.values["governing_plane_id"] == "z"
    assert result.values["margin_to_reference_strength_Pa"] == 3.0


def test_nonclosed_representative_cycle_is_not_qualified_as_computed():
    result = critical_plane_findley_parameter(_history([
        (0, 0, 0, 0, 0, 0),
        (0, 0, 2, 0, 0, 1),
        (0, 0, 0, 0, 0, 1),
    ]))
    assert result.status == "needs_data"
    assert result.values["reason"] == "representative_cycle_or_stress_endpoint_closure_is_unresolved"


def test_pair_budget_prevents_unbounded_quadratic_work():
    result = critical_plane_findley_parameter(_history([
        (0, 0, 0, 0, 0, 0),
        (0, 0, 1, 0, 0, 1),
        (0, 0, 0, 0, 0, 0),
    ], budget=2))
    assert result.status == "out_of_scope"
    assert result.values["required_shear_pair_evaluations"] == 3
