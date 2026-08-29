from dataclasses import dataclass
from statistics import mean

from ripple.evaluation.dataset import BenchmarkEntry


@dataclass
class QuestionResult:
    entry_id: str
    category: str
    expected: list[str]
    retrieved: list[str]
    recall_at_5: float
    recall_at_10: float
    reciprocal_rank_value: float
    precision_at_5: float
    latency: dict[str, float]


@dataclass
class AggregateMetrics:
    question_count: int
    recall_at_5: float
    recall_at_10: float
    mrr: float
    precision_at_5: float
    mean_latency_ms: float
    mean_latency_by_stage: dict[str, float]


@dataclass
class CategoryMetrics(AggregateMetrics):
    category: str


def recall_at_k(
    expected: list[str],
    retrieved: list[str],
    k: int,
) -> float:
    if k <= 0:
        raise ValueError("k must be greater than zero")

    if not expected:
        raise ValueError("expected must not be empty")

    top = set(retrieved[:k])
    return len(top & set(expected)) / len(expected)


def precision_at_k(
    expected: list[str],
    retrieved: list[str],
    k: int,
) -> float:
    if k <= 0:
        raise ValueError("k must be greater than zero")

    expected_set = set(expected)
    top = retrieved[:k]
    correct_count = sum(
        1 for address in top if address in expected_set
    )
    return correct_count / k


def reciprocal_rank(
    expected: list[str],
    retrieved: list[str],
) -> float:
    expected_set = set(expected)

    for rank, address in enumerate(retrieved, start=1):
        if address in expected_set:
            return 1.0 / rank

    return 0.0


def score_question(
    entry: BenchmarkEntry,
    retrieved: list[str],
    latency: dict[str, float],
) -> QuestionResult:
    """Score one ranked retrieval result against its benchmark answer key."""
    return QuestionResult(
        entry_id=entry.id,
        category=entry.category,
        expected=entry.expected,
        retrieved=retrieved,
        recall_at_5=recall_at_k(entry.expected, retrieved, k=5),
        recall_at_10=recall_at_k(entry.expected, retrieved, k=10),
        reciprocal_rank_value=reciprocal_rank(entry.expected, retrieved),
        precision_at_5=precision_at_k(entry.expected, retrieved, k=5),
        latency=latency,
    )


def aggregate(results: list[QuestionResult]) -> AggregateMetrics:
    """Average question scores whose latency stages are consistent."""
    if not results:
        raise ValueError("results must not be empty")

    latency_keys = set(results[0].latency)
    for index, result in enumerate(results[1:], start=1):
        result_keys = set(result.latency)
        if result_keys != latency_keys:
            raise ValueError(
                "latency keys must match across results; "
                f"index {index} has {sorted(result_keys)}, "
                f"expected {sorted(latency_keys)}"
            )

    if "total_ms" not in latency_keys:
        raise ValueError("latency must include total_ms")

    mean_latency_by_stage = {
        key: mean(result.latency[key] for result in results)
        for key in sorted(latency_keys)
    }

    return AggregateMetrics(
        question_count=len(results),
        recall_at_5=mean(result.recall_at_5 for result in results),
        recall_at_10=mean(result.recall_at_10 for result in results),
        mrr=mean(result.reciprocal_rank_value for result in results),
        precision_at_5=mean(result.precision_at_5 for result in results),
        mean_latency_ms=mean_latency_by_stage["total_ms"],
        mean_latency_by_stage=mean_latency_by_stage,
    )


def aggregate_by_category(
    results: list[QuestionResult],
) -> list[CategoryMetrics]:
    """Aggregate question results independently for each sorted category."""
    grouped: dict[str, list[QuestionResult]] = {}
    for result in results:
        grouped.setdefault(result.category, []).append(result)

    category_metrics: list[CategoryMetrics] = []
    for category in sorted(grouped):
        metrics = aggregate(grouped[category])
        category_metrics.append(
            CategoryMetrics(
                question_count=metrics.question_count,
                recall_at_5=metrics.recall_at_5,
                recall_at_10=metrics.recall_at_10,
                mrr=metrics.mrr,
                precision_at_5=metrics.precision_at_5,
                mean_latency_ms=metrics.mean_latency_ms,
                mean_latency_by_stage=metrics.mean_latency_by_stage,
                category=category,
            )
        )

    return category_metrics
