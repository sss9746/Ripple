import pytest

from ripple.config import RetrievalConfig
from ripple.retrieval import pipeline
from ripple.retrieval.vector_store import RetrievedBlock


QUESTION = "What creates the VPC?"
QUESTION_EMBEDDING = [0.25] * 1536


def _block(
    block_id: int,
    address: str,
    score: float,
    embed_text: str = "embed text",
) -> RetrievedBlock:
    return RetrievedBlock(
        id=block_id,
        address=address,
        file_path="main.tf",
        start_line=1,
        end_line=5,
        body="resource body",
        embed_text=embed_text,
        score=score,
    )


def _serialized(blocks: list[RetrievedBlock]) -> list[dict]:
    return [
        {
            "id": block.id,
            "address": block.address,
            "score": block.score,
        }
        for block in blocks
    ]


class _FakeEmbeddingProvider:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [QUESTION_EMBEDDING]


class _FakeVectorStore:
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


class _FakeBM25Index:
    def __init__(self, blocks: list[RetrievedBlock]) -> None:
        self.blocks = blocks
        self.calls: list[tuple[str, int]] = []

    def query(self, question: str, k: int) -> list[RetrievedBlock]:
        self.calls.append((question, k))
        return self.blocks if k > 0 else []


class _FakeReranker:
    def __init__(self, *, reverse: bool = False) -> None:
        self.reverse = reverse
        self.calls: list[tuple[str, list[RetrievedBlock]]] = []

    def rerank(
        self,
        question: str,
        candidates: list[RetrievedBlock],
    ) -> list[RetrievedBlock]:
        self.calls.append((question, candidates))
        if self.reverse:
            return list(reversed(candidates))
        return candidates


def _install_vector_store(
    monkeypatch: pytest.MonkeyPatch,
    blocks: list[RetrievedBlock],
) -> _FakeVectorStore:
    store = _FakeVectorStore(blocks)
    monkeypatch.setattr(pipeline, "PgVectorStore", lambda: store)
    return store


def _install_bm25_index(
    monkeypatch: pytest.MonkeyPatch,
    blocks: list[RetrievedBlock],
) -> tuple[_FakeBM25Index, list[int]]:
    index = _FakeBM25Index(blocks)
    build_calls: list[int] = []

    def fake_build_index(repo_id: int) -> _FakeBM25Index:
        build_calls.append(repo_id)
        return index

    monkeypatch.setattr(pipeline, "build_index", fake_build_index)
    return index, build_calls


def _unexpected_call(*args: object, **kwargs: object) -> None:
    pytest.fail(f"Unexpected call: args={args}, kwargs={kwargs}")


def test_run_pipeline_vector_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vector_blocks = [_block(1, "aws_vpc.main", 0.95)]
    vector_store = _install_vector_store(monkeypatch, vector_blocks)
    embedder = _FakeEmbeddingProvider()
    monkeypatch.setattr(pipeline, "build_index", _unexpected_call)

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=RetrievalConfig(
            use_bm25=False,
            use_rerank=False,
            final_k=8,
        ),
        embedder=embedder,
    )

    assert result.blocks == vector_blocks
    assert embedder.calls == [[QUESTION]]
    assert vector_store.calls == [(3, QUESTION_EMBEDDING, 30)]
    assert set(result.stages_json) == {"vector", "final"}
    assert set(result.latency_json) == {"vector_query_ms", "total_ms"}


def test_run_pipeline_bm25_only_without_openai_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    bm25_blocks = [_block(2, "aws_subnet.public", 8.0)]
    bm25_index, build_calls = _install_bm25_index(
        monkeypatch,
        bm25_blocks,
    )
    monkeypatch.setattr(pipeline, "OpenAIEmbeddingProvider", _unexpected_call)
    monkeypatch.setattr(pipeline, "PgVectorStore", _unexpected_call)

    result = pipeline.run_pipeline(
        repo_id=4,
        question=QUESTION,
        config=RetrievalConfig(
            use_vector=False,
            use_rerank=False,
            final_k=8,
        ),
    )

    assert result.blocks == bm25_blocks
    assert build_calls == [4]
    assert bm25_index.calls == [(QUESTION, 30)]
    assert set(result.stages_json) == {"bm25", "final"}
    assert set(result.latency_json) == {"bm25_ms", "total_ms"}


