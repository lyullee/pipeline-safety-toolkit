"""Deferred native/reference soil-pipe tests; no execution qualification implied."""

from copy import deepcopy

import pytest

from examples.soil_pipe import make_case
from pipeline_integrity.contracts import digest
from pipeline_integrity.soil_pipe import (
    SoilPipeRequest,
    evaluate_soil_pipe,
    soil_pipe_boundary_metadata,
    verify_soil_pipe_record,
)


def amend(q, c, **changes):
    q = SoilPipeRequest.model_validate(q.model_copy(update=changes).model_dump(mode="python"))
    return q, c.model_copy(update={"metadata": {"soil_pipe_boundary": soil_pipe_boundary_metadata(q)}})


def soil_change(q, c, **changes):
    return amend(q, c, soil_supports=(q.soil_supports[0].model_copy(update=changes),))


def body_change(q, c, **changes):
    return amend(q, c, body_states=(q.body_states[0].model_copy(update=changes),))


def test_default_never_calls_native(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unreviewed soil basis invoked structural owner")

    monkeypatch.setattr("pipeline_integrity.soil_pipe.solve_frame", forbidden)
    q, c = make_case()
    r = evaluate_soil_pipe(q, c)
    assert r["status"] == "needs_data" and r["structural_record"] is None
    assert verify_soil_pipe_record(r, request=q, context=c)


@pytest.mark.parametrize("ground", [-0.001, 0.001])
def test_cantilever_tip_spring_matches_independent_closed_form(ground):
    q, c = make_case(reviewed=True, ground_m=ground)
    r = evaluate_soil_pipe(q, c)
    assert r["status"] == "computed_diagnostic"
    section, material = q.frame.sections[0], q.frame.materials[0]
    beam_stiffness = 3 * material.young_modulus_pa * section.iz_m4 / 2**3
    expected = 100000 * ground / (beam_stiffness + 100000)
    response = r["solution"]["soil_response"][0]["axes"][0]
    assert response["pipe_displacement_m"] == pytest.approx(expected, rel=1e-8)
    assert response["soil_force_on_pipe_N"] == pytest.approx(beam_stiffness * expected, rel=1e-8)
    assert response["pipe_force_on_soil_N"] == -response["soil_force_on_pipe_N"]
    assert r["solution"]["physical_static_balance"]["passed"]
    assert r["solution"]["physical_nodal_balance"]["passed"]
    assert r["solution"]["spring_law_passed"]
    assert verify_soil_pipe_record(r, request=q, context=c)


def test_native_zero_datum_reaction_is_not_reported_as_physical_soil_reaction():
    q, c = make_case(reviewed=True)
    r = evaluate_soil_pipe(q, c)
    response = r["solution"]["soil_response"][0]["axes"][0]
    assert response["soil_force_on_pipe_N"] == pytest.approx(
        response["native_zero_datum_spring_force_N"] - 100
    )


@pytest.mark.parametrize("behavior", ["compression_only", "gap"])
def test_separation_is_not_silently_solved_as_bilateral(behavior, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("unsupported contact model invoked linear solver")

    monkeypatch.setattr("pipeline_integrity.soil_pipe.solve_frame", forbidden)
    q, c = make_case(reviewed=True)
    q, c = soil_change(q, c, behavior=behavior)
    assert evaluate_soil_pipe(q, c)["status"] == "out_of_scope"


@pytest.mark.parametrize(
    "changes", [{"ground_displacement_m": None}, {"stiffness_N_m": {"DY": None}}, {"reviewed": False}]
)
def test_missing_ground_data_withholds_native_result(changes):
    q, c = make_case(reviewed=True)
    q, c = soil_change(q, c, **changes)
    assert evaluate_soil_pipe(q, c)["status"] == "needs_data"


def test_buoyancy_can_reverse_net_body_force_without_clipping():
    q, c = make_case(reviewed=True, ground_m=0)
    q, c = body_change(
        q,
        c,
        pipe_and_attached_mass_kg_m=1,
        displaced_area_m2=0.002,
        immersed_fraction=1,
        external_density_kg_m3=1000,
    )
    r = evaluate_soil_pipe(q, c)
    assert r["status"] == "computed_diagnostic"
    assert r["body_load_ledger"][0]["effective_mass_kg_m"] == -1
    assert r["body_load_ledger"][0]["body_force_N_m"][1] == pytest.approx(9.80665)
    assert r["solution"]["physical_static_balance"]["passed"]


def test_unknown_body_load_is_not_zero():
    q, c = make_case(reviewed=True)
    q, c = body_change(q, c, contents_mass_kg_m=None)
    r = evaluate_soil_pipe(q, c)
    assert r["status"] == "needs_data" and r["body_load_ledger"] is None


def test_zero_ground_motion_and_zero_body_load_give_zero_response():
    q, c = make_case(reviewed=True, ground_m=0)
    r = evaluate_soil_pipe(q, c)
    assert r["solution"]["soil_response"][0]["axes"][0]["soil_force_on_pipe_N"] == pytest.approx(0, abs=1e-10)
    assert r["solution"]["members"][0]["sampled_bending_moment_peak_Nm"] == pytest.approx(0, abs=1e-10)


def test_geometry_stiffness_boundary_and_nan_rejected():
    q, c = make_case(reviewed=True)
    with pytest.raises(ValueError):
        soil_change(q, c, node_id="anchor")
    with pytest.raises(ValueError):
        body_change(q, c, outer_diameter_m=0.11)
    with pytest.raises(ValueError):
        soil_change(q, c, ground_displacement_m=(0, float("nan"), 0))
    for bad in (
        c.model_copy(update={"metadata": {}}),
        c.model_copy(update={"clock_id": "other"}),
        c.model_copy(update={"evidence": ()}),
    ):
        with pytest.raises(ValueError):
            evaluate_soil_pipe(q, bad)


@pytest.mark.parametrize("field", ["reaction", "balance", "bending", "source", "qualification"])
def test_rehashed_engineering_mutation_fails_replay(field):
    q, c = make_case(reviewed=True)
    r = deepcopy(evaluate_soil_pipe(q, c))
    if field == "reaction":
        r["solution"]["soil_response"][0]["axes"][0]["soil_force_on_pipe_N"] += 1
    elif field == "balance":
        r["solution"]["physical_static_balance"]["force_residual_n"][0] += 1
    elif field == "bending":
        r["solution"]["members"][0]["sampled_bending_moment_peak_Nm"] += 1
    elif field == "source":
        r["structural_owner_sha256"] = "0" * 64
    else:
        r["contact_separation_or_yield_solved"] = True
    r["record_digest"] = digest({k: v for k, v in r.items() if k != "record_digest"})
    with pytest.raises(ValueError):
        verify_soil_pipe_record(r, request=q, context=c)
