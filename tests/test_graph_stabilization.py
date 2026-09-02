from dataclasses import asdict

from ripple.evaluation.graph_stabilization import (
    BATCHED_GRAPH_NAME,
    ROUTED_GRAPH_NAME,
    compare_ordered_results,
    evaluate_gates,
    stabilization_configs,
)
from ripple.evaluation.metrics import (
    AggregateMetrics,
    CategoryMetrics,
    QuestionResult,
)
from ripple.evaluation.runner import ConfigResult


def _aggregate(
    *,
    recall_at_5: float,
    recall_at_10: float,
    mrr: float,
    graph_ms: float,
) -> AggregateMetrics:
    return AggregateMetrics(
        question_count=40,
        recall_at_5=recall_at_5,
        recall_at_10=recall_at_10,
        mrr=mrr,
        precision_at_5=0.2,
        mean_latency_ms=1000.0,
        mean_latency_by_stage={
            "graph_ms": graph_ms,
            "total_ms": 1000.0,
        },
    )


def _result(
    *,
    retrieved: list[str] | None = None,
    recall_at_5: float = 0.86,
    recall_at_10: float = 1.0,
    mrr: float = 0.80,
    graph_ms: float = 150.0,
    relational_recall_at_10: float = 1.0,
) -> ConfigResult:
    _name, config = stabilization_configs()[1]
    question = QuestionResult(
        entry_id="q001",
        category="lookup",
        expected=["module.vpc"],
        retrieved=retrieved or ["module.vpc"],
        recall_at_5=1.0,
        recall_at_10=1.0,
        reciprocal_rank_value=1.0,
        precision_at_5=0.2,
        latency={"graph_ms": graph_ms, "total_ms": 1000.0},
    )
    relational_metrics = asdict(_aggregate(
        recall_at_5=0.8,
        recall_at_10=relational_recall_at_10,
        mrr=0.5,
        graph_ms=graph_ms,
    ))
    return ConfigResult(
        config_name=ROUTED_GRAPH_NAME,
        config=config,
        per_question=[question],
        aggregate=_aggregate(
            recall_at_5=recall_at_5,
            recall_at_10=recall_at_10,
            mrr=mrr,
            graph_ms=graph_ms,
        ),
        by_category=[
            CategoryMetrics(**relational_metrics, category="relational")
        ],
    )


def _baselines() -> tuple[dict, dict]:
    cross = {
        "aggregate": {
            "recall_at_5": 0.8541666666666666,
            "recall_at_10": 0.9,
            "mrr": 0.7456547619047619,
        }
    }
    graph = {
        "aggregate": {
            "recall_at_5": 0.8208333333333333,
            "recall_at_10": 1.0,
            "mrr": 0.7889880952380952,
        },
        "by_category": [
            {"category": "relational", "recall_at_10": 1.0}
        ],
    }
    return cross, graph


def test_stabilization_configs_only_toggle_intent_routing() -> None:
    configs = stabilization_configs()

    assert [name for name, _config in configs] == [
        BATCHED_GRAPH_NAME,
        ROUTED_GRAPH_NAME,
    ]
    batched = asdict(configs[0][1])
    routed = asdict(configs[1][1])
    assert batched.pop("graph_route_by_intent") is False
    assert routed.pop("graph_route_by_intent") is True
    assert batched == routed


def test_compare_ordered_results_reports_exact_equality() -> None:
    accepted = {
        "per_question": [
            {"entry_id": "q001", "retrieved": ["a", "b"]}
        ]
    }
    comparison = compare_ordered_results(
        accepted,
        _result(retrieved=["a", "b"]),
    )

    assert comparison["equal"] is True
    assert comparison["questions_checked"] == 1
    assert comparison["differences"] == []
    assert comparison["excluded_fields"] == ["latency"]


def test_compare_ordered_results_reports_order_changes() -> None:
    accepted = {
        "per_question": [
            {"entry_id": "q001", "retrieved": ["a", "b"]}
        ]
    }
    comparison = compare_ordered_results(
        accepted,
        _result(retrieved=["b", "a"]),
    )

    assert comparison["equal"] is False
    assert comparison["differences"] == [
        {
            "entry_id": "q001",
            "accepted": ["a", "b"],
            "batched": ["b", "a"],
        }
    ]


def test_gates_accept_strict_quality_and_fast_graph() -> None:
    cross, graph = _baselines()

    gates = evaluate_gates(cross, graph, _result())

    assert gates == {
        "strict_quality_path": True,
        "tradeoff_quality_path": True,
        "graph_latency_under_500ms": True,
        "relational_recall_at_10_preserved": True,
        "accepted": True,
    }


def test_gates_accept_predeclared_quality_tradeoff() -> None:
    cross, graph = _baselines()
    result = _result(
        recall_at_5=0.845,
        recall_at_10=1.0,
        mrr=graph["aggregate"]["mrr"],
    )

    gates = evaluate_gates(cross, graph, result)

    assert gates["strict_quality_path"] is False
    assert gates["tradeoff_quality_path"] is True
    assert gates["accepted"] is True


def test_gates_reject_slow_graph_or_relational_regression() -> None:
    cross, graph = _baselines()

    slow = evaluate_gates(cross, graph, _result(graph_ms=500.0))
    regression = evaluate_gates(
        cross,
        graph,
        _result(relational_recall_at_10=0.9),
    )

    assert slow["accepted"] is False
    assert slow["graph_latency_under_500ms"] is False
    assert regression["accepted"] is False
    assert regression["relational_recall_at_10_preserved"] is False
