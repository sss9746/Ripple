from pathlib import Path

import psycopg
import pytest

from ripple import db
from ripple.config import RetrievalConfig
from ripple.ingest import indexer
from ripple.retrieval import pipeline
from ripple.retrieval.graph import GraphNeighbor
from ripple.retrieval.vector_store import RetrievedBlock


QUESTION = "What creates the VPC?"
QUESTION_EMBEDDING = [0.25] * 1536
REFERENCE_FIXTURE_ROOT = (
    Path(__file__).parent / "fixtures" / "reference_repo"
).resolve()


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


def _neighbor(
    block_id: int,
    address: str,
    ref_text: str,
) -> GraphNeighbor:
    return GraphNeighbor(
        id=block_id,
        address=address,
        file_path="main.tf",
        start_line=1,
        end_line=5,
        body=f"body for {address}",
        embed_text=f"search text for {address}",
        ref_text=ref_text,
    )


class _FakeEmbeddingProvider:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(texts)
        return [QUESTION_EMBEDDING]


class _ZeroEmbeddingProvider:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 1536 for _ in texts]


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


def _install_graph(
    monkeypatch: pytest.MonkeyPatch,
    *,
    dependents_by_id: dict[int, list[GraphNeighbor]],
    dependencies_by_id: dict[int, list[GraphNeighbor]],
    allowed_ids: set[int] | None = None,
) -> list[tuple[str, int]]:
    calls: list[tuple[str, int]] = []
    allowed = allowed_ids or (
        set(dependents_by_id) | set(dependencies_by_id)
    )

    def fake_fetch_neighbors(
        repo_id: int,
        seed_ids: list[int],
        directions: tuple[str, ...] = ("dependent", "dependency"),
    ) -> dict[int, dict[str, list[GraphNeighbor]]]:
        assert repo_id == 3
        result: dict[int, dict[str, list[GraphNeighbor]]] = {}

        for resource_id in seed_ids:
            if resource_id not in allowed:
                pytest.fail(
                    f"Unexpected depth-two graph lookup: {resource_id}"
                )
            for direction in directions:
                calls.append((direction, resource_id))
                source = (
                    dependents_by_id
                    if direction == "dependent"
                    else dependencies_by_id
                )
                neighbors = source.get(resource_id, [])
                if neighbors:
                    result.setdefault(resource_id, {})[direction] = neighbors

        return result

    monkeypatch.setattr(pipeline, "fetch_neighbors", fake_fetch_neighbors)
    return calls


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
            use_graph=False,
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
            use_graph=False,
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
            use_graph=False,
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
            use_graph=False,
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
            use_graph=False,
        ),
    )

    assert result.blocks == []
    assert result.stages_json == {"final": []}
    assert set(result.latency_json) == {"total_ms"}


def test_config_json_separates_requested_and_executed_stages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_vector_store(monkeypatch, [])
    config = RetrievalConfig(
        use_bm25=False,
        use_rerank=True,
        use_graph=False,
    )
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
            use_graph=False,
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
                use_graph=False,
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
            use_graph=False,
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
            use_graph=False,
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
            use_graph=False,
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
            use_graph=False,
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
                use_graph=False,
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
            use_graph=False,
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
            use_graph=False,
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
            use_graph=False,
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
            use_graph=False,
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
            use_graph=False,
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
            use_graph=False,
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
            use_graph=False,
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
            use_graph=False,
            rerank_top_n=rerank_top_n,
        ),
        embedder=_FakeEmbeddingProvider(),
        reranker=reranker,
    )

    assert reranker.calls == [(QUESTION, [])]
    assert result.blocks == []
    assert result.stages_json["rerank"] == []


def test_disabled_graph_never_calls_graph_functions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    blocks = [_block(1, "aws_vpc.main", 8.0)]
    _install_bm25_index(monkeypatch, blocks)
    monkeypatch.setattr(pipeline, "fetch_neighbors", _unexpected_call)

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=RetrievalConfig(
            use_vector=False,
            use_rerank=False,
            use_graph=False,
        ),
    )

    assert result.blocks == blocks
    assert "graph" not in result.stages_json
    assert "graph_ms" not in result.latency_json
    assert result.config_json["executed"]["graph"] is False


