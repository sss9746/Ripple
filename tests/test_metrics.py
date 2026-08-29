import pytest

from ripple.evaluation.dataset import BenchmarkEntry
from ripple.evaluation.metrics import (
    QuestionResult,
    aggregate,
    aggregate_by_category,
    precision_at_k,
    recall_at_k,
    reciprocal_rank,
    score_question,
)


def question_result(
    entry_id: str,
    category: str,
    recall_at_5: float,
    recall_at_10: float,
    reciprocal_rank_value: float,
    precision_at_5: float,
    latency: dict[str, float],
) -> QuestionResult:
    return QuestionResult(
        entry_id=entry_id,
        category=category,
        expected=["expected.block"],
        retrieved=["retrieved.block"],
        recall_at_5=recall_at_5,
        recall_at_10=recall_at_10,
        reciprocal_rank_value=reciprocal_rank_value,
        precision_at_5=precision_at_5,
        latency=latency,
    )


def test_recall_at_k_counts_expected_addresses_in_top_k() -> None:
    expected = ["aws_vpc.main", "aws_subnet.public"]
    retrieved = ["aws_instance.other", "aws_vpc.main", "aws_subnet.public"]

    assert recall_at_k(expected, retrieved, k=2) == 0.5
    assert recall_at_k(expected, retrieved, k=3) == 1.0


def test_recall_at_k_handles_fewer_than_k_results() -> None:
    assert recall_at_k(
        ["aws_vpc.main", "aws_subnet.public"],
        ["aws_vpc.main"],
        k=5,
    ) == 0.5


def test_recall_at_k_returns_zero_when_nothing_matches() -> None:
    assert recall_at_k(
        ["aws_vpc.main"],
        ["aws_instance.other"],
        k=1,
    ) == 0.0


@pytest.mark.parametrize("k", [0, -1])
def test_recall_at_k_rejects_non_positive_k(k: int) -> None:
    with pytest.raises(ValueError, match="k must be greater than zero"):
        recall_at_k(["aws_vpc.main"], ["aws_vpc.main"], k)


def test_recall_at_k_rejects_empty_expected() -> None:
    with pytest.raises(ValueError, match="expected must not be empty"):
        recall_at_k([], ["aws_vpc.main"], k=5)


def test_precision_at_k_divides_matches_by_k() -> None:
    expected = ["aws_vpc.main", "aws_subnet.public"]
    retrieved = ["aws_instance.other", "aws_vpc.main", "aws_subnet.public"]

    assert precision_at_k(expected, retrieved, k=3) == pytest.approx(2 / 3)


def test_precision_at_k_penalizes_unfilled_positions() -> None:
    assert precision_at_k(
        ["aws_vpc.main"],
        ["aws_vpc.main"],
        k=5,
    ) == 0.2


def test_precision_at_k_returns_zero_when_nothing_matches() -> None:
    assert precision_at_k(
        ["aws_vpc.main"],
        ["aws_instance.other"],
        k=1,
    ) == 0.0


@pytest.mark.parametrize("k", [0, -1])
def test_precision_at_k_rejects_non_positive_k(k: int) -> None:
    with pytest.raises(ValueError, match="k must be greater than zero"):
        precision_at_k(["aws_vpc.main"], ["aws_vpc.main"], k)


@pytest.mark.parametrize(
    ("retrieved", "expected_rank"),
    [
        (["aws_vpc.main", "aws_instance.other"], 1.0),
        (["aws_instance.one", "aws_instance.two", "aws_vpc.main"], 1 / 3),
        (["aws_instance.one", "aws_instance.two"], 0.0),
    ],
)
def test_reciprocal_rank_uses_first_expected_result(
    retrieved: list[str],
    expected_rank: float,
) -> None:
    assert reciprocal_rank(
        ["aws_vpc.main"],
        retrieved,
    ) == pytest.approx(expected_rank)


def test_score_question_preserves_rank_order_and_latency() -> None:
    entry = BenchmarkEntry(
        id="q001",
        question="What creates the VPC?",
        expected=["module.vpc"],
        category="lookup",
    )
    retrieved = ["output.vpc_id", "module.vpc", "aws_security_group.rds"]
    latency = {"vector_query_ms": 10.0, "total_ms": 12.0}

    result = score_question(entry, retrieved, latency)

    assert result.entry_id == "q001"
    assert result.category == "lookup"
    assert result.expected == ["module.vpc"]
    assert result.retrieved == retrieved
    assert result.recall_at_5 == 1.0
    assert result.recall_at_10 == 1.0
    assert result.reciprocal_rank_value == 0.5
    assert result.precision_at_5 == 0.2
    assert result.latency == latency


