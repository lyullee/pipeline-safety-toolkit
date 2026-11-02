"""Original MIT synthetic corpus; no standard, law, book or user document content."""

from datetime import UTC, datetime

from pipeline_integrity.contracts import EvidenceRef, RunContext, digest
from pipeline_integrity.knowledge_exchange import (
    METHODS,
    REVIEWS,
    KnowledgeCandidate,
    KnowledgePassage,
    KnowledgeRequest,
    KnowledgeSource,
    evaluate_knowledge,
)


def make_cases(source_sha256=None):
    context = RunContext(
        asset_id="synthetic-pipe-A",
        scenario_id="offline-review",
        clock_id="declared-clock",
        origin="synthetic",
        evidence=(
            EvidenceRef(
                id="authored-fixture",
                citation="Original synthetic knowledge fixture, revision 1",
                origin="synthetic",
                locator="examples/knowledge_exchange.py:make_cases",
                license_id="MIT",
                limitations=("Not an engineering requirement or operational instruction.",),
            ),
        ),
    )
    sources = []
    paragraphs = (
        (
            "fixture-pressure",
            "DEMO-PRESSURE",
            "Pressure review exercise",
            (
                "Synthetic pressure exercise: review a pending threshold of 200000 Pa absolute for pipe A. This number is invented for software verification.",
                "Synthetic pressure evidence must preserve asset, clock, unit, source revision and a human review record. It grants no operating permission.",
            ),
        ),
        (
            "fixture-temperature",
            "DEMO-TEMPERATURE",
            "Temperature review exercise",
            (
                "Synthetic temperature exercise: the placeholder observation is 300 K for pipe A. It is not a measured operating condition.",
                "Synthetic missing evidence must remain unresolved. A retrieved passage is not a reasoned answer, regulation, or equipment command.",
            ),
        ),
    )
    for source_id, code, title, texts in paragraphs:
        passages = tuple(
            KnowledgePassage(
                passage_id=f"p{i}",
                page=i,
                hierarchy=f"{i} Synthetic exercise",
                locator=f"fixture:{source_id}#paragraph-{i}",
                text=text,
            )
            for i, text in enumerate(texts, 1)
        )
        sources.append(
            KnowledgeSource(
                source_id=source_id,
                source_version="authored-v1",
                document_code=code,
                title=title,
                source_uri=f"fixture:{source_id}",
                license_id="MIT",
                rights_statement="Original work, Copyright (c) 2026 Ugwiyeon Lee; MIT License.",
                asset_id=context.asset_id,
                domain_id="synthetic-pipeline-review",
                origin="synthetic",
                permitted_operations=METHODS,
                valid_from_utc=datetime(2026, 1, 1, tzinfo=UTC),
                valid_until_utc=datetime(2027, 1, 1, tzinfo=UTC),
                passages=passages,
                content_sha256=digest(passages),
            )
        )
    requests = {}
    for method in METHODS:
        requests[method] = KnowledgeRequest(
            method=method,
            asset_id=context.asset_id,
            location_id="review-desk",
            clock_id=context.clock_id,
            domain_id="synthetic-pipeline-review",
            as_of_utc=datetime(2026, 10, 27, tzinfo=UTC),
            question="Synthetic pressure threshold evidence",
            sources=tuple(sources),
            allowed_source_ids=tuple(s.source_id for s in sources),
            candidates=(
                KnowledgeCandidate(
                    candidate_id="invented-candidate",
                    source_id=sources[0].source_id,
                    passage_id="p1",
                    exact_quote=sources[0].passages[0].text,
                    tag_id="PT-DEMO",
                    variable="absolute_pressure",
                    threshold_value=200000.0,
                    unit="Pa",
                    compare_dir=">=",
                    rationale="Synthetic contract exercise only.",
                ),
            )
            if method == "candidate_review"
            else (),
            applicability_reviews=dict.fromkeys(REVIEWS, True),
            review_evidence_ids=dict.fromkeys(REVIEWS, "authored-fixture"),
            evidence_ids=("authored-fixture",),
            owner_source_sha256=source_sha256,
        )
    return requests, context


if __name__ == "__main__":
    requests, context = make_cases()
    result = evaluate_knowledge(requests["retrieve"], context)
    assert result["status"] == "needs_data" and result["native_receipt"] is None
    print(result["status"])
