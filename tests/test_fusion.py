import pytest

from ripple.retrieval.fusion import concat_dedup, fuse, rrf
from ripple.retrieval.vector_store import RetrievedBlock


def _block(
    block_id: int,
    address: str,
    score: float,
) -> RetrievedBlock:
    return RetrievedBlock(
        id=block_id,
        address=address,
        file_path="main.tf",
        start_line=1,
        end_line=1,
        body="resource body",
        score=score,
    )


def test_rrf_matches_hand_computed_scores() -> None:
    scores = rrf([[1, 2, 3], [2, 1, 4]], k=60)

    assert scores == pytest.approx(
        {
            1: (1 / 61) + (1 / 62),
            2: (1 / 62) + (1 / 61),
            3: 1 / 63,
            4: 1 / 63,
        }
    )


@pytest.mark.parametrize("ranked_lists", [[], [[], []]])
def test_rrf_returns_empty_scores_for_empty_lists(
    ranked_lists: list[list[int]],
) -> None:
    assert rrf(ranked_lists) == {}


def test_rrf_rejects_negative_k() -> None:
    with pytest.raises(ValueError, match="must be non-negative"):
        rrf([[1]], k=-1)


def test_rrf_accepts_zero_k() -> None:
    assert rrf([[1, 2]], k=0) == pytest.approx({1: 1.0, 2: 0.5})


def test_fuse_orders_blocks_by_rrf_and_replaces_scores() -> None:
    vector_results = [
        _block(1, "aws_vpc.main", 0.95),
        _block(2, "aws_subnet.public", 0.85),
    ]
    bm25_results = [
        _block(2, "aws_subnet.public", 8.0),
        _block(3, "aws_security_group.worker", 7.0),
    ]

    results = fuse([vector_results, bm25_results], k=60)

    assert [block.id for block in results] == [2, 1, 3]
    assert [block.score for block in results] == pytest.approx(
        [
            (1 / 62) + (1 / 61),
            1 / 61,
            1 / 62,
        ]
    )
    assert results[0].score != vector_results[1].score
    assert results[0].score != bm25_results[0].score


def test_fuse_breaks_equal_score_ties_by_address() -> None:
    later_address = _block(1, "z.block", 0.9)
    earlier_address = _block(2, "a.block", 8.0)

    results = fuse([[later_address], [earlier_address]])

    assert [block.address for block in results] == ["a.block", "z.block"]


def test_fuse_propagates_negative_k_error() -> None:
    with pytest.raises(ValueError, match="must be non-negative"):
        fuse([[_block(1, "aws_vpc.main", 0.9)]], k=-1)


def test_concat_dedup_keeps_occurrence_with_best_rank_and_score() -> None:
    vector_results = [
        _block(1, "a.first", 0.9),
        _block(2, "d.second", 0.8),
        _block(3, "b.shared", 0.7),
    ]
    bm25_results = [
        _block(3, "b.shared", 9.0),
        _block(4, "c.only_bm25", 8.0),
    ]

    results = concat_dedup([vector_results, bm25_results])

    assert [block.id for block in results] == [1, 3, 4, 2]
    shared_block = next(block for block in results if block.id == 3)
    assert shared_block.score == 9.0


def test_concat_dedup_preserves_unique_block_score() -> None:
    unique_block = _block(1, "aws_vpc.main", 42.0)

    [result] = concat_dedup([[unique_block]])

    assert result == unique_block
    assert result.score == 42.0


def test_concat_dedup_and_fuse_treat_scores_differently() -> None:
    block = _block(1, "aws_vpc.main", 42.0)

    [concatenated] = concat_dedup([[block]])
    [fused] = fuse([[block]])

    assert concatenated.score == 42.0
    assert fused.score == pytest.approx(1 / 61)