def test_aggregate_computes_hand_calculated_means() -> None:
    results = [
        question_result(
            "q001",
            "lookup",
            recall_at_5=1.0,
            recall_at_10=1.0,
            reciprocal_rank_value=1.0,
            precision_at_5=0.2,
            latency={"vector_query_ms": 10.0, "total_ms": 12.0},
        ),
        question_result(
            "q002",
            "lookup",
            recall_at_5=0.0,
            recall_at_10=0.5,
            reciprocal_rank_value=0.5,
            precision_at_5=0.0,
            latency={"vector_query_ms": 20.0, "total_ms": 24.0},
        ),
    ]

    metrics = aggregate(results)

    assert metrics.question_count == 2
    assert metrics.recall_at_5 == 0.5
    assert metrics.recall_at_10 == 0.75
    assert metrics.mrr == 0.75
    assert metrics.precision_at_5 == 0.1
    assert metrics.mean_latency_ms == 18.0
    assert metrics.mean_latency_by_stage == {
        "total_ms": 18.0,
        "vector_query_ms": 15.0,
    }


def test_aggregate_rejects_empty_results() -> None:
    with pytest.raises(ValueError, match="results must not be empty"):
        aggregate([])


def test_aggregate_rejects_inconsistent_latency_keys() -> None:
    vector_only = question_result(
        "q001",
        "lookup",
        1.0,
        1.0,
        1.0,
        0.2,
        {"vector_query_ms": 10.0, "total_ms": 12.0},
    )
    hybrid = question_result(
        "q002",
        "lookup",
        1.0,
        1.0,
        1.0,
        0.2,
        {"vector_query_ms": 10.0, "bm25_ms": 2.0, "total_ms": 14.0},
    )

    with pytest.raises(ValueError, match="latency keys must match"):
        aggregate([vector_only, hybrid])


def test_aggregate_requires_total_latency() -> None:
    result = question_result(
        "q001",
        "lookup",
        1.0,
        1.0,
        1.0,
        0.2,
        {"vector_query_ms": 10.0},
    )

    with pytest.raises(ValueError, match="latency must include total_ms"):
        aggregate([result])


def test_different_configs_aggregate_latency_independently() -> None:
    vector_metrics = aggregate(
        [
            question_result(
                "q001",
                "lookup",
                1.0,
                1.0,
                1.0,
                0.2,
                {"vector_query_ms": 10.0, "total_ms": 12.0},
            )
        ]
    )
    hybrid_metrics = aggregate(
        [
            question_result(
                "q001",
                "lookup",
                1.0,
                1.0,
                1.0,
                0.2,
                {
                    "vector_query_ms": 10.0,
                    "bm25_ms": 2.0,
                    "fusion_ms": 1.0,
                    "total_ms": 14.0,
                },
            )
        ]
    )

    assert set(vector_metrics.mean_latency_by_stage) == {
        "vector_query_ms",
        "total_ms",
    }
    assert "bm25_ms" not in vector_metrics.mean_latency_by_stage
    assert "fusion_ms" not in vector_metrics.mean_latency_by_stage
    assert set(hybrid_metrics.mean_latency_by_stage) == {
        "vector_query_ms",
        "bm25_ms",
        "fusion_ms",
        "total_ms",
    }


def test_aggregate_by_category_is_sorted_and_independent() -> None:
    results = [
        question_result(
            "q001",
            "lookup",
            1.0,
            1.0,
            1.0,
            0.2,
            {"vector_query_ms": 10.0, "total_ms": 12.0},
        ),
        question_result(
            "q002",
            "attribute",
            0.0,
            0.5,
            0.5,
            0.0,
            {"vector_query_ms": 20.0, "total_ms": 24.0},
        ),
        question_result(
            "q003",
            "lookup",
            0.0,
            0.0,
            0.0,
            0.0,
            {"vector_query_ms": 30.0, "total_ms": 36.0},
        ),
    ]

    metrics = aggregate_by_category(results)

    assert [item.category for item in metrics] == ["attribute", "lookup"]
    assert metrics[0].question_count == 1
    assert metrics[0].recall_at_5 == 0.0
    assert metrics[0].mean_latency_ms == 24.0
    assert metrics[1].question_count == 2
    assert metrics[1].recall_at_5 == 0.5
    assert metrics[1].mean_latency_ms == 24.0


def test_aggregate_by_category_rejects_inconsistent_latency_keys() -> None:
    results = [
        question_result(
            "q001",
            "lookup",
            1.0,
            1.0,
            1.0,
            0.2,
            {"vector_query_ms": 10.0, "total_ms": 12.0},
        ),
        question_result(
            "q002",
            "lookup",
            1.0,
            1.0,
            1.0,
            0.2,
            {"vector_query_ms": 10.0, "bm25_ms": 2.0, "total_ms": 14.0},
        ),
    ]

    with pytest.raises(ValueError, match="latency keys must match"):
        aggregate_by_category(results)
