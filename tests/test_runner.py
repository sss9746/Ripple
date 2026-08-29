import json
from pathlib import Path

import git
import pytest

from ripple.config import RetrievalConfig
from ripple.evaluation.dataset import BenchmarkEntry
from ripple.evaluation import runner
from ripple.evaluation.metrics import (
    QuestionResult,
    aggregate,
    aggregate_by_category,
)
from ripple.llm import generate
from ripple.retrieval.pipeline import PipelineResult
from ripple.retrieval.vector_store import RetrievedBlock


def retrieved_block(block_id: int, address: str) -> RetrievedBlock:
    return RetrievedBlock(
        id=block_id,
        address=address,
        file_path="main.tf",
        start_line=block_id,
        end_line=block_id + 2,
        body=f"body for {address}",
        score=1.0 / block_id,
    )


def pipeline_result(
    blocks: list[RetrievedBlock],
    latency: dict[str, float],
) -> PipelineResult:
    return PipelineResult(
        blocks=blocks,
        config_json={},
        stages_json={},
        latency_json=latency,
    )


def test_run_benchmark_scores_ranked_results_and_preserves_latency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entries = [
        BenchmarkEntry(
            id="q001",
            question="What creates the VPC?",
            expected=["module.vpc"],
            category="lookup",
        ),
        BenchmarkEntry(
            id="q002",
            question="Which block enables DNS support?",
            expected=["module.vpc"],
            category="attribute",
        ),
    ]
    latencies = {
        "What creates the VPC?": {
            "vector_query_ms": 10.0,
            "total_ms": 12.0,
        },
        "Which block enables DNS support?": {
            "vector_query_ms": 20.0,
            "total_ms": 24.0,
        },
    }
    canned = {
        "What creates the VPC?": [
            retrieved_block(1, "output.vpc_id"),
            retrieved_block(2, "module.vpc"),
        ],
        "Which block enables DNS support?": [
            retrieved_block(3, "module.vpc"),
            retrieved_block(4, "output.vpc_enable_dns_support"),
        ],
    }
    calls: list[tuple[int, str, RetrievalConfig]] = []

    def fake_run_pipeline(
        repo_id: int,
        question: str,
        config: RetrievalConfig,
    ) -> PipelineResult:
        calls.append((repo_id, question, config))
        return pipeline_result(canned[question], latencies[question])

    monkeypatch.setattr(runner.pipeline, "run_pipeline", fake_run_pipeline)
    config = runner.ABLATION_CONFIGS[0][1]

    result = runner.run_benchmark(
        repo_id=42,
        entries=entries,
        config=config,
        config_name="Vector only",
    )

    assert calls == [
        (42, "What creates the VPC?", config),
        (42, "Which block enables DNS support?", config),
    ]
    assert result.config_name == "Vector only"
    assert result.config is config
    assert result.per_question[0].retrieved == [
        "output.vpc_id",
        "module.vpc",
    ]
    assert result.per_question[0].reciprocal_rank_value == 0.5
    assert result.per_question[0].latency is latencies["What creates the VPC?"]
    assert result.per_question[1].retrieved == [
        "module.vpc",
        "output.vpc_enable_dns_support",
    ]
    assert result.per_question[1].reciprocal_rank_value == 1.0
    assert result.aggregate.question_count == 2
    assert [item.category for item in result.by_category] == [
        "attribute",
        "lookup",
    ]


def test_run_benchmark_preserves_all_ten_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocks = [
        retrieved_block(index, f"block.{index}")
        for index in range(1, 11)
    ]
    monkeypatch.setattr(
        runner.pipeline,
        "run_pipeline",
        lambda repo_id, question, config: pipeline_result(
            blocks,
            {"vector_query_ms": 10.0, "total_ms": 12.0},
        ),
    )
    entry = BenchmarkEntry(
        id="q001",
        question="Which block is last?",
        expected=["block.10"],
        category="lookup",
    )

    result = runner.run_benchmark(
        repo_id=42,
        entries=[entry],
        config=runner.ABLATION_CONFIGS[0][1],
        config_name="Vector only",
    )

    assert result.per_question[0].retrieved == [
        f"block.{index}" for index in range(1, 11)
    ]
    assert result.per_question[0].recall_at_5 == 0.0
    assert result.per_question[0].recall_at_10 == 1.0
    assert result.per_question[0].reciprocal_rank_value == 0.1


def test_ablation_configs_are_explicit_and_support_recall_at_10() -> None:
    assert [name for name, _config in runner.ABLATION_CONFIGS] == [
        "Vector only",
        "Vector + BM25",
        "Vector + BM25 + RRF",
    ]

    for _name, config in runner.ABLATION_CONFIGS:
        assert config.final_k >= 10
        assert config.use_rerank is False
        assert config.use_graph is False
        assert config.use_rewrite is False


def test_run_benchmark_never_generates_an_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_called(*args: object, **kwargs: object) -> None:
        raise AssertionError("answer generation must not run during evaluation")

    monkeypatch.setattr(generate, "answer_question", fail_if_called)
    monkeypatch.setattr(
        runner.pipeline,
        "run_pipeline",
        lambda repo_id, question, config: pipeline_result(
            [retrieved_block(1, "module.vpc")],
            {"vector_query_ms": 10.0, "total_ms": 12.0},
        ),
    )

    result = runner.run_benchmark(
        repo_id=42,
        entries=[
            BenchmarkEntry(
                id="q001",
                question="What creates the VPC?",
                expected=["module.vpc"],
                category="lookup",
            )
        ],
        config=runner.ABLATION_CONFIGS[0][1],
        config_name="Vector only",
    )

    assert result.aggregate.recall_at_5 == 1.0


