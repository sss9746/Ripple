from pathlib import Path

import pytest

from ripple.evaluation.dataset import load_benchmark
from ripple.retrieval.intent import (
    QueryIntent,
    classify_intent,
    directions_for_intent,
)


BENCHMARK_PATH = Path(__file__).parent.parent / "data" / "benchmark.json"
GOLD_INTENT_BY_CATEGORY = {
    "lookup": QueryIntent.LOOKUP,
    "attribute": QueryIntent.ATTRIBUTE,
    "relational": QueryIntent.DEPENDENCY,
    "blast_radius": QueryIntent.BLAST_RADIUS,
}


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("Which module creates the network?", QueryIntent.LOOKUP),
        (
            "Which blocks contain an exact reference to module.vpc?",
            QueryIntent.ATTRIBUTE,
        ),
        ("What does this subnet rely on?", QueryIntent.DEPENDENCY),
        (
            "What breaks if this security group is deleted?",
            QueryIntent.BLAST_RADIUS,
        ),
        (
            "How does this subnet relate to the VPC?",
            QueryIntent.AMBIGUOUS_RELATIONSHIP,
        ),
        ("", QueryIntent.LOOKUP),
        ("   ", QueryIntent.LOOKUP),
    ],
)
def test_classify_intent(
    question: str,
    expected: QueryIntent,
) -> None:
    assert classify_intent(question) is expected


def test_blast_radius_takes_priority_over_dependency_language() -> None:
    question = (
        "What is affected if the resource this module depends on is removed?"
    )

    assert classify_intent(question) is QueryIntent.BLAST_RADIUS


@pytest.mark.parametrize(
    ("intent", "expected"),
    [
        (QueryIntent.LOOKUP, ()),
        (QueryIntent.ATTRIBUTE, ()),
        (QueryIntent.DEPENDENCY, ("dependency",)),
        (QueryIntent.BLAST_RADIUS, ("dependent",)),
        (
            QueryIntent.AMBIGUOUS_RELATIONSHIP,
            ("dependent", "dependency"),
        ),
    ],
)
def test_directions_for_intent(
    intent: QueryIntent,
    expected: tuple[str, ...],
) -> None:
    assert directions_for_intent(intent) == expected


def test_router_matches_all_benchmark_gold_labels() -> None:
    entries = load_benchmark(BENCHMARK_PATH)
    mismatches = [
        (
            entry.id,
            entry.question,
            GOLD_INTENT_BY_CATEGORY[entry.category].value,
            classify_intent(entry.question).value,
        )
        for entry in entries
        if classify_intent(entry.question)
        is not GOLD_INTENT_BY_CATEGORY[entry.category]
    ]

    assert mismatches == []
