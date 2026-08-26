import dataclasses
import time
from dataclasses import dataclass

from ripple.config import RetrievalConfig
from ripple.llm.embeddings import EmbeddingProvider, OpenAIEmbeddingProvider
from ripple.retrieval import fusion
from ripple.retrieval.bm25 import build_index
from ripple.retrieval.pgvector_store import PgVectorStore
from ripple.retrieval.vector_store import RetrievedBlock, VectorStore


SUPPORTED_VECTOR_BACKENDS = ("pgvector",)


@dataclass
class PipelineResult:
    blocks: list[RetrievedBlock]
    config_json: dict
    stages_json: dict[str, list[dict]]
    latency_json: dict[str, float]


def _serialize(
    blocks: list[RetrievedBlock],
) -> list[dict]:
    return [
        {
            "id": block.id,
            "address": block.address,
            "score": block.score,
        }
        for block in blocks
    ]


def _get_vector_store(
    config: RetrievalConfig,
) -> VectorStore:
    if config.vector_backend == "pgvector":
        return PgVectorStore()

    raise ValueError(
        f"Unsupported vector_backend {config.vector_backend!r}; "
        f"only {SUPPORTED_VECTOR_BACKENDS} are implemented"
    )


def _build_config_json(
    config: RetrievalConfig,
) -> dict:
    fusion_will_run = config.use_vector and config.use_bm25

    if not fusion_will_run:
        fusion_method = None
    elif config.use_rrf:
        fusion_method = "rrf"
    else:
        fusion_method = "concat_dedup"

    return {
        "requested": dataclasses.asdict(config),
        "executed": {
            "vector": config.use_vector,
            "bm25": config.use_bm25,
            "fusion": fusion_will_run,
            "fusion_method": fusion_method,
            "rerank": False,
            "graph": False,
            "rewrite": False,
        },
    }


def run_pipeline(
    repo_id: int,
    question: str,
    config: RetrievalConfig,
    embedder: EmbeddingProvider | None = None,
) -> PipelineResult:
    total_start = time.perf_counter()

    stages_json: dict[str, list[dict]] = {}
    latency_json: dict[str, float] = {}

    vector_results: list[RetrievedBlock] = []
    bm25_results: list[RetrievedBlock] = []

    if config.use_vector:
        vector_store = _get_vector_store(config)
        vector_start = time.perf_counter()

        if config.vector_k > 0:
            embedder = embedder or OpenAIEmbeddingProvider()
            [question_embedding] = embedder.embed([question])

            vector_results = vector_store.query(
                repo_id,
                question_embedding,
                config.vector_k,
            )

        latency_json["vector_query_ms"] = (
            time.perf_counter() - vector_start
        ) * 1000
        stages_json["vector"] = _serialize(vector_results)

    if config.use_bm25:
        bm25_start = time.perf_counter()

        bm25_results = build_index(repo_id).query(
            question,
            config.bm25_k,
        )

        latency_json["bm25_ms"] = (
            time.perf_counter() - bm25_start
        ) * 1000
        stages_json["bm25"] = _serialize(bm25_results)

    if config.use_vector and config.use_bm25:
        fusion_start = time.perf_counter()

        if config.use_rrf:
            candidates = fusion.fuse(
                [vector_results, bm25_results],
                k=config.rrf_k,
            )
        else:
            candidates = fusion.concat_dedup(
                [vector_results, bm25_results]
            )

        latency_json["fusion_ms"] = (
            time.perf_counter() - fusion_start
        ) * 1000
        stages_json["fusion"] = _serialize(candidates)

    elif config.use_vector:
        candidates = vector_results

    elif config.use_bm25:
        candidates = bm25_results

    else:
        candidates = []

    if config.final_k > 0:
        blocks = candidates[: config.final_k]
    else:
        blocks = []

    stages_json["final"] = _serialize(blocks)
    latency_json["total_ms"] = (
        time.perf_counter() - total_start
    ) * 1000

    return PipelineResult(
        blocks=blocks,
        config_json=_build_config_json(config),
        stages_json=stages_json,
        latency_json=latency_json,
    )