def test_run_pipeline_fuses_vector_and_bm25_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vector_blocks = [
        _block(1, "aws_vpc.main", 0.95),
        _block(2, "aws_subnet.public", 0.85),
    ]
    bm25_blocks = [
        _block(2, "aws_subnet.public", 8.0),
        _block(3, "aws_security_group.worker", 7.0),
    ]
    _install_vector_store(monkeypatch, vector_blocks)
    _install_bm25_index(monkeypatch, bm25_blocks)

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=RetrievalConfig(
            use_rerank=False,
            final_k=3,
            rrf_k=60,
        ),
        embedder=_FakeEmbeddingProvider(),
    )

    assert [block.id for block in result.blocks] == [2, 1, 3]
    assert [block.score for block in result.blocks] == pytest.approx(
        [(1 / 62) + (1 / 61), 1 / 61, 1 / 62]
    )
    assert set(result.stages_json) == {
        "vector",
        "bm25",
        "fusion",
        "final",
    }
    assert set(result.latency_json) == {
        "vector_query_ms",
        "bm25_ms",
        "fusion_ms",
        "total_ms",
    }


def test_run_pipeline_concatenates_when_rrf_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vector_blocks = [
        _block(1, "aws_vpc.main", 0.95),
        _block(2, "aws_subnet.public", 0.85),
    ]
    bm25_blocks = [
        _block(2, "aws_subnet.public", 8.0),
        _block(3, "aws_security_group.worker", 7.0),
    ]
    _install_vector_store(monkeypatch, vector_blocks)
    _install_bm25_index(monkeypatch, bm25_blocks)

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=RetrievalConfig(
            use_rrf=False,
            use_rerank=False,
            final_k=3,
        ),
        embedder=_FakeEmbeddingProvider(),
    )

    assert [block.id for block in result.blocks] == [2, 1, 3]
    assert [block.score for block in result.blocks] == [8.0, 0.95, 7.0]
    assert result.config_json["executed"]["fusion_method"] == "concat_dedup"


def test_run_pipeline_with_both_retrievers_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline, "OpenAIEmbeddingProvider", _unexpected_call)
    monkeypatch.setattr(pipeline, "PgVectorStore", _unexpected_call)
    monkeypatch.setattr(pipeline, "build_index", _unexpected_call)

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=RetrievalConfig(
            use_vector=False,
            use_bm25=False,
            use_rerank=False,
        ),
    )

    assert result.blocks == []
    assert result.stages_json == {"final": []}
    assert set(result.latency_json) == {"total_ms"}


def test_config_json_separates_requested_and_executed_stages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_vector_store(monkeypatch, [])
    config = RetrievalConfig(use_bm25=False, use_rerank=True)
    reranker = _FakeReranker()

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=config,
        embedder=_FakeEmbeddingProvider(),
        reranker=reranker,
    )

    assert result.config_json["requested"]["use_rerank"] is True
    assert result.config_json["executed"] == {
        "vector": True,
        "bm25": False,
        "fusion": False,
        "fusion_method": None,
        "rerank": True,
        "graph": False,
        "rewrite": False,
    }


@pytest.mark.parametrize(
    ("use_rrf", "expected_method"),
    [(True, "rrf"), (False, "concat_dedup")],
)
def test_config_json_records_executed_fusion_method(
    monkeypatch: pytest.MonkeyPatch,
    use_rrf: bool,
    expected_method: str,
) -> None:
    _install_vector_store(monkeypatch, [])
    _install_bm25_index(monkeypatch, [])

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=RetrievalConfig(
            use_rrf=use_rrf,
            use_rerank=False,
        ),
        embedder=_FakeEmbeddingProvider(),
    )

    assert result.config_json["executed"]["fusion"] is True
    assert result.config_json["executed"]["fusion_method"] == expected_method


def test_unsupported_vector_backend_fails_before_external_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline, "PgVectorStore", _unexpected_call)
    monkeypatch.setattr(pipeline, "OpenAIEmbeddingProvider", _unexpected_call)

    with pytest.raises(ValueError, match="Unsupported vector_backend"):
        pipeline.run_pipeline(
            repo_id=3,
            question=QUESTION,
            config=RetrievalConfig(
                vector_backend="pinecone",
                use_bm25=False,
                use_rerank=False,
            ),
        )


