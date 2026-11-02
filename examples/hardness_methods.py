"""Independently authored SI indentation examples, without measured specimens or acceptance rules."""

from pipeline_integrity.contracts import EvidenceRef, RunContext
from pipeline_integrity.hardness_exchange import REVIEWS, HardnessConditions, HardnessRequest


def make_cases():
    context = RunContext(
        asset_id="authored_hardness_specimen",
        scenario_id="authored_indentation_geometry",
        clock_id="test_elapsed_s",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored",
                citation="Original hypothetical force, indentation dimensions, material and test-condition declarations",
                origin="synthetic",
                license_id="MIT",
            ),
        ),
    )
    parameters = {
        "testing.brinell": dict(
            test_force_N=1000.0, ball_diameter_m=0.005, mean_indentation_diameter_m=0.003
        ),
        "testing.vickers": dict(test_force_N=98.0665, first_diagonal_m=0.00019, second_diagonal_m=0.00021),
    }
    cases = {
        method: HardnessRequest(
            method=method,
            asset_id=context.asset_id,
            location_id="authored_indentation_location",
            clock_id=context.clock_id,
            material_condition_id="authored_material_condition",
            parameters=p,
            conditions=HardnessConditions(
                test_id=method + ":authored_test",
                specimen_id="authored_specimen",
                dwell_s=10.0,
                surface_condition_id="authored_surface_condition",
                procedure_id="authored_geometry_demonstration",
                force_evidence_id="authored",
                length_evidence_id="authored",
                conditions_evidence_id="authored",
                geometry_applicable=True,
            ),
            applicability_reviews=dict.fromkeys(REVIEWS[method], True),
            evidence_ids=("authored",),
        )
        for method, p in parameters.items()
    }
    return cases, context


if __name__ == "__main__":
    from pipeline_integrity.hardness_exchange import evaluate_hardness, verify_hardness_record

    requests, ctx = make_cases()
    for name, request in requests.items():
        result = evaluate_hardness(request, ctx)
        verify_hardness_record(result)
        print(name, result["status"], result["values"])