def test_corpus_git_revision_finds_parent_repo_from_nested_path(
    tmp_path: Path,
) -> None:
    repository = git.Repo.init(tmp_path)
    with repository.config_writer() as config:
        config.set_value("user", "name", "Ripple Tests")
        config.set_value("user", "email", "ripple-tests@example.invalid")

    tracked_file = tmp_path / "tracked.txt"
    tracked_file.write_text("benchmark corpus")
    repository.index.add([tracked_file.name])
    commit = repository.index.commit("Initial corpus")
    nested_path = tmp_path / "examples" / "complete"
    nested_path.mkdir(parents=True)

    assert runner._corpus_git_revision(str(nested_path)) == commit.hexsha


def test_corpus_git_revision_returns_unavailable_for_non_git_path(
    tmp_path: Path,
) -> None:
    assert runner._corpus_git_revision(str(tmp_path)) == (
        runner.GIT_REVISION_UNAVAILABLE
    )
    assert runner._corpus_git_revision(str(tmp_path / "missing")) == (
        runner.GIT_REVISION_UNAVAILABLE
    )


def test_indexed_corpus_fingerprint_ignores_row_order_and_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_rows = [
        (10, "module.vpc", 'module "vpc" {}'),
        (20, "aws_security_group.rds", 'resource "aws_security_group" "rds" {}'),
    ]
    reordered_with_new_ids = [
        (999, "aws_security_group.rds", 'resource "aws_security_group" "rds" {}'),
        (888, "module.vpc", 'module "vpc" {}'),
    ]

    monkeypatch.setattr(
        runner.db,
        "fetch_resource_bodies",
        lambda repo_id: first_rows,
    )
    first_digest, first_count = runner._indexed_corpus_fingerprint(42)

    monkeypatch.setattr(
        runner.db,
        "fetch_resource_bodies",
        lambda repo_id: reordered_with_new_ids,
    )
    second_digest, second_count = runner._indexed_corpus_fingerprint(42)

    assert first_digest == second_digest
    assert len(first_digest) == 64
    assert first_count == second_count == 2


def test_indexed_corpus_fingerprint_changes_with_address_or_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fingerprint(rows: list[tuple[int, str, str]]) -> str:
        monkeypatch.setattr(
            runner.db,
            "fetch_resource_bodies",
            lambda repo_id: rows,
        )
        digest, _count = runner._indexed_corpus_fingerprint(42)
        return digest

    original = fingerprint([(1, "module.vpc", 'module "vpc" {}')])
    changed_address = fingerprint(
        [(1, "module.network", 'module "vpc" {}')]
    )
    changed_body = fingerprint(
        [(1, "module.vpc", 'module "vpc" { source = "../../" }')]
    )

    assert changed_address != original
    assert changed_body != original


def test_build_report_includes_provenance_results_and_no_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    question = QuestionResult(
        entry_id="q001",
        category="lookup",
        expected=["module.vpc"],
        retrieved=["module.vpc"],
        recall_at_5=1.0,
        recall_at_10=1.0,
        reciprocal_rank_value=1.0,
        precision_at_5=0.2,
        latency={"vector_query_ms": 10.0, "total_ms": 12.0},
    )
    config = runner.ABLATION_CONFIGS[0][1]
    config_result = runner.ConfigResult(
        config_name="Vector only",
        config=config,
        per_question=[question],
        aggregate=aggregate([question]),
        by_category=aggregate_by_category([question]),
    )
    monkeypatch.setattr(
        runner.db,
        "fetch_repo",
        lambda repo_id: (
            "vpc-complete",
            "https://example.com/vpc.git",
            "/tmp/vpc/examples/complete",
        ),
    )
    monkeypatch.setattr(
        runner.db,
        "fetch_resource_bodies",
        lambda repo_id: [(1, "module.vpc", 'module "vpc" {}')],
    )
    monkeypatch.setattr(
        runner,
        "_corpus_git_revision",
        lambda local_path: "abc123",
    )
    monkeypatch.setenv("OPENAI_API_KEY", "fake-openai-secret")
    monkeypatch.setenv("DATABASE_URL", "fake-database-secret")

    report = runner.build_report(
        repo_id=42,
        benchmark_path="data/benchmark.json",
        benchmark_sha256="benchmark-hash",
        results=[config_result],
    )

    assert report["schema_version"] == 1
    assert report["generated_at"].endswith("Z")
    assert report["repo_id"] == 42
    assert report["benchmark_path"] == "data/benchmark.json"
    assert report["benchmark_sha256"] == "benchmark-hash"
    assert report["embedding_model"] == runner.EMBEDDING_MODEL
    assert report["question_count"] == 1
    assert report["corpus"]["repo_name"] == "vpc-complete"
    assert report["corpus"]["git_revision"] == "abc123"
    assert report["corpus"]["resource_count"] == 1
    assert len(report["corpus"]["indexed_corpus_sha256"]) == 64
    assert report["results"][0]["config_name"] == "Vector only"
    assert report["results"][0]["config"]["final_k"] == 10
    assert report["results"][0]["per_question"][0]["latency"] == {
        "vector_query_ms": 10.0,
        "total_ms": 12.0,
    }

    serialized = json.dumps(report)
    assert "fake-openai-secret" not in serialized
    assert "fake-database-secret" not in serialized


def test_build_report_rejects_missing_repo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner.db, "fetch_repo", lambda repo_id: None)

    with pytest.raises(ValueError, match="repo_id=999 does not exist"):
        runner.build_report(
            repo_id=999,
            benchmark_path="data/benchmark.json",
            benchmark_sha256="benchmark-hash",
            results=[],
        )
