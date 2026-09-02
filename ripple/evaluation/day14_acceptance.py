import json
from dataclasses import asdict
from pathlib import Path

from ripple import db
from ripple.config import RetrievalConfig
from ripple.evaluation import runner
from ripple.evaluation.graph_stabilization import (
    DAY13_GRAPH_NAME,
    ROUTED_GRAPH_NAME,
    baseline_result,
)
from ripple.llm.embeddings import EMBEDDING_MODEL


SESSION_C_REPORT_PATH = Path(
    "data/eval_results/2026-09-01T20-48-26-006545Z.json"
)
DAY13_REPORT_PATH = Path(
    "data/eval_results/2026-08-31T22-17-19-477902Z.json"
)


_BASE_CONFIG = {
    "vector_backend": "pgvector",
    "use_vector": True,
    "use_bm25": True,
    "use_rrf": True,
    "use_rerank": True,
    "use_graph": True,
    "use_rewrite": False,
    "vector_k": 30,
    "bm25_k": 30,
    "rrf_k": 60,
    "rerank_top_n": 50,
    "final_k": 10,
    "graph_seed_n": 3,
    "graph_max_added": 10,
    "graph_route_by_intent": False,
}


_APPROVED_DAY14_ROWS = [
    (
        "Vector only",
        {
            **_BASE_CONFIG,
            "use_bm25": False,
            "use_rrf": False,
            "use_rerank": False,
            "use_graph": False,
        },
    ),
    (
        "Vector + BM25",
        {
            **_BASE_CONFIG,
            "use_rrf": False,
            "use_rerank": False,
            "use_graph": False,
        },
    ),
    (
        "Vector + BM25 + RRF",
        {
            **_BASE_CONFIG,
            "use_rerank": False,
            "use_graph": False,
        },
    ),
    (
        "+ Cross-encoder rerank",
        {
            **_BASE_CONFIG,
            "use_graph": False,
        },
    ),
    (
        "+ Graph expansion",
        {
            **_BASE_CONFIG,
            "graph_route_by_intent": True,
        },
    ),
]


def _load_json(path: Path) -> dict:
    """Load one evaluation artifact and require a JSON object."""
    report = json.loads(path.read_text())
    if not isinstance(report, dict):
        raise ValueError(f"evaluation report must be an object: {path}")
    return report


def load_session_c_reference(
    path: Path = SESSION_C_REPORT_PATH,
) -> dict:
    """Load the accepted graph-stabilization Session C report."""
    return _load_json(path)


def load_day13_accepted_graph_row(
    path: Path = DAY13_REPORT_PATH,
) -> dict:
    """Load the accepted Day 13 graph row used by the quality gates."""
    return baseline_result(_load_json(path), DAY13_GRAPH_NAME)


def extract_session_c_routed_row(session_c_report: dict) -> dict:
    """Return Session C's accepted intent-routed graph result."""
    return baseline_result(session_c_report, ROUTED_GRAPH_NAME)


def validate_repo_matches_session_c(
    repo_id: int,
    session_c_report: dict,
) -> None:
    """Reject a repository identity that differs from Session C."""
    expected_repo_id = session_c_report.get("repo_id")
    if repo_id != expected_repo_id:
        raise ValueError(
            f"repo_id mismatch: expected {expected_repo_id}, got {repo_id}"
        )

    current = db.fetch_repo(repo_id)
    if current is None:
        raise ValueError(f"repo_id={repo_id} does not exist")

    repo_name, source_url, local_path = current
    expected = session_c_report.get("corpus", {})
    actual_identity = {
        "repo_name": repo_name,
        "source_url": source_url,
        "local_path": local_path,
    }
    expected_identity = {
        key: expected.get(key)
        for key in actual_identity
    }
    if actual_identity != expected_identity:
        raise ValueError(
            "repository identity does not match Session C: "
            f"expected {expected_identity}, got {actual_identity}"
        )


def validate_benchmark_matches_session_c(
    benchmark_sha256: str,
    session_c_report: dict,
) -> None:
    """Reject benchmark bytes that differ from Session C."""
    expected = session_c_report.get("benchmark_sha256")
    if benchmark_sha256 != expected:
        raise ValueError(
            "benchmark fingerprint does not match Session C: "
            f"expected {expected}, got {benchmark_sha256}"
        )


def validate_corpus_matches_session_c(
    repo_id: int,
    session_c_report: dict,
) -> None:
    """Reject indexed resources or Git revision that differ from Session C."""
    expected = session_c_report.get("corpus", {})
    digest, resource_count = runner.indexed_corpus_fingerprint(repo_id)
    actual = {
        "indexed_corpus_sha256": digest,
        "resource_count": resource_count,
    }
    expected_index = {
        key: expected.get(key)
        for key in actual
    }
    if actual != expected_index:
        raise ValueError(
            "indexed corpus does not match Session C: "
            f"expected {expected_index}, got {actual}"
        )

    repo = db.fetch_repo(repo_id)
    if repo is None:
        raise ValueError(f"repo_id={repo_id} does not exist")
    current_revision = runner._corpus_git_revision(repo[2])
    expected_revision = expected.get("git_revision")
    if current_revision != expected_revision:
        raise ValueError(
            "corpus Git revision does not match Session C: "
            f"expected {expected_revision}, got {current_revision}"
        )


def validate_embedding_model_matches_session_c(
    session_c_report: dict,
) -> None:
    """Reject an embedding-model change that invalidates comparison."""
    expected = session_c_report.get("embedding_model")
    if EMBEDDING_MODEL != expected:
        raise ValueError(
            "embedding model does not match Session C: "
            f"expected {expected}, got {EMBEDDING_MODEL}"
        )


def validate_approved_five_row_configuration(
    configs: list[tuple[str, RetrievalConfig]],
) -> None:
    """Reject any rename, reordering, addition, removal, or field drift."""
    actual = [
        (name, asdict(config))
        for name, config in configs
    ]
    if actual != _APPROVED_DAY14_ROWS:
        raise ValueError(
            "evaluation configurations do not match the approved "
            "Day 14 five-row snapshot"
        )


def relabel_ordering_comparison(raw: dict) -> dict:
    """Give Session C and Day 14 explicit labels in an ordering diff."""
    return {
        **raw,
        "differences": [
            {
                "entry_id": difference["entry_id"],
                "session_c_routed": difference["accepted"],
                "day14_row5": difference["batched"],
            }
            for difference in raw["differences"]
        ],
    }


def validate_embedding_accounting(
    embedding_cache: dict,
    *,
    unique_questions: int,
    entry_count: int,
    vector_config_count: int,
) -> dict:
    """Check exact paid-call and timed cache-hit counts."""
    expected_provider_calls = unique_questions
    expected_cache_hits = entry_count * vector_config_count
    valid = (
        embedding_cache["provider_calls"] == expected_provider_calls
        and embedding_cache["cache_hits"] == expected_cache_hits
        and embedding_cache["unique_questions"] == unique_questions
    )
    return {
        "expected_provider_calls": expected_provider_calls,
        "actual_provider_calls": embedding_cache["provider_calls"],
        "expected_cache_hits": expected_cache_hits,
        "actual_cache_hits": embedding_cache["cache_hits"],
        "expected_unique_questions": unique_questions,
        "actual_unique_questions": embedding_cache["unique_questions"],
        "valid": valid,
    }
