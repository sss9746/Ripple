import dataclasses
import time
from dataclasses import dataclass

from ripple.config import RetrievalConfig
from ripple.llm.embeddings import EmbeddingProvider, OpenAIEmbeddingProvider
from ripple.retrieval import fusion
from ripple.retrieval.bm25 import build_index
from ripple.retrieval.graph import fetch_neighbors
from ripple.retrieval.intent import classify_intent, directions_for_intent
from ripple.retrieval.pgvector_store import PgVectorStore
from ripple.retrieval.rerank import CrossEncoderReranker, Reranker
from ripple.retrieval.vector_store import RetrievedBlock, VectorStore


SUPPORTED_VECTOR_BACKENDS = ("pgvector",)


@dataclass
class PipelineResult:
    blocks: list[RetrievedBlock]
    config_json: dict
    stages_json: dict[str, list[dict] | dict]
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


def _serialize_graph(
    blocks: list[RetrievedBlock],
) -> list[dict]:
    return [
        {
            "id": block.id,
            "address": block.address,
            "score": block.score,
            "score_status": block.graph_score_status,
            "relationship": block.graph_relationship,
            "origin_address": block.graph_origin_address,
            "ref_text": block.graph_ref_text,
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
            "rerank": config.use_rerank,
            "graph": config.use_graph,
            "rewrite": False,
        },
    }


def run_pipeline(
    repo_id: int,
    question: str,
    config: RetrievalConfig,
    embedder: EmbeddingProvider | None = None,
    reranker: Reranker | None = None,
) -> PipelineResult:
    total_start = time.perf_counter()

    stages_json: dict[str, list[dict] | dict] = {}
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

    if config.use_rerank:
        rerank_start = time.perf_counter()

        if config.rerank_top_n > 0:
            rerank_candidates = candidates[: config.rerank_top_n]
        else:
            rerank_candidates = []

        active_reranker = (
            reranker
            if reranker is not None
            else CrossEncoderReranker()
        )
        candidates = active_reranker.rerank(
            question,
            rerank_candidates,
        )

        latency_json["rerank_ms"] = (
            time.perf_counter() - rerank_start
        ) * 1000
        stages_json["rerank"] = _serialize(candidates)

    if config.use_graph:
        graph_start = time.perf_counter()
        seed_n = max(config.graph_seed_n, 0)
        max_added = max(config.graph_max_added, 0)

        if config.graph_route_by_intent:
            graph_intent = classify_intent(question)
            directions = directions_for_intent(graph_intent)
            stages_json["graph_intent"] = {
                "intent": graph_intent.value,
                "directions": list(directions),
            }
        else:
            directions = ("dependent", "dependency")

        seeds = candidates[:seed_n]
        seed_ids = {block.id for block in seeds}
        original_position = {
            block.id: position
            for position, block in enumerate(candidates)
        }

        moved_ids: set[int] = set()
        handled_ids: set[int] = set()
        graph_actions: list[RetrievedBlock] = []
        augmented: list[RetrievedBlock] = []
        action_count = 0
        neighbors_by_seed = (
            fetch_neighbors(
                repo_id,
                [block.id for block in seeds],
                directions,
            )
            if seeds and max_added > 0 and directions
            else {}
        )

        for position, block in enumerate(candidates):
            if block.id in moved_ids:
                continue

            augmented.append(block)

            if position >= seed_n:
                continue

            insertions_here: list[RetrievedBlock] = []

            by_direction = neighbors_by_seed.get(block.id, {})
            for relationship in directions:
                if action_count >= max_added:
                    break

                for neighbor in by_direction.get(relationship, []):
                    if action_count >= max_added:
                        break

                    if (
                        neighbor.id in handled_ids
                        or neighbor.id in seed_ids
                    ):
                        continue

                    handled_ids.add(neighbor.id)
                    existing_position = original_position.get(
                        neighbor.id
                    )

                    if existing_position is None:
                        new_block = RetrievedBlock(
                            id=neighbor.id,
                            address=neighbor.address,
                            file_path=neighbor.file_path,
                            start_line=neighbor.start_line,
                            end_line=neighbor.end_line,
                            body=neighbor.body,
                            embed_text=neighbor.embed_text,
                            score=None,
                            graph_relationship=relationship,
                            graph_origin_address=block.address,
                            graph_ref_text=neighbor.ref_text,
                            graph_score_status="unscored",
                        )
                    else:
                        original_block = candidates[
                            existing_position
                        ]
                        new_block = dataclasses.replace(
                            original_block,
                            graph_relationship=relationship,
                            graph_origin_address=block.address,
                            graph_ref_text=neighbor.ref_text,
                            graph_score_status="promoted",
                        )
                        moved_ids.add(neighbor.id)

                    insertions_here.append(new_block)
                    graph_actions.append(new_block)
                    action_count += 1

            augmented.extend(insertions_here)

        candidates = augmented
        latency_json["graph_ms"] = (
            time.perf_counter() - graph_start
        ) * 1000
        stages_json["graph"] = _serialize_graph(
            graph_actions
        )

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