def test_unused_unsupported_vector_backend_does_not_raise(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(pipeline, "PgVectorStore", _unexpected_call)
    monkeypatch.setattr(pipeline, "OpenAIEmbeddingProvider", _unexpected_call)
    monkeypatch.setattr(pipeline, "build_index", _unexpected_call)

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=RetrievalConfig(
            vector_backend="pinecone",
            use_vector=False,
            use_bm25=False,
            use_rerank=False,
        ),
    )

    assert result.blocks == []


@pytest.mark.parametrize("final_k", [0, -1])
def test_nonpositive_final_k_returns_no_final_blocks(
    monkeypatch: pytest.MonkeyPatch,
    final_k: int,
) -> None:
    bm25_blocks = [
        _block(1, "aws_vpc.main", 8.0),
        _block(2, "aws_subnet.public", 7.0),
    ]
    _install_bm25_index(monkeypatch, bm25_blocks)

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=RetrievalConfig(
            use_vector=False,
            use_rerank=False,
            final_k=final_k,
        ),
    )

    assert result.blocks == []
    assert result.stages_json["bm25"] == _serialized(bm25_blocks)
    assert result.stages_json["final"] == []


@pytest.mark.parametrize("vector_k", [0, -1])
def test_nonpositive_vector_k_skips_embedding_and_query(
    monkeypatch: pytest.MonkeyPatch,
    vector_k: int,
) -> None:
    vector_store = _install_vector_store(monkeypatch, [])
    monkeypatch.setattr(pipeline, "OpenAIEmbeddingProvider", _unexpected_call)

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=RetrievalConfig(
            use_bm25=False,
            use_rerank=False,
            vector_k=vector_k,
        ),
    )

    assert vector_store.calls == []
    assert result.stages_json["vector"] == []


@pytest.mark.parametrize("bm25_k", [0, -1])
def test_nonpositive_bm25_k_returns_no_bm25_results(
    monkeypatch: pytest.MonkeyPatch,
    bm25_k: int,
) -> None:
    bm25_index, _build_calls = _install_bm25_index(
        monkeypatch,
        [_block(1, "aws_vpc.main", 8.0)],
    )

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=RetrievalConfig(
            use_vector=False,
            use_rerank=False,
            bm25_k=bm25_k,
        ),
    )

    assert bm25_index.calls == [(QUESTION, bm25_k)]
    assert result.stages_json["bm25"] == []


def test_negative_rrf_k_raises_when_rrf_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_vector_store(monkeypatch, [_block(1, "aws_vpc.main", 0.9)])
    _install_bm25_index(monkeypatch, [_block(1, "aws_vpc.main", 8.0)])

    with pytest.raises(ValueError, match="must be non-negative"):
        pipeline.run_pipeline(
            repo_id=3,
            question=QUESTION,
            config=RetrievalConfig(
                use_rerank=False,
                rrf_k=-1,
            ),
            embedder=_FakeEmbeddingProvider(),
        )


def test_negative_rrf_k_is_ignored_when_rrf_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_vector_store(monkeypatch, [_block(1, "aws_vpc.main", 0.9)])
    _install_bm25_index(monkeypatch, [_block(1, "aws_vpc.main", 8.0)])

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=RetrievalConfig(
            rrf_k=-1,
            use_rrf=False,
            use_rerank=False,
        ),
        embedder=_FakeEmbeddingProvider(),
    )

    assert [block.id for block in result.blocks] == [1]


@pytest.mark.parametrize(
    ("use_vector", "use_bm25"),
    [(True, False), (False, True)],
)
def test_negative_rrf_k_is_ignored_with_only_one_retriever(
    monkeypatch: pytest.MonkeyPatch,
    use_vector: bool,
    use_bm25: bool,
) -> None:
    _install_vector_store(monkeypatch, [_block(1, "aws_vpc.main", 0.9)])
    _install_bm25_index(monkeypatch, [_block(1, "aws_vpc.main", 8.0)])

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=RetrievalConfig(
            use_vector=use_vector,
            use_bm25=use_bm25,
            use_rerank=False,
            rrf_k=-1,
        ),
        embedder=_FakeEmbeddingProvider(),
    )

    assert [block.id for block in result.blocks] == [1]


