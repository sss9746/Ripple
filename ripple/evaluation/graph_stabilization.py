import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from ripple.config import RetrievalConfig
from ripple.evaluation.dataset import BenchmarkEntry
from ripple.evaluation.metrics import (
    aggregate,
    aggregate_by_category,
    score_question,
)
from ripple.evaluation.runner import ABLATION_CONFIGS, ConfigResult
from ripple.llm.embeddings import EmbeddingProvider
from ripple.retrieval import pipeline
from ripple.retrieval.rerank import Reranker


CROSS_ENCODER_NAME = "+ Cross-encoder rerank"
DAY13_GRAPH_NAME = "+ Graph expansion"
BATCHED_GRAPH_NAME = "+ Batched graph expansion"
ROUTED_GRAPH_NAME = "+ Batched and intent-routed graph expansion"


@dataclass
class StabilizationRun:
    result: ConfigResult
    traces: list[dict]


def stabilization_configs() -> list[tuple[str, RetrievalConfig]]:
    """Return the two new Session C configurations."""
    day13_graph = dict(ABLATION_CONFIGS)[DAY13_GRAPH_NAME]
    return [
        (
            BATCHED_GRAPH_NAME,
            replace(day13_graph, graph_route_by_intent=False),
        ),
        (
            ROUTED_GRAPH_NAME,
            replace(day13_graph, graph_route_by_intent=True),
        ),
    ]


def load_accepted_report(path: Path) -> dict:
    report = json.loads(path.read_text())
    names = {
        result.get("config_name")
        for result in report.get("results", [])
    }
    required = {CROSS_ENCODER_NAME, DAY13_GRAPH_NAME}
    missing = required - names
    if missing:
        raise ValueError(
            "accepted report is missing configurations: "
            + ", ".join(sorted(missing))
        )
    return report


def baseline_result(report: dict, config_name: str) -> dict:
    return next(
        result
        for result in report["results"]
        if result["config_name"] == config_name
    )


def run_stabilization_config(
    repo_id: int,
    entries: list[BenchmarkEntry],
    config: RetrievalConfig,
    config_name: str,
    *,
    embedder: EmbeddingProvider,
    reranker: Reranker,
    reranker_json: dict,
) -> StabilizationRun:
    """Evaluate one graph policy and preserve its graph-stage provenance."""
    per_question = []
    traces: list[dict] = []

    for entry in entries:
        pipeline_result = pipeline.run_pipeline(
            repo_id,
            entry.question,
            config,
            embedder=embedder,
            reranker=reranker,
        )
        retrieved = [block.address for block in pipeline_result.blocks]
        per_question.append(
            score_question(
                entry,
                retrieved,
                pipeline_result.latency_json,
            )
        )
        traces.append(
            {
                "entry_id": entry.id,
                "graph_intent": pipeline_result.stages_json.get(
                    "graph_intent"
                ),
                "graph": pipeline_result.stages_json.get("graph", []),
                "final": pipeline_result.stages_json["final"],
            }
        )

    return StabilizationRun(
        result=ConfigResult(
            config_name=config_name,
            config=config,
            per_question=per_question,
            aggregate=aggregate(per_question),
            by_category=aggregate_by_category(per_question),
            reranker_json=reranker_json,
        ),
        traces=traces,
    )


def compare_ordered_results(
    accepted_graph: dict,
    batched_result: ConfigResult,
) -> dict:
    """Compare every ordered final address list, excluding latency fields."""
    accepted = {
        row["entry_id"]: row["retrieved"]
        for row in accepted_graph["per_question"]
    }
    current = {
        row.entry_id: row.retrieved
        for row in batched_result.per_question
    }
    all_ids = sorted(set(accepted) | set(current))
    differences = [
        {
            "entry_id": entry_id,
            "accepted": accepted.get(entry_id),
            "batched": current.get(entry_id),
        }
        for entry_id in all_ids
        if accepted.get(entry_id) != current.get(entry_id)
    ]
    return {
        "equal": not differences,
        "questions_checked": len(all_ids),
        "excluded_fields": ["latency"],
        "differences": differences,
        "provenance_basis": (
            "The accepted Day 13 report did not store graph provenance; "
            "legacy-versus-batched provenance equality is enforced by "
            "the Session B database and pipeline tests."
        ),
    }


def evaluate_gates(
    cross_encoder: dict,
    day13_graph: dict,
    routed: ConfigResult,
) -> dict:
    """Apply the quality and warm graph-latency gates declared before the run."""
    metrics = routed.aggregate
    cross = cross_encoder["aggregate"]
    graph = day13_graph["aggregate"]
    tolerance = 1e-12

    strict_quality = (
        metrics.recall_at_5 + tolerance >= cross["recall_at_5"]
        and metrics.recall_at_10 + tolerance >= cross["recall_at_10"]
        and metrics.mrr + tolerance >= cross["mrr"]
    )
    tradeoff_quality = (
        metrics.recall_at_5 + tolerance >= 0.845
        and metrics.recall_at_10 + tolerance >= graph["recall_at_10"]
        and metrics.mrr + tolerance >= graph["mrr"]
    )
    graph_ms = metrics.mean_latency_by_stage.get("graph_ms")
    latency_pass = graph_ms is not None and graph_ms < 500.0

    relational = next(
        category
        for category in routed.by_category
        if category.category == "relational"
    )
    day13_relational = next(
        category
        for category in day13_graph["by_category"]
        if category["category"] == "relational"
    )
    relational_pass = (
        relational.recall_at_10 + tolerance
        >= day13_relational["recall_at_10"]
    )

    return {
        "strict_quality_path": strict_quality,
        "tradeoff_quality_path": tradeoff_quality,
        "graph_latency_under_500ms": latency_pass,
        "relational_recall_at_10_preserved": relational_pass,
        "accepted": (
            (strict_quality or tradeoff_quality)
            and latency_pass
            and relational_pass
        ),
    }


def build_stabilization_report(
    accepted_report_path: Path,
    accepted_report: dict,
    batched: StabilizationRun,
    routed: StabilizationRun,
    equality: dict,
    gates: dict,
    cache_json: dict,
) -> dict:
    """Build the four-row Session C artifact without rewriting Day 13."""
    cross = baseline_result(accepted_report, CROSS_ENCODER_NAME)
    graph = baseline_result(accepted_report, DAY13_GRAPH_NAME)
    return {
        "schema_version": 1,
        "evaluation_type": "graph_stabilization_session_c",
        "accepted_day13_report": str(accepted_report_path),
        "repo_id": accepted_report["repo_id"],
        "benchmark_path": accepted_report["benchmark_path"],
        "benchmark_sha256": accepted_report["benchmark_sha256"],
        "corpus": accepted_report["corpus"],
        "embedding_model": accepted_report["embedding_model"],
        "question_count": accepted_report["question_count"],
        "results": [cross, graph, asdict(batched.result), asdict(routed.result)],
        "graph_traces": {
            BATCHED_GRAPH_NAME: batched.traces,
            ROUTED_GRAPH_NAME: routed.traces,
        },
        "day13_batched_equivalence": equality,
        "acceptance_gates": gates,
        "embedding_cache": cache_json,
    }
