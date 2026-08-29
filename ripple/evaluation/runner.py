import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from ripple import db
from ripple.config import RetrievalConfig
from ripple.evaluation.dataset import BenchmarkEntry
from ripple.evaluation.metrics import (
    AggregateMetrics,
    CategoryMetrics,
    QuestionResult,
    aggregate,
    aggregate_by_category,
    score_question,
)
from ripple.llm.embeddings import EMBEDDING_MODEL
from ripple.retrieval import pipeline


GIT_REVISION_UNAVAILABLE = "unavailable"


@dataclass
class ConfigResult:
    config_name: str
    config: RetrievalConfig
    per_question: list[QuestionResult]
    aggregate: AggregateMetrics
    by_category: list[CategoryMetrics]


ABLATION_CONFIGS: list[tuple[str, RetrievalConfig]] = [
    (
        "Vector only",
        RetrievalConfig(
            use_vector=True,
            use_bm25=False,
            use_rrf=False,
            use_rerank=False,
            use_graph=False,
            use_rewrite=False,
            final_k=10,
        ),
    ),
    (
        "Vector + BM25",
        RetrievalConfig(
            use_vector=True,
            use_bm25=True,
            use_rrf=False,
            use_rerank=False,
            use_graph=False,
            use_rewrite=False,
            final_k=10,
        ),
    ),
    (
        "Vector + BM25 + RRF",
        RetrievalConfig(
            use_vector=True,
            use_bm25=True,
            use_rrf=True,
            use_rerank=False,
            use_graph=False,
            use_rewrite=False,
            final_k=10,
        ),
    ),
]


def _corpus_git_revision(local_path: str) -> str:
    """Return the enclosing repository's commit or an honest fallback."""
    try:
        import git

        repository = git.Repo(
            local_path,
            search_parent_directories=True,
        )
        return repository.head.commit.hexsha
    except Exception:
        return GIT_REVISION_UNAVAILABLE


def _indexed_corpus_fingerprint(repo_id: int) -> tuple[str, int]:
    """Hash the exact address/body pairs currently indexed for a repository."""
    rows = db.fetch_resource_bodies(repo_id)
    pairs = sorted(
        (address, body)
        for _resource_id, address, body in rows
    )
    canonical = json.dumps(pairs, separators=(",", ":"))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return digest, len(pairs)


def build_report(
    repo_id: int,
    benchmark_path: str,
    benchmark_sha256: str,
    results: list[ConfigResult],
) -> dict:
    """Assemble one JSON-ready evaluation report with corpus provenance."""
    repo = db.fetch_repo(repo_id)
    if repo is None:
        raise ValueError(f"repo_id={repo_id} does not exist")

    repo_name, source_url, local_path = repo
    indexed_corpus_sha256, resource_count = (
        _indexed_corpus_fingerprint(repo_id)
    )
    question_count = (
        len(results[0].per_question)
        if results
        else 0
    )

    return {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
        "repo_id": repo_id,
        "benchmark_path": benchmark_path,
        "benchmark_sha256": benchmark_sha256,
        "corpus": {
            "repo_name": repo_name,
            "source_url": source_url,
            "local_path": local_path,
            "git_revision": _corpus_git_revision(local_path),
            "indexed_corpus_sha256": indexed_corpus_sha256,
            "resource_count": resource_count,
        },
        "embedding_model": EMBEDDING_MODEL,
        "question_count": question_count,
        "results": [asdict(result) for result in results],
    }


def run_benchmark(
    repo_id: int,
    entries: list[BenchmarkEntry],
    config: RetrievalConfig,
    config_name: str,
) -> ConfigResult:
    """Run and score every benchmark question under one retrieval config."""
    per_question: list[QuestionResult] = []

    for entry in entries:
        pipeline_result = pipeline.run_pipeline(
            repo_id,
            entry.question,
            config,
        )
        retrieved = [block.address for block in pipeline_result.blocks]
        per_question.append(
            score_question(
                entry,
                retrieved,
                pipeline_result.latency_json,
            )
        )

    return ConfigResult(
        config_name=config_name,
        config=config,
        per_question=per_question,
        aggregate=aggregate(per_question),
        by_category=aggregate_by_category(per_question),
    )