def test_final_stage_matches_truncated_pipeline_blocks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vector_blocks = [
        _block(1, "aws_vpc.main", 0.95),
        _block(2, "aws_subnet.public", 0.85),
    ]
    bm25_blocks = [
        _block(2, "aws_subnet.public", 8.0),
        _block(3, "aws_security_group.worker", 7.0),
    ]
    _install_vector_store(monkeypatch, vector_blocks)
    _install_bm25_index(monkeypatch, bm25_blocks)

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=RetrievalConfig(
            use_rerank=False,
            final_k=2,
        ),
        embedder=_FakeEmbeddingProvider(),
    )

    assert result.stages_json["final"] == _serialized(result.blocks)
    assert len(result.stages_json["final"]) == 2
    assert len(result.stages_json["vector"]) == 2
    assert len(result.stages_json["bm25"]) == 2
    assert len(result.stages_json["fusion"]) == 3


def test_disabled_rerank_never_calls_reranker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vector_blocks = [_block(1, "aws_vpc.main", 0.95)]
    _install_vector_store(monkeypatch, vector_blocks)
    monkeypatch.setattr(pipeline, "build_index", _unexpected_call)
    reranker = _FakeReranker()

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=RetrievalConfig(
            use_bm25=False,
            use_rerank=False,
        ),
        embedder=_FakeEmbeddingProvider(),
        reranker=reranker,
    )

    assert reranker.calls == []
    assert result.blocks == vector_blocks
    assert "rerank" not in result.stages_json
    assert "rerank_ms" not in result.latency_json


def test_rerank_uses_fused_top_n_before_final_k(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vector_blocks = [
        _block(1, "example.a", 0.9),
        _block(2, "example.b", 0.8),
        _block(3, "example.c", 0.7),
    ]
    bm25_blocks = [_block(4, "example.d", 8.0)]
    _install_vector_store(monkeypatch, vector_blocks)
    _install_bm25_index(monkeypatch, bm25_blocks)
    reranker = _FakeReranker(reverse=True)

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=RetrievalConfig(
            use_rrf=False,
            use_rerank=True,
            rerank_top_n=3,
            final_k=1,
        ),
        embedder=_FakeEmbeddingProvider(),
        reranker=reranker,
    )

    assert len(reranker.calls) == 1
    assert reranker.calls[0][0] == QUESTION
    assert [block.id for block in reranker.calls[0][1]] == [1, 4, 2]
    assert [row["id"] for row in result.stages_json["rerank"]] == [
        2,
        4,
        1,
    ]
    assert [block.id for block in result.blocks] == [2]
    assert result.config_json["executed"]["rerank"] is True
    assert "rerank_ms" in result.latency_json


def test_enabled_rerank_runs_with_vector_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vector_blocks = [_block(1, "aws_vpc.main", 0.95)]
    _install_vector_store(monkeypatch, vector_blocks)
    monkeypatch.setattr(pipeline, "build_index", _unexpected_call)
    reranker = _FakeReranker()

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=RetrievalConfig(
            use_bm25=False,
            use_rerank=True,
        ),
        embedder=_FakeEmbeddingProvider(),
        reranker=reranker,
    )

    assert len(reranker.calls) == 1
    assert reranker.calls[0][1] == vector_blocks
    assert result.stages_json["rerank"] == _serialized(vector_blocks)


def test_enabled_rerank_records_empty_stage_for_no_candidates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_vector_store(monkeypatch, [])
    monkeypatch.setattr(pipeline, "build_index", _unexpected_call)
    reranker = _FakeReranker()

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=RetrievalConfig(
            use_bm25=False,
            use_rerank=True,
        ),
        embedder=_FakeEmbeddingProvider(),
        reranker=reranker,
    )

    assert reranker.calls == [(QUESTION, [])]
    assert result.stages_json["rerank"] == []
    assert result.stages_json["final"] == []
    assert result.config_json["executed"]["rerank"] is True
    assert "rerank_ms" in result.latency_json


@pytest.mark.parametrize("rerank_top_n", [0, -1])
def test_nonpositive_rerank_top_n_passes_empty_pool(
    monkeypatch: pytest.MonkeyPatch,
    rerank_top_n: int,
) -> None:
    _install_vector_store(
        monkeypatch,
        [_block(1, "aws_vpc.main", 0.95)],
    )
    monkeypatch.setattr(pipeline, "build_index", _unexpected_call)
    reranker = _FakeReranker()

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=RetrievalConfig(
            use_bm25=False,
            use_rerank=True,
            rerank_top_n=rerank_top_n,
        ),
        embedder=_FakeEmbeddingProvider(),
        reranker=reranker,
    )

    assert reranker.calls == [(QUESTION, [])]
    assert result.blocks == []
    assert result.stages_json["rerank"] == []
