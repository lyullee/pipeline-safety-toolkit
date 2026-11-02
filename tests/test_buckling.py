"""Deferred native eigenproblem/reference tests; not executed during implementation."""

import math
from copy import deepcopy
from pathlib import Path

import pytest

from examples.buckling import make_case
from pipeline_integrity.buckling import (
    BucklingRequest,
    PyNiteBucklingProvider,
    buckling_boundary_metadata,
    evaluate_buckling,
    verify_buckling_record,
)
from pipeline_integrity.contracts import digest
from pipeline_integrity.datasets import file_digest


def amend(q, c, **changes):
    q = BucklingRequest.model_validate(q.model_copy(update=changes).model_dump(mode="python"))
    return q, c.model_copy(update={"metadata": {"buckling_boundary": buckling_boundary_metadata(q)}})


def test_default_is_not_a_static_analysis_disguised_as_buckling(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unreviewed buckling basis invoked native owner")

    monkeypatch.setattr("pipeline_integrity.buckling.solve_frame", forbidden)
    q, c = make_case()
    r = evaluate_buckling(q, c)
    assert r["status"] == "needs_data" and r["reference_frame_record"] is None
    assert r["solver"] is None and r["solution"] is None
    assert verify_buckling_record(r, request=q, context=c)


def test_native_pinned_column_converges_to_independent_euler_reference():
    q, c = make_case(reviewed=True, elements=8)
    r = evaluate_buckling(q, c)
    assert r["status"] == "computed_diagnostic"
    section, material = q.frame.sections[0], q.frame.materials[0]
    reference = math.pi**2 * material.young_modulus_pa * section.iy_m4 / 4**2 / 1000
    assert r["solution"]["critical_positive_load_factor"] == pytest.approx(reference, rel=3e-4)
    assert r["matrix_receipt"]["prestress_audit"]["members"][0][
        "geometric_stiffness_axial_force_tension_positive_N"
    ] == pytest.approx(-1000)
    assert all(
        m["relative_eigen_residual"] < q.relative_eigen_residual_tolerance for m in r["solution"]["modes"]
    )
    assert not r["shell_collapse_plasticity_or_imperfection_solved"]


def test_mesh_refinement_is_checked_without_claiming_field_validation():
    results = []
    for count in (2, 8):
        q, c = make_case(reviewed=True, elements=count)
        r = evaluate_buckling(q, c)
        results.append(r["solution"]["critical_positive_load_factor"])
    assert results[1] < results[0]


def test_reference_load_scaling_and_gauge_congruence_preserve_physical_critical_load():
    q, c = make_case(reviewed=True, reference_force_N=-2000)
    one = evaluate_buckling(q, c)
    q, c = amend(q, c, translation_gauge_m=0.1, rotation_gauge_rad=0.2)
    two = evaluate_buckling(q, c)
    assert one["solution"]["critical_positive_load_factor"] == pytest.approx(
        two["solution"]["critical_positive_load_factor"], rel=1e-8
    )
    q, c = make_case(reviewed=True, reference_force_N=-1000)
    three = evaluate_buckling(q, c)
    assert 2 * one["solution"]["critical_positive_load_factor"] == pytest.approx(
        three["solution"]["critical_positive_load_factor"], rel=1e-8
    )


def test_tension_is_not_flipped_into_positive_compression_buckling():
    q, c = make_case(reviewed=True, reference_force_N=1000)
    r = evaluate_buckling(q, c)
    assert r["status"] == "no_positive_eigenvalue_in_supplied_model"
    assert r["solution"]["critical_positive_load_factor"] is None


class MutatedNativeProvider:
    def __init__(self, kind):
        self.kind = kind

    def assemble(self, request, context, reference_record):
        receipt = PyNiteBucklingProvider().assemble(request, context, reference_record)
        receipt = receipt.model_copy(
            update={"provider_id": "mutated_test_provider", "provider_sha256": file_digest(Path(__file__))}
        )
        if self.kind == "dofs":
            return receipt.model_copy(update={"dof_order": tuple(reversed(receipt.dof_order))})
        if self.kind == "supports":
            return receipt.model_copy(update={"fixed_dof_indices": ()})
        if self.kind == "reference":
            return receipt.model_copy(update={"reference_record_digest": "0" * 64})
        matrix = [list(row) for row in receipt.elastic_stiffness]
        if self.kind == "asymmetric":
            matrix[6][7] += 1e9
        else:
            matrix = [[0.0] * len(matrix) for row in matrix]
        return receipt.model_copy(update={"elastic_stiffness": tuple(tuple(row) for row in matrix)})


@pytest.mark.parametrize("kind", ["dofs", "supports", "reference", "asymmetric", "zero_stiffness"])
def test_provider_matrix_dof_support_and_mechanism_audits(kind):
    q, c = make_case(reviewed=True)
    q, c = amend(
        q, c, provider_id="mutated_test_provider", expected_provider_sha256=file_digest(Path(__file__))
    )
    r = evaluate_buckling(q, c, provider=MutatedNativeProvider(kind))
    assert r["status"] == "out_of_scope" and r["solution"] is None


def test_external_provider_requires_actual_callback_and_explicit_source_pin():
    q, c = make_case(reviewed=True)
    q, c = amend(q, c, provider_id="missing_owner")
    assert evaluate_buckling(q, c)["status"] == "needs_data"


def test_releases_imposed_settlement_budget_and_context_rejected():
    q, c = make_case(reviewed=True)
    release = q.frame.members[0].model_copy(update={"rotation_releases": ("Rzi",)})
    with pytest.raises(ValueError):
        amend(q, c, frame=q.frame.model_copy(update={"members": (release,) + q.frame.members[1:]}))
    support = q.frame.supports[0].model_copy(update={"imposed_translations_m": {"DY": 0.001}})
    with pytest.raises(ValueError):
        amend(q, c, frame=q.frame.model_copy(update={"supports": (support,) + q.frame.supports[1:]}))
    with pytest.raises(ValueError):
        amend(q, c, maximum_full_dofs=12)
    with pytest.raises(ValueError):
        evaluate_buckling(q, c.model_copy(update={"metadata": {}}))


@pytest.mark.parametrize("field", ["factor", "mode", "matrix", "source", "qualification"])
def test_rehashed_engineering_mutation_fails_actual_provider_replay(field):
    q, c = make_case(reviewed=True, elements=2)
    r = deepcopy(evaluate_buckling(q, c))
    if field == "factor":
        r["solution"]["critical_positive_load_factor"] *= 2
    elif field == "mode":
        r["solution"]["modes"][0]["physical_mode_by_full_dof"][0] = 1
    elif field == "matrix":
        r["matrix_receipt"]["elastic_stiffness"][0][0] += 1
    elif field == "source":
        r["structural_owner_sha256"] = "0" * 64
    else:
        r["shell_collapse_plasticity_or_imperfection_solved"] = True
    r["record_digest"] = digest({k: v for k, v in r.items() if k != "record_digest"})
    with pytest.raises(ValueError):
        verify_buckling_record(r, request=q, context=c)