def test_graph_uses_original_seeds_both_directions_at_depth_one_and_dedupes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [
        _block(1, "seed.one", 9.0),
        _block(2, "seed.two", 8.0),
        _block(3, "base.three", 7.0),
    ]
    _install_bm25_index(monkeypatch, candidates)
    duplicate_first = _neighbor(10, "new.shared", "seed.one.first")
    duplicate_later = _neighbor(10, "new.shared", "seed.one.later")
    calls = _install_graph(
        monkeypatch,
        dependents_by_id={
            1: [duplicate_first, _neighbor(11, "new.dependent", "dep")],
            2: [duplicate_later],
            10: [_neighbor(99, "depth.two", "must-not-run")],
        },
        dependencies_by_id={
            1: [duplicate_later],
            2: [_neighbor(12, "new.dependency", "dependency")],
        },
        allowed_ids={1, 2},
    )

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=RetrievalConfig(
            use_vector=False,
            use_rerank=False,
            use_graph=True,
            graph_seed_n=2,
            graph_max_added=10,
            final_k=10,
        ),
    )

    assert calls == [
        ("dependent", 1),
        ("dependency", 1),
        ("dependent", 2),
        ("dependency", 2),
    ]
    assert [block.id for block in result.blocks] == [1, 10, 11, 2, 12, 3]
    assert [row["id"] for row in result.stages_json["graph"]] == [10, 11, 12]
    assert result.stages_json["graph"][0]["ref_text"] == "seed.one.first"
    assert sum(block.id == 10 for block in result.blocks) == 1


def test_graph_promotes_lower_ranked_candidate_and_adds_unscored_neighbor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [
        _block(block_id, f"candidate.{block_id}", float(100 - block_id))
        for block_id in range(1, 13)
    ]
    _install_bm25_index(monkeypatch, candidates)
    calls = _install_graph(
        monkeypatch,
        dependents_by_id={
            1: [
                _neighbor(2, "candidate.2", "other.seed"),
                _neighbor(12, "candidate.12", "promoted.ref"),
                _neighbor(20, "new.block", "new.ref"),
            ],
            2: [],
        },
        dependencies_by_id={1: [], 2: []},
        allowed_ids={1, 2},
    )

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=RetrievalConfig(
            use_vector=False,
            use_rerank=False,
            use_graph=True,
            graph_seed_n=2,
            graph_max_added=10,
            final_k=10,
        ),
    )

    assert calls == [
        ("dependent", 1),
        ("dependency", 1),
        ("dependent", 2),
        ("dependency", 2),
    ]
    assert [block.id for block in result.blocks] == [
        1,
        12,
        20,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
    ]

    promoted = result.blocks[1]
    assert promoted.score == candidates[11].score
    assert promoted.graph_score_status == "promoted"
    assert promoted.graph_relationship == "dependent"
    assert promoted.graph_origin_address == "candidate.1"
    assert promoted.graph_ref_text == "promoted.ref"

    added = result.blocks[2]
    assert added.score is None
    assert added.graph_score_status == "unscored"
    assert added.graph_relationship == "dependent"
    assert added.graph_origin_address == "candidate.1"
    assert added.graph_ref_text == "new.ref"

    unchanged_seed = result.blocks[3]
    assert unchanged_seed == candidates[1]
    assert unchanged_seed.graph_score_status is None

    assert result.stages_json["graph"] == [
        {
            "id": 12,
            "address": "candidate.12",
            "score": candidates[11].score,
            "score_status": "promoted",
            "relationship": "dependent",
            "origin_address": "candidate.1",
            "ref_text": "promoted.ref",
        },
        {
            "id": 20,
            "address": "new.block",
            "score": None,
            "score_status": "unscored",
            "relationship": "dependent",
            "origin_address": "candidate.1",
            "ref_text": "new.ref",
        },
    ]


def test_graph_global_cap_counts_promotions_and_additions_together(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidates = [
        _block(block_id, f"candidate.{block_id}", float(100 - block_id))
        for block_id in range(1, 13)
    ]
    _install_bm25_index(monkeypatch, candidates)
    calls = _install_graph(
        monkeypatch,
        dependents_by_id={
            1: [_neighbor(12, "candidate.12", "promotion")],
            2: [_neighbor(20, "new.block", "addition")],
        },
        dependencies_by_id={1: [], 2: []},
        allowed_ids={1, 2},
    )

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=RetrievalConfig(
            use_vector=False,
            use_rerank=False,
            use_graph=True,
            graph_seed_n=2,
            graph_max_added=1,
            final_k=20,
        ),
    )

    assert calls == [
        ("dependent", 1),
        ("dependency", 1),
        ("dependent", 2),
        ("dependency", 2),
    ]
    assert [row["id"] for row in result.stages_json["graph"]] == [12]
    assert sum(block.id == 12 for block in result.blocks) == 1
    assert all(block.id != 20 for block in result.blocks)


