import dataclasses
from collections import defaultdict

from ripple.retrieval.vector_store import RetrievedBlock


def rrf(
    ranked_lists: list[list[int]],
    k: int = 60,
) -> dict[int, float]:
    if k < 0:
        raise ValueError(f"rrf's k must be non-negative, got {k}")

    scores: dict[int, float] = defaultdict(float)

    for ranked_list in ranked_lists:
        for rank, document_id in enumerate(ranked_list, start=1):
            scores[document_id] += 1.0 / (k + rank)

    return dict(scores)


def fuse(
    ranked_lists: list[list[RetrievedBlock]],
    k: int = 60,
) -> list[RetrievedBlock]:
    id_lists = [
        [block.id for block in ranked_list]
        for ranked_list in ranked_lists
    ]
    scores = rrf(id_lists, k=k)

    blocks_by_id: dict[int, RetrievedBlock] = {}

    for ranked_list in ranked_lists:
        for block in ranked_list:
            blocks_by_id.setdefault(block.id, block)

    ranked_ids = sorted(
        scores,
        key=lambda document_id: (
            -scores[document_id],
            blocks_by_id[document_id].address,
        ),
    )

    return [
        dataclasses.replace(
            blocks_by_id[document_id],
            score=scores[document_id],
        )
        for document_id in ranked_ids
    ]


def concat_dedup(
    ranked_lists: list[list[RetrievedBlock]],
) -> list[RetrievedBlock]:
    best_ranks: dict[int, int] = {}
    blocks_by_id: dict[int, RetrievedBlock] = {}

    for ranked_list in ranked_lists:
        for rank, block in enumerate(ranked_list, start=1):
            if block.id not in best_ranks or rank < best_ranks[block.id]:
                best_ranks[block.id] = rank
                blocks_by_id[block.id] = block

    ranked_ids = sorted(
        best_ranks,
        key=lambda document_id: (
            best_ranks[document_id],
            blocks_by_id[document_id].address,
        ),
    )

    return [
        blocks_by_id[document_id]
        for document_id in ranked_ids
    ]