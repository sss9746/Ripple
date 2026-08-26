import psycopg
import pytest

from ripple import db
from ripple.config import RetrievalConfig
from ripple.retrieval.pipeline import PipelineResult
from ripple.retrieval.vector_store import RetrievedBlock
from scripts import ask as ask_module


QUESTION = "What creates the VPC?"


def _block() -> RetrievedBlock:
    return RetrievedBlock(
        id=1,
        address="aws_vpc.main",
        file_path="main.tf",
        start_line=1,
        end_line=10,
        body='resource "aws_vpc" "main" {}',
        score=0.0325,
    )


def _pipeline_result(
    blocks: list[RetrievedBlock],
) -> PipelineResult:
    serialized_blocks = [
        {
            "id": block.id,
            "address": block.address,
            "score": block.score,
        }
        for block in blocks
    ]
    return PipelineResult(
        blocks=blocks,
        config_json={
            "requested": {
                "use_vector": True,
                "use_bm25": True,
                "use_rrf": True,
            },
            "executed": {
                "vector": True,
                "bm25": True,
                "fusion": True,
                "fusion_method": "rrf",
                "rerank": False,
                "graph": False,
                "rewrite": False,
            },
        },
        stages_json={
            "vector": serialized_blocks,
            "bm25": serialized_blocks,
            "fusion": serialized_blocks,
            "final": serialized_blocks,
        },
        latency_json={
            "vector_query_ms": 10.0,
            "bm25_ms": 2.0,
            "fusion_ms": 0.5,
            "total_ms": 12.5,
        },
    )


def test_ask_runs_pipeline_generates_answer_and_writes_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = RetrievalConfig(final_k=5)
    pipeline_result = _pipeline_result([_block()])
    pipeline_calls: list[tuple[int, str, RetrievalConfig]] = []
    answer_calls: list[tuple[str, list[RetrievedBlock]]] = []
    log_calls: list[dict] = []

    def fake_run_pipeline(
        repo_id: int,
        question: str,
        received_config: RetrievalConfig,
    ) -> PipelineResult:
        pipeline_calls.append((repo_id, question, received_config))
        return pipeline_result

    def fake_answer_question(
        question: str,
        blocks: list[RetrievedBlock],
    ) -> str:
        answer_calls.append((question, blocks))
        return "canned answer"

    def fake_insert_query_log(**kwargs: object) -> int:
        log_calls.append(kwargs)
        return 123

    monkeypatch.setattr(
        ask_module.pipeline,
        "run_pipeline",
        fake_run_pipeline,
    )
    monkeypatch.setattr(
        ask_module,
        "answer_question",
        fake_answer_question,
    )
    monkeypatch.setattr(
        ask_module.db,
        "insert_query_log",
        fake_insert_query_log,
    )

    answer = ask_module.ask(3, QUESTION, config)

    assert answer == "canned answer"
    assert pipeline_calls == [(3, QUESTION, config)]
    assert answer_calls == [(QUESTION, pipeline_result.blocks)]
    assert log_calls == [
        {
            "repo_id": 3,
            "question": QUESTION,
            "config_json": pipeline_result.config_json,
            "stages_json": pipeline_result.stages_json,
            "latency_json": pipeline_result.latency_json,
            "answer": "canned answer",
        }
    ]


def test_ask_logs_empty_results_without_generating_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipeline_result = _pipeline_result([])
    log_calls: list[dict] = []

    monkeypatch.setattr(
        ask_module.pipeline,
        "run_pipeline",
        lambda _repo_id, _question, _config: pipeline_result,
    )

    def unexpected_answer(*args: object) -> str:
        pytest.fail(f"answer_question should not be called: {args}")

    def fake_insert_query_log(**kwargs: object) -> int:
        log_calls.append(kwargs)
        return 124

    monkeypatch.setattr(ask_module, "answer_question", unexpected_answer)
    monkeypatch.setattr(
        ask_module.db,
        "insert_query_log",
        fake_insert_query_log,
    )

    answer = ask_module.ask(3, QUESTION)

    assert answer == ask_module.NO_RESULTS_MESSAGE
    assert log_calls[0]["answer"] is None
    assert log_calls[0]["stages_json"]["final"] == []


def test_ask_uses_default_retrieval_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    received_configs: list[RetrievalConfig] = []
    pipeline_result = _pipeline_result([])

    def fake_run_pipeline(
        _repo_id: int,
        _question: str,
        config: RetrievalConfig,
    ) -> PipelineResult:
        received_configs.append(config)
        return pipeline_result

    monkeypatch.setattr(
        ask_module.pipeline,
        "run_pipeline",
        fake_run_pipeline,
    )
    monkeypatch.setattr(
        ask_module.db,
        "insert_query_log",
        lambda **_kwargs: 125,
    )

    ask_module.ask(3, QUESTION)

    assert received_configs == [RetrievalConfig()]


def test_main_builds_default_and_overridden_configs(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[int, str, RetrievalConfig]] = []

    def fake_ask(
        repo_id: int,
        question: str,
        config: RetrievalConfig,
    ) -> str:
        calls.append((repo_id, question, config))
        return "printed answer"

    monkeypatch.setattr(ask_module, "ask", fake_ask)

    ask_module.main(["3", QUESTION])
    ask_module.main(["3", QUESTION, "--final-k", "5"])

    assert calls == [
        (3, QUESTION, RetrievalConfig()),
        (3, QUESTION, RetrievalConfig(final_k=5)),
    ]
    assert capsys.readouterr().out == "printed answer\nprinted answer\n"


def test_ask_writes_a_fully_reconstructable_query_log(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    try:
        repo_id = db.insert_repo(
            name="pytest-day6-ask-log",
            source_url=None,
            local_path="/tmp/pytest-day6-ask-log",
        )
    except (RuntimeError, psycopg.OperationalError):
        pytest.skip("database not reachable")

    pipeline_result = _pipeline_result([_block()])
    monkeypatch.setattr(
        ask_module.pipeline,
        "run_pipeline",
        lambda _repo_id, _question, _config: pipeline_result,
    )
    monkeypatch.setattr(
        ask_module,
        "answer_question",
        lambda _question, _blocks: "database-backed canned answer",
    )

    try:
        answer = ask_module.ask(repo_id, QUESTION)

        with db.get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT question, config_json, stages_json,
                           latency_json, answer
                    FROM query_logs
                    WHERE repo_id = %s
                    """,
                    (repo_id,),
                )
                saved_row = cursor.fetchone()

        assert answer == "database-backed canned answer"
        assert saved_row == (
            QUESTION,
            pipeline_result.config_json,
            pipeline_result.stages_json,
            pipeline_result.latency_json,
            "database-backed canned answer",
        )
        assert saved_row[2]["final"] == pipeline_result.stages_json["final"]
    finally:
        with db.get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM repos WHERE id = %s", (repo_id,))
