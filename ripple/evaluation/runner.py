import hashlib
import json
import time
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
from ripple.llm.embeddings import (
    EMBEDDING_MODEL,
    CachingEmbeddingProvider,
    EmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from ripple.retrieval import pipeline
from ripple.retrieval.rerank import (
    CrossEncoderReranker,
    PreparedReranker,
)


GIT_REVISION_UNAVAILABLE = "unavailable"


@dataclass
class ConfigResult:
    config_name: str
    config: RetrievalConfig
    per_question: list[QuestionResult]
    aggregate: AggregateMetrics
    by_category: list[CategoryMetrics]
    reranker_json: dict | None = None


@dataclass
class EvaluationRun:
    results: list[ConfigResult]
    embedding_cache: dict
    embedding_precomputation: dict
    latency_methodology: dict


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
    (
        "+ Cross-encoder rerank",
        RetrievalConfig(
            use_vector=True,
            use_bm25=True,
            use_rrf=True,
            use_rerank=True,
            use_graph=False,
            use_rewrite=False,
            final_k=10,
        ),
    ),
    (
        "+ Graph expansion",
        RetrievalConfig(
            use_vector=True,
            use_bm25=True,
            use_rrf=True,
            use_rerank=True,
            use_graph=True,
            graph_route_by_intent=True,
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


def indexed_corpus_fingerprint(repo_id: int) -> tuple[str, int]:
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
    *,
    embedding_cache: dict | None = None,
    embedding_precomputation: dict | None = None,
    latency_methodology: dict | None = None,
) -> dict:
    """Assemble one JSON-ready evaluation report with corpus provenance."""
    repo = db.fetch_repo(repo_id)
    if repo is None:
        raise ValueError(f"repo_id={repo_id} does not exist")

    repo_name, source_url, local_path = repo
    indexed_corpus_sha256, resource_count = (
        indexed_corpus_fingerprint(repo_id)
    )
    question_count = (
        len(results[0].per_question)
        if results
        else 0
    )

    report = {
        "schema_version": 3,
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

    if embedding_cache is not None:
        report["embedding_cache"] = embedding_cache
    if embedding_precomputation is not None:
        report["embedding_precomputation"] = embedding_precomputation
    if latency_methodology is not None:
        report["latency_methodology"] = latency_methodology

    return report


def run_benchmark(
    repo_id: int,
    entries: list[BenchmarkEntry],
    config: RetrievalConfig,
    config_name: str,
    *,
    embedder: EmbeddingProvider | None = None,
    reranker: PreparedReranker | None = None,
) -> ConfigResult:
    """Run and score every benchmark question under one retrieval config."""
    active_reranker: PreparedReranker | None = None
    reranker_json: dict | None = None

    if config.use_rerank:
        active_reranker = (
            reranker
            if reranker is not None
            else CrossEncoderReranker()
        )
        if active_reranker.prepare_ms is None:
            active_reranker.prepare()
            verb = "prepared"
        else:
            verb = "reused"
        reranker_json = active_reranker.describe()
        print(
            f"[{config_name}] reranker {verb} "
            f"({active_reranker.prepare_ms:.0f}ms, one-time; "
            "excluded from question latency)"
        )

    per_question: list[QuestionResult] = []

    for entry in entries:
        pipeline_kwargs = {}
        if embedder is not None:
            pipeline_kwargs["embedder"] = embedder
        if active_reranker is not None:
            pipeline_kwargs["reranker"] = active_reranker

        pipeline_result = pipeline.run_pipeline(
            repo_id,
            entry.question,
            config,
            **pipeline_kwargs,
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
        reranker_json=reranker_json,
    )


def execute_evaluation_run(
    repo_id: int,
    entries: list[BenchmarkEntry],
    configs: list[tuple[str, RetrievalConfig]],
) -> EvaluationRun:
    """Run approved configs with shared, pre-warmed model providers."""
    uses_vector = any(
        config.use_vector and config.vector_k > 0
        for _name, config in configs
    )
    uses_rerank = any(
        config.use_rerank
        for _name, config in configs
    )
    unique_questions = sorted({entry.question for entry in entries})

    shared_embedder: CachingEmbeddingProvider | None = None
    prewarm_total_ms = 0.0
    if uses_vector:
        shared_embedder = CachingEmbeddingProvider(
            OpenAIEmbeddingProvider()
        )
        prewarm_start = time.perf_counter()
        for question in unique_questions:
            shared_embedder.embed([question])
        prewarm_total_ms = (
            time.perf_counter() - prewarm_start
        ) * 1000
        mean_ms = prewarm_total_ms / max(len(unique_questions), 1)
        print(
            "Pre-warmed embedding cache: "
            f"{len(unique_questions)} unique questions in "
            f"{prewarm_total_ms:.0f}ms ({mean_ms:.1f}ms/question)"
        )

    provider_calls_before_run = (
        shared_embedder.request_count
        if shared_embedder is not None
        else 0
    )
    shared_reranker = (
        CrossEncoderReranker()
        if uses_rerank
        else None
    )

    results = [
        run_benchmark(
            repo_id=repo_id,
            entries=entries,
            config=config,
            config_name=config_name,
            embedder=shared_embedder,
            reranker=shared_reranker,
        )
        for config_name, config in configs
    ]

    provider_calls_after_run = (
        shared_embedder.request_count
        if shared_embedder is not None
        else 0
    )
    cache_hits = (
        shared_embedder.cache_hit_count
        if shared_embedder is not None
        else 0
    )

    return EvaluationRun(
        results=results,
        embedding_cache={
            "provider_calls": provider_calls_after_run,
            "cache_hits": cache_hits,
            "unique_questions": len(unique_questions),
        },
        embedding_precomputation={
            "unique_questions": len(unique_questions),
            "provider_calls": provider_calls_before_run,
            "total_ms": prewarm_total_ms,
            "mean_ms_per_question": (
                prewarm_total_ms / len(unique_questions)
                if unique_questions
                else 0.0
            ),
        },
        latency_methodology={
            "description": (
                "The embedding cache was fully pre-warmed before any "
                "configuration ran. Every row's vector_query_ms/total_ms "
                "reflects only an in-memory cache lookup and the vector "
                "database query itself, never live OpenAI embedding "
                "network latency. Embedding network latency is measured "
                "separately as embedding_precomputation."
            ),
            "provider_calls_before_run": provider_calls_before_run,
            "provider_calls_during_run": (
                provider_calls_after_run - provider_calls_before_run
            ),
            "valid": (
                provider_calls_after_run == provider_calls_before_run
            ),
        },
    )