@pytest.mark.parametrize(
    ("graph_seed_n", "graph_max_added"),
    [(0, 10), (-1, 10), (2, 0), (2, -1)],
)
def test_nonpositive_graph_limits_perform_no_lookups(
    monkeypatch: pytest.MonkeyPatch,
    graph_seed_n: int,
    graph_max_added: int,
) -> None:
    blocks = [_block(1, "aws_vpc.main", 8.0)]
    _install_bm25_index(monkeypatch, blocks)
    monkeypatch.setattr(pipeline, "fetch_neighbors", _unexpected_call)

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=RetrievalConfig(
            use_vector=False,
            use_rerank=False,
            use_graph=True,
            graph_seed_n=graph_seed_n,
            graph_max_added=graph_max_added,
        ),
    )

    assert result.blocks == blocks
    assert result.stages_json["graph"] == []
    assert "graph_ms" in result.latency_json
    assert result.config_json["executed"]["graph"] is True


def test_enabled_graph_with_empty_candidates_records_empty_stage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_bm25_index(monkeypatch, [])
    monkeypatch.setattr(pipeline, "fetch_neighbors", _unexpected_call)

    result = pipeline.run_pipeline(
        repo_id=3,
        question=QUESTION,
        config=RetrievalConfig(
            use_vector=False,
            use_rerank=False,
            use_graph=True,
        ),
    )

    assert result.blocks == []
    assert result.stages_json["graph"] == []
    assert "graph_ms" in result.latency_json
    assert result.config_json["executed"]["graph"] is True


@pytest.mark.parametrize(
    (
        "question",
        "expected_intent",
        "expected_directions",
        "expected_calls",
    ),
    [
        (
            "Which module creates the VPC?",
            "lookup",
            [],
            [],
        ),
        (
            "Which blocks contain an exact reference to module.vpc?",
            "attribute",
            [],
            [],
        ),
        (
            "What does aws_subnet.public depend on?",
            "dependency",
            ["dependency"],
            [("dependency", 1)],
        ),
        (
            "What is affected if aws_vpc.main is removed?",
            "blast_radius",
            ["dependent"],
            [("dependent", 1)],
        ),
        (
            "How does aws_subnet.public relate to aws_vpc.main?",
            "ambiguous_relationship",
            ["dependent", "dependency"],
            [("dependent", 1), ("dependency", 1)],
        ),
    ],
)
def test_graph_routes_by_question_intent(
    monkeypatch: pytest.MonkeyPatch,
    question: str,
    expected_intent: str,
    expected_directions: list[str],
    expected_calls: list[tuple[str, int]],
) -> None:
    blocks = [_block(1, "seed.one", 9.0)]
    _install_bm25_index(monkeypatch, blocks)
    calls = _install_graph(
        monkeypatch,
        dependents_by_id={1: [_neighbor(2, "dependent.one", "dep.ref")]},
        dependencies_by_id={
            1: [_neighbor(3, "dependency.one", "dependency.ref")]
        },
        allowed_ids={1},
    )

    result = pipeline.run_pipeline(
        repo_id=3,
        question=question,
        config=RetrievalConfig(
            use_vector=False,
            use_rerank=False,
            use_graph=True,
            graph_route_by_intent=True,
            graph_seed_n=1,
            graph_max_added=10,
            final_k=10,
        ),
    )

    assert result.stages_json["graph_intent"] == {
        "intent": expected_intent,
        "directions": expected_directions,
    }
    assert calls == expected_calls


def test_graph_expansion_with_real_reference_repository() -> None:
    try:
        connection = db.get_connection()
    except (RuntimeError, psycopg.OperationalError):
        pytest.skip("database not reachable")
    else:
        connection.close()

    repo_id: int | None = None

    try:
        repo_id = db.insert_repo(
            name="pytest-day13-pipeline-graph",
            source_url=None,
            local_path=str(REFERENCE_FIXTURE_ROOT),
        )
        assert indexer.index_repo(
            repo_id,
            str(REFERENCE_FIXTURE_ROOT),
            embedder=_ZeroEmbeddingProvider(),
        ) == 7
        assert indexer.index_edges(repo_id) == 5

        result = pipeline.run_pipeline(
            repo_id=repo_id,
            question="aws_vpc.main",
            config=RetrievalConfig(
                use_vector=False,
                use_bm25=True,
                bm25_k=1,
                use_rerank=False,
                use_graph=True,
                graph_seed_n=1,
                graph_max_added=10,
                final_k=10,
            ),
        )

        assert [row["address"] for row in result.stages_json["bm25"]] == [
            "aws_vpc.main"
        ]
        graph_rows = {
            row["address"]: row for row in result.stages_json["graph"]
        }

        for address in (
            "aws_security_group.worker",
            "aws_subnet.public",
        ):
            assert graph_rows[address] == {
                "id": graph_rows[address]["id"],
                "address": address,
                "score": None,
                "score_status": "unscored",
                "relationship": "dependent",
                "origin_address": "aws_vpc.main",
                "ref_text": "aws_vpc.main.id",
            }
    except psycopg.OperationalError:
        pytest.skip("database not reachable")
    finally:
        if repo_id is not None:
            with db.get_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM repos WHERE id = %s",
                        (repo_id,),
                    )
