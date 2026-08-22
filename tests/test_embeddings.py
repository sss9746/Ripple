from types import SimpleNamespace

import pytest

from ripple.llm.embeddings import (
    EMBEDDING_BATCH_SIZE,
    EMBEDDING_DIM,
    EMBEDDING_MODEL,
    OpenAIEmbeddingProvider,
)


class FakeEmbeddings:
    def __init__(self, responder):
        self.responder = responder
        self.calls: list[dict[str, object]] = []

    def create(self, *, model: str, input: list[str]):
        self.calls.append({"model": model, "input": list(input)})
        return self.responder(input)


class FakeOpenAIClient:
    def __init__(self, responder):
        self.embeddings = FakeEmbeddings(responder)


def make_response(
    texts: list[str],
    *,
    reverse: bool = False,
    dimension: int = EMBEDDING_DIM,
):
    items = [
        SimpleNamespace(
            index=index,
            embedding=[float(text.rsplit("-", 1)[-1])] * dimension,
        )
        for index, text in enumerate(texts)
    ]
    if reverse:
        items.reverse()
    return SimpleNamespace(data=items)


def test_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="OPENAI_API_KEY"):
        OpenAIEmbeddingProvider()


def test_empty_input_skips_api_call():
    client = FakeOpenAIClient(
        lambda texts: pytest.fail("The API should not be called for empty input")
    )
    provider = OpenAIEmbeddingProvider(client=client)

    assert provider.embed([]) == []
    assert client.embeddings.calls == []


def test_embed_batches_inputs_and_preserves_order():
    client = FakeOpenAIClient(make_response)
    provider = OpenAIEmbeddingProvider(client=client)
    texts = [f"text-{index}" for index in range(250)]

    embeddings = provider.embed(texts)

    assert [
        len(call["input"]) for call in client.embeddings.calls
    ] == [EMBEDDING_BATCH_SIZE, EMBEDDING_BATCH_SIZE, 50]
    assert all(
        call["model"] == EMBEDDING_MODEL
        for call in client.embeddings.calls
    )
    assert [
        text
        for call in client.embeddings.calls
        for text in call["input"]
    ] == texts
    assert len(embeddings) == len(texts)
    assert [embedding[0] for embedding in embeddings] == [
        float(index) for index in range(250)
    ]


def test_embed_uses_response_indexes_to_restore_order():
    client = FakeOpenAIClient(
        lambda texts: make_response(texts, reverse=True)
    )
    provider = OpenAIEmbeddingProvider(client=client)

    embeddings = provider.embed(["text-0", "text-1", "text-2"])

    assert [embedding[0] for embedding in embeddings] == [0.0, 1.0, 2.0]


def test_embed_rejects_wrong_response_count():
    client = FakeOpenAIClient(
        lambda texts: SimpleNamespace(data=make_response(texts).data[:1])
    )
    provider = OpenAIEmbeddingProvider(client=client)

    with pytest.raises(ValueError, match="returned 1 embeddings for 3 inputs"):
        provider.embed(["text-0", "text-1", "text-2"])


def test_embed_rejects_wrong_vector_dimension():
    client = FakeOpenAIClient(
        lambda texts: make_response(texts, dimension=10)
    )
    provider = OpenAIEmbeddingProvider(client=client)

    with pytest.raises(ValueError, match="10-dimension vector"):
        provider.embed(["text-0"])


def test_embed_rejects_invalid_response_index():
    client = FakeOpenAIClient(
        lambda texts: SimpleNamespace(
            data=[
                SimpleNamespace(index=1, embedding=[0.0] * EMBEDDING_DIM)
            ]
        )
    )
    provider = OpenAIEmbeddingProvider(client=client)

    with pytest.raises(ValueError, match="invalid index 1"):
        provider.embed(["text-0"])


def test_embed_rejects_duplicate_response_index():
    client = FakeOpenAIClient(
        lambda texts: SimpleNamespace(
            data=[
                SimpleNamespace(index=0, embedding=[0.0] * EMBEDDING_DIM),
                SimpleNamespace(index=0, embedding=[1.0] * EMBEDDING_DIM),
            ]
        )
    )
    provider = OpenAIEmbeddingProvider(client=client)

    with pytest.raises(ValueError, match="duplicate index 0"):
        provider.embed(["text-0", "text-1"])
