import builtins
from importlib.metadata import version as installed_version
import json
from types import SimpleNamespace

import numpy as np
import pytest

from ripple.retrieval.rerank import CrossEncoderReranker
from ripple.retrieval.vector_store import RetrievedBlock


class _FakeModel:
    def __init__(self, scores: list[float]) -> None:
        self.scores = scores
        self.calls: list[list[tuple[str, str]]] = []

    def predict(
        self,
        pairs: list[tuple[str, str]],
    ) -> list[float]:
        self.calls.append(pairs)
        return self.scores


def _block(
    block_id: int,
    address: str,
    *,
    body: str,
    embed_text: str,
) -> RetrievedBlock:
    return RetrievedBlock(
        id=block_id,
        address=address,
        file_path="main.tf",
        start_line=1,
        end_line=5,
        body=body,
        embed_text=embed_text,
        score=0.0,
    )


def test_rerank_batches_embed_text_and_sorts_by_score() -> None:
    model = _FakeModel([0.2, 0.9])
    reranker = CrossEncoderReranker(model=model)
    candidates = [
        _block(
            1,
            "aws_vpc.main",
            body="raw VPC body",
            embed_text="searchable VPC text",
        ),
        _block(
            2,
            "aws_security_group.rds",
            body="raw security-group body",
            embed_text="searchable RDS security-group text",
        ),
    ]

    results = reranker.rerank(
        "What creates the RDS security group?",
        candidates,
    )

    assert model.calls == [
        [
            (
                "What creates the RDS security group?",
                "searchable VPC text",
            ),
            (
                "What creates the RDS security group?",
                "searchable RDS security-group text",
            ),
        ]
    ]
    assert [result.id for result in results] == [2, 1]
    assert [result.score for result in results] == [0.9, 0.2]


def test_rerank_empty_candidates_never_loads_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reranker = CrossEncoderReranker()

    def fail_if_called():
        raise AssertionError(
            "model must not be constructed for empty input"
        )

    monkeypatch.setattr(reranker, "_get_model", fail_if_called)

    assert reranker.rerank("question", []) == []


@pytest.mark.parametrize(
    "scores",
    [
        [0.5],
        [0.1, 0.2, 0.3],
    ],
)
def test_rerank_rejects_wrong_score_count(
    scores: list[float],
) -> None:
    model = _FakeModel(scores)
    reranker = CrossEncoderReranker(model=model)
    candidates = [
        _block(
            1,
            "example.one",
            body="first body",
            embed_text="first embed text",
        ),
        _block(
            2,
            "example.two",
            body="second body",
            embed_text="second embed text",
        ),
    ]

    with pytest.raises(
        ValueError,
        match=(
            rf"returned {len(scores)} scores "
            r"for 2 candidates"
        ),
    ):
        reranker.rerank("question", candidates)


def test_rerank_sorts_equal_scores_by_address() -> None:
    model = _FakeModel([0.5, 0.5])
    reranker = CrossEncoderReranker(model=model)
    candidates = [
        _block(
            1,
            "example.z",
            body="z body",
            embed_text="z embed text",
        ),
        _block(
            2,
            "example.a",
            body="a body",
            embed_text="a embed text",
        ),
    ]

    results = reranker.rerank("question", candidates)

    assert [result.address for result in results] == [
        "example.a",
        "example.z",
    ]


def test_prepare_runs_one_dummy_prediction_and_is_idempotent() -> None:
    model = _FakeModel([0.5])
    reranker = CrossEncoderReranker(model=model)

    reranker.prepare()
    first_prepare_ms = reranker.prepare_ms
    reranker.prepare()

    assert model.calls == [[("prepare", "prepare")]]
    assert isinstance(first_prepare_ms, float)
    assert first_prepare_ms >= 0
    assert reranker.prepare_ms == first_prepare_ms


def test_describe_returns_provenance_without_importing_model_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    model = _FakeModel([0.5])
    reranker = CrossEncoderReranker(model=model)
    real_import = builtins.__import__

    def guarded_import(
        name: str,
        *args: object,
        **kwargs: object,
    ):
        if name == "sentence_transformers" or name.startswith(
            "sentence_transformers."
        ):
            raise AssertionError(
                "describe() must not import sentence_transformers"
            )
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    description = reranker.describe()

    assert description == {
        "model_name": "BAAI/bge-reranker-base",
        "max_length": 512,
        "sentence_transformers_version": installed_version(
            "sentence-transformers"
        ),
        "model_revision": "unavailable",
        "prepare_ms": None,
        "enabled": True,
    }


def test_describe_reads_model_revision_from_wrapped_config() -> None:
    model = SimpleNamespace(
        model=SimpleNamespace(
            config=SimpleNamespace(_commit_hash="abc123")
        )
    )
    reranker = CrossEncoderReranker(model=model)

    assert reranker.describe()["model_revision"] == "abc123"


def test_rerank_normalizes_numpy_scores_to_json_safe_floats() -> None:
    model = _FakeModel(np.array([0.25, 0.75], dtype=np.float32))
    reranker = CrossEncoderReranker(model=model)
    candidates = [
        _block(
            1,
            "example.one",
            body="first body",
            embed_text="first embed text",
        ),
        _block(
            2,
            "example.two",
            body="second body",
            embed_text="second embed text",
        ),
    ]

    results = reranker.rerank("question", candidates)

    assert all(type(result.score) is float for result in results)
    json.dumps([result.score for result in results])


def test_rerank_preserves_and_scores_duplicate_candidate_ids() -> None:
    model = _FakeModel([0.2, 0.8])
    reranker = CrossEncoderReranker(model=model)
    candidates = [
        _block(
            7,
            "example.first",
            body="first body",
            embed_text="first embed text",
        ),
        _block(
            7,
            "example.second",
            body="second body",
            embed_text="second embed text",
        ),
    ]

    results = reranker.rerank("question", candidates)

    assert len(results) == 2
    assert [result.id for result in results] == [7, 7]
    assert [result.address for result in results] == [
        "example.second",
        "example.first",
    ]
    assert [result.score for result in results] == [0.8, 0.2]
