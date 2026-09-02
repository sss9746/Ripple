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


def retrieved_block(
    block_id: int,
    address: str,
    embed_text: str | None = None,
) -> RetrievedBlock:
    if embed_text is None:
        embed_text = f"embed text for {address}"

    return RetrievedBlock(
        id=block_id,
        address=address,
        file_path="main.tf",
        start_line=block_id,
        end_line=block_id + 2,
        body=f"body for {address}",
        embed_text=embed_text,
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
        "+ Cross-encoder rerank",
        "+ Graph expansion",
    ]

    for index, (_name, config) in enumerate(runner.ABLATION_CONFIGS):
        assert config.final_k >= 10
        assert config.use_rerank is (index in (3, 4))
        assert config.graph_route_by_intent is (index == 4)
        assert config.use_graph is (index == 4)
        assert config.use_rewrite is False

    rerank_config = runner.ABLATION_CONFIGS[3][1]
    assert rerank_config.use_vector is True
    assert rerank_config.use_bm25 is True
    assert rerank_config.use_rrf is True

    graph_config = runner.ABLATION_CONFIGS[4][1]
    assert graph_config.use_vector is True
    assert graph_config.use_bm25 is True
    assert graph_config.use_rrf is True


def test_run_benchmark_reuses_one_prepared_reranker(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    instances: list[object] = []

    class FakeReranker:
        def __init__(self) -> None:
            self.prepare_calls = 0
            self.prepare_ms: float | None = None
            instances.append(self)

        def prepare(self) -> None:
            self.prepare_calls += 1
            self.prepare_ms = 12.5

        def describe(self) -> dict:
            return {
                "model_name": "fake-reranker",
                "prepare_ms": self.prepare_ms,
            }

    received_rerankers: list[object] = []

    def fake_run_pipeline(
        repo_id: int,
        question: str,
        config: RetrievalConfig,
        *,
        reranker: object,
    ) -> PipelineResult:
        received_rerankers.append(reranker)
        return pipeline_result(
            [retrieved_block(1, "module.vpc")],
            {"rerank_ms": 2.0, "total_ms": 3.0},
        )

    monkeypatch.setattr(runner, "CrossEncoderReranker", FakeReranker)
    monkeypatch.setattr(
        runner.pipeline,
        "run_pipeline",
        fake_run_pipeline,
    )
    entries = [
        BenchmarkEntry(
            id="q001",
            question="What creates the VPC?",
            expected=["module.vpc"],
            category="lookup",
        ),
        BenchmarkEntry(
            id="q002",
            question="Which module manages networking?",
            expected=["module.vpc"],
            category="lookup",
        ),
    ]

    result = runner.run_benchmark(
        repo_id=42,
        entries=entries,
        config=runner.ABLATION_CONFIGS[3][1],
        config_name="+ Cross-encoder rerank",
    )

    assert len(instances) == 1
    assert instances[0].prepare_calls == 1
    assert received_rerankers == [instances[0], instances[0]]
    assert result.reranker_json == {
        "model_name": "fake-reranker",
        "prepare_ms": 12.5,
    }
    assert capsys.readouterr().out.count("reranker prepared") == 1


def test_execute_evaluation_run_constructs_and_prepares_one_shared_reranker(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    instances: list[object] = []

    class FakeReranker:
        def __init__(self) -> None:
            self.prepare_calls = 0
            self.prepare_ms: float | None = None
            instances.append(self)

        def prepare(self) -> None:
            self.prepare_calls += 1
            self.prepare_ms = 8.0

        def describe(self) -> dict:
            return {
                "model_name": "fake-reranker",
                "prepare_ms": self.prepare_ms,
            }

    received_rerankers: list[object] = []

    def fake_run_pipeline(
        repo_id: int,
        question: str,
        config: RetrievalConfig,
        *,
        reranker: object,
    ) -> PipelineResult:
        received_rerankers.append(reranker)
        return pipeline_result(
            [retrieved_block(1, "module.vpc")],
            {"rerank_ms": 2.0, "total_ms": 3.0},
        )

    monkeypatch.setattr(runner, "CrossEncoderReranker", FakeReranker)
    monkeypatch.setattr(runner.pipeline, "run_pipeline", fake_run_pipeline)
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: (_ for _ in ()).throw(
            AssertionError("library evaluation must never prompt")
        ),
    )
    entry = BenchmarkEntry(
        id="q001",
        question="What creates the VPC?",
        expected=["module.vpc"],
        category="lookup",
    )
    rerank_only = RetrievalConfig(
        use_vector=False,
        use_bm25=False,
        use_rrf=False,
        use_rerank=True,
        use_graph=False,
    )
    rerank_with_graph = RetrievalConfig(
        use_vector=False,
        use_bm25=False,
        use_rrf=False,
        use_rerank=True,
        use_graph=True,
    )

    evaluation = runner.execute_evaluation_run(
        repo_id=42,
        entries=[entry],
        configs=[
            ("Rerank", rerank_only),
            ("Rerank + graph", rerank_with_graph),
        ],
    )

    assert len(instances) == 1
    assert instances[0].prepare_calls == 1
    assert received_rerankers == [instances[0], instances[0]]
    assert len(evaluation.results) == 2
    output = capsys.readouterr().out
    assert output.count("reranker prepared") == 1
    assert output.count("reranker reused") == 1


def test_execute_evaluation_run_prewarms_before_timed_vector_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []

    class FakeEmbeddingProvider:
        def embed(self, texts: list[str]) -> list[list[float]]:
            events.append(f"provider:{texts[0]}")
            return [[0.0] * 1536 for _text in texts]

    def fake_run_pipeline(
        repo_id: int,
        question: str,
        config: RetrievalConfig,
        *,
        embedder: object,
    ) -> PipelineResult:
        events.append(f"pipeline:{question}")
        embedder.embed([question])
        return pipeline_result(
            [retrieved_block(1, "module.vpc")],
            {"vector_query_ms": 1.0, "total_ms": 2.0},
        )

    provider = FakeEmbeddingProvider()
    monkeypatch.setattr(
        runner,
        "OpenAIEmbeddingProvider",
        lambda: provider,
    )
    monkeypatch.setattr(runner.pipeline, "run_pipeline", fake_run_pipeline)
    entries = [
        BenchmarkEntry(
            id="q001",
            question="First question?",
            expected=["module.vpc"],
            category="lookup",
        ),
        BenchmarkEntry(
            id="q002",
            question="Second question?",
            expected=["module.vpc"],
            category="lookup",
        ),
    ]
    configs = [
        ("Vector one", runner.ABLATION_CONFIGS[0][1]),
        ("Vector two", runner.ABLATION_CONFIGS[0][1]),
    ]

    evaluation = runner.execute_evaluation_run(
        repo_id=42,
        entries=entries,
        configs=configs,
    )

    assert events[:2] == [
        "provider:First question?",
        "provider:Second question?",
    ]
    assert all(event.startswith("pipeline:") for event in events[2:])
    assert evaluation.embedding_cache == {
        "provider_calls": 2,
        "cache_hits": 4,
        "unique_questions": 2,
    }
    assert evaluation.latency_methodology["provider_calls_during_run"] == 0
    assert evaluation.latency_methodology["valid"] is True


def test_run_benchmark_without_rerank_preserves_three_argument_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_if_constructed() -> None:
        raise AssertionError("disabled reranker must not be constructed")

    calls: list[tuple[int, str, RetrievalConfig]] = []

    def fake_run_pipeline(
        repo_id: int,
        question: str,
        config: RetrievalConfig,
    ) -> PipelineResult:
        calls.append((repo_id, question, config))
        return pipeline_result(
            [retrieved_block(1, "module.vpc")],
            {"total_ms": 1.0},
        )

    monkeypatch.setattr(
        runner,
        "CrossEncoderReranker",
        fail_if_constructed,
    )
    monkeypatch.setattr(
        runner.pipeline,
        "run_pipeline",
        fake_run_pipeline,
    )
    entry = BenchmarkEntry(
        id="q001",
        question="What creates the VPC?",
        expected=["module.vpc"],
        category="lookup",
    )
    config = runner.ABLATION_CONFIGS[0][1]

    result = runner.run_benchmark(
        repo_id=42,
        entries=[entry],
        config=config,
        config_name="Vector only",
    )

    assert calls == [(42, entry.question, config)]
    assert result.reranker_json is None


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
    first_digest, first_count = runner.indexed_corpus_fingerprint(42)

    monkeypatch.setattr(
        runner.db,
        "fetch_resource_bodies",
        lambda repo_id: reordered_with_new_ids,
    )
    second_digest, second_count = runner.indexed_corpus_fingerprint(42)

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
        digest, _count = runner.indexed_corpus_fingerprint(42)
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
    reranker_json = {
        "model_name": "BAAI/bge-reranker-base",
        "max_length": 512,
        "sentence_transformers_version": "6.0.0",
        "model_revision": "model-abc123",
        "prepare_ms": 12.5,
        "enabled": True,
    }
    rerank_config_result = runner.ConfigResult(
        config_name="+ Cross-encoder rerank",
        config=runner.ABLATION_CONFIGS[3][1],
        per_question=[question],
        aggregate=aggregate([question]),
        by_category=aggregate_by_category([question]),
        reranker_json=reranker_json,
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
        results=[config_result, rerank_config_result],
    )

    assert report["schema_version"] == 3
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
    assert report["results"][0]["reranker_json"] is None
    assert report["results"][1]["config_name"] == (
        "+ Cross-encoder rerank"
    )
    assert report["results"][1]["reranker_json"] == reranker_json
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


def test_build_report_includes_optional_execution_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        runner.db,
        "fetch_repo",
        lambda repo_id: ("repo", None, "/tmp/repo"),
    )
    monkeypatch.setattr(
        runner.db,
        "fetch_resource_bodies",
        lambda repo_id: [],
    )
    monkeypatch.setattr(
        runner,
        "_corpus_git_revision",
        lambda local_path: "abc123",
    )
    embedding_cache = {
        "provider_calls": 40,
        "cache_hits": 200,
    }
    embedding_precomputation = {
        "provider_calls": 40,
        "total_ms": 123.0,
    }
    latency_methodology = {
        "provider_calls_during_run": 0,
        "valid": True,
    }

    report = runner.build_report(
        repo_id=42,
        benchmark_path="data/benchmark.json",
        benchmark_sha256="benchmark-hash",
        results=[],
        embedding_cache=embedding_cache,
        embedding_precomputation=embedding_precomputation,
        latency_methodology=latency_methodology,
    )

    assert report["schema_version"] == 3
    assert report["embedding_cache"] is embedding_cache
    assert report["embedding_precomputation"] is embedding_precomputation
    assert report["latency_methodology"] is latency_methodology
