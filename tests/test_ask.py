import pytest

from ripple.retrieval.vector_store import RetrievedBlock
from scripts import ask as ask_module


QUESTION = "What creates the VPC?"
QUESTION_EMBEDDING = [0.25] * 1536


def _block() -> RetrievedBlock:
    return RetrievedBlock(
        id=1,
        address="aws_vpc.main",
        file_path="main.tf",
        start_line=1,
        end_line=10,
        body='resource "aws_vpc" "main" {}',
        score=0.95,
    )


class _FakeEmbeddingProvider:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [QUESTION_EMBEDDING]


class _FakePgVectorStore:
    def __init__(self, blocks: list[RetrievedBlock]) -> None:
        self.blocks = blocks
        self.calls: list[tuple[int, list[float], int]] = []

    def query(
        self,
        repo_id: int,
        embedding: list[float],
        k: int,
    ) -> list[RetrievedBlock]:
        self.calls.append((repo_id, embedding, k))
        return self.blocks


def test_ask_embeds_searches_and_generates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedder = _FakeEmbeddingProvider()
    blocks = [_block()]
    store = _FakePgVectorStore(blocks)
    answer_calls: list[tuple[str, list[RetrievedBlock]]] = []

    def fake_answer(
        question: str,
        retrieved_blocks: list[RetrievedBlock],
    ) -> str:
        answer_calls.append((question, retrieved_blocks))
        return "canned answer"

    monkeypatch.setattr(
        ask_module,
        "OpenAIEmbeddingProvider",
        lambda: embedder,
    )
    monkeypatch.setattr(ask_module, "PgVectorStore", lambda: store)
    monkeypatch.setattr(ask_module, "answer_question", fake_answer)

    result = ask_module.ask(3, QUESTION)

    assert result == "canned answer"
    assert embedder.calls == [[QUESTION]]
    assert store.calls == [(3, QUESTION_EMBEDDING, ask_module.DEFAULT_TOP_K)]
    assert answer_calls == [(QUESTION, blocks)]


def test_ask_skips_generation_when_search_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    embedder = _FakeEmbeddingProvider()
    store = _FakePgVectorStore([])

    def unexpected_answer(*args: object) -> str:
        pytest.fail(f"answer_question should not be called: {args}")

    monkeypatch.setattr(
        ask_module,
        "OpenAIEmbeddingProvider",
        lambda: embedder,
    )
    monkeypatch.setattr(ask_module, "PgVectorStore", lambda: store)
    monkeypatch.setattr(ask_module, "answer_question", unexpected_answer)

    result = ask_module.ask(3, QUESTION)

    assert result == (
        "No indexed resources found for this repo — nothing to answer from."
    )
    assert store.calls == [(3, QUESTION_EMBEDDING, ask_module.DEFAULT_TOP_K)]


def test_main_parses_arguments_and_prints_answer(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    calls: list[tuple[int, str, int]] = []

    def fake_ask(repo_id: int, question: str, top_k: int) -> str:
        calls.append((repo_id, question, top_k))
        return "printed answer"

    monkeypatch.setattr(ask_module, "ask", fake_ask)

    ask_module.main(["3", QUESTION])

    assert calls == [(3, QUESTION, ask_module.DEFAULT_TOP_K)]
    assert capsys.readouterr().out == "printed answer\n"
