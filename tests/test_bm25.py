from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import psycopg
import pytest
from rank_bm25 import BM25Okapi

from ripple import db
from ripple.ingest import indexer
from ripple.retrieval.bm25 import (
    BM25Document,
    BM25Index,
    build_index,
    tokenize,
)


REFERENCE_FIXTURE_ROOT = (
    Path(__file__).parent / "fixtures" / "reference_repo"
).resolve()
SAMPLE_FIXTURE_ROOT = (
    Path(__file__).parent / "fixtures" / "sample_repo"
).resolve()


class _FakeEmbeddingProvider:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 1536 for _ in texts]


@dataclass(frozen=True)
class _BM25TestRepositories:
    reference_repo_id: int
    sample_repo_id: int
    empty_repo_id: int
    reference_resource_ids: frozenset[int]
    sample_resource_ids: frozenset[int]
    reference_ids_by_address: dict[str, int]
    sample_ids_by_address: dict[str, int]


@pytest.fixture(scope="module")
def bm25_test_repositories() -> Iterator[_BM25TestRepositories]:
    try:
        connection = db.get_connection()
    except (RuntimeError, psycopg.OperationalError):
        pytest.skip("database not reachable")
    else:
        connection.close()

    repo_ids: list[int] = []

    try:
        reference_repo_id = db.insert_repo(
            name="pytest-day5-bm25-reference",
            source_url=None,
            local_path=str(REFERENCE_FIXTURE_ROOT),
        )
        repo_ids.append(reference_repo_id)

        sample_repo_id = db.insert_repo(
            name="pytest-day5-bm25-sample",
            source_url=None,
            local_path=str(SAMPLE_FIXTURE_ROOT),
        )
        repo_ids.append(sample_repo_id)

        empty_repo_id = db.insert_repo(
            name="pytest-day5-bm25-empty",
            source_url=None,
            local_path=str(SAMPLE_FIXTURE_ROOT),
        )
        repo_ids.append(empty_repo_id)

        assert indexer.index_repo(
            reference_repo_id,
            str(REFERENCE_FIXTURE_ROOT),
            embedder=_FakeEmbeddingProvider(),
        ) == 7
        assert indexer.index_repo(
            sample_repo_id,
            str(SAMPLE_FIXTURE_ROOT),
            embedder=_FakeEmbeddingProvider(),
        ) == 6

        reference_rows = db.fetch_bm25_documents(reference_repo_id)
        sample_rows = db.fetch_bm25_documents(sample_repo_id)

        yield _BM25TestRepositories(
            reference_repo_id=reference_repo_id,
            sample_repo_id=sample_repo_id,
            empty_repo_id=empty_repo_id,
            reference_resource_ids=frozenset(row[0] for row in reference_rows),
            sample_resource_ids=frozenset(row[0] for row in sample_rows),
            reference_ids_by_address={row[1]: row[0] for row in reference_rows},
            sample_ids_by_address={row[1]: row[0] for row in sample_rows},
        )
    except psycopg.OperationalError:
        pytest.skip("database not reachable")
    finally:
        if repo_ids:
            with db.get_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.executemany(
                        "DELETE FROM repos WHERE id = %s",
                        [(repo_id,) for repo_id in repo_ids],
                    )


def _build_test_index(
    entries: list[tuple[str, str]],
) -> BM25Index:
    tokenized_corpus = [
        tokenize(embed_text)
        for _address, embed_text in entries
    ]
    documents = [
        BM25Document(
            id=index,
            address=address,
            file_path="main.tf",
            start_line=1,
            end_line=1,
            body=f"body for {address}",
            embed_text=embed_text,
            tokens=frozenset(tokenized_corpus[index]),
        )
        for index, (address, embed_text) in enumerate(entries)
    ]
    return BM25Index(documents, BM25Okapi(tokenized_corpus))


def test_tokenize_emits_full_terraform_address_and_parts() -> None:
    assert tokenize("aws_security_group.worker") == [
        "aws_security_group.worker",
        "aws",
        "security",
        "group",
        "worker",
    ]


def test_tokenize_normalizes_case() -> None:
    assert tokenize("AWS_Security_Group.Worker") == tokenize(
        "aws_security_group.worker"
    )


def test_tokenize_splits_hyphenated_token() -> None:
    assert tokenize("t3-micro") == ["t3-micro", "t3", "micro"]


def test_tokenize_filters_one_character_parts() -> None:
    assert tokenize("a.b") == ["a.b"]


def test_tokenize_ignores_empty_parts_from_consecutive_delimiters() -> None:
    assert tokenize("aws..vpc") == ["aws..vpc", "aws", "vpc"]


def test_tokenize_treats_terraform_syntax_as_separators() -> None:
    assert tokenize('name = "worker-sg"') == [
        "name",
        "worker-sg",
        "worker",
        "sg",
    ]


def test_tokenize_does_not_duplicate_delimiter_free_word() -> None:
    assert tokenize("worker") == ["worker"]


def test_tokenize_finds_full_address_and_parts_in_embed_text() -> None:
    embed_text = "aws_vpc.main\nFile: main.tf\nType: aws_vpc"

    tokens = tokenize(embed_text)

    assert "aws_vpc.main" in tokens
    assert "main" in tokens
    assert "main.tf" in tokens
    assert "aws_vpc" in tokens


def test_tokenize_empty_text_returns_empty_list() -> None:
    assert tokenize("") == []


def test_query_ranks_exact_address_first() -> None:
    index = _build_test_index(
        [
            ("aws_vpc.main", "aws_vpc.main creates the main vpc"),
            ("aws_subnet.public", "aws_subnet.public uses a vpc"),
            (
                "aws_security_group.worker",
                "aws_security_group.worker controls traffic",
            ),
        ]
    )

    results = index.query("aws_vpc.main", k=3)

    assert results[0].address == "aws_vpc.main"


@pytest.mark.parametrize("k", [0, -1])
def test_query_returns_empty_list_for_nonpositive_k(k: int) -> None:
    index = _build_test_index([("aws_vpc.main", "aws_vpc.main")])

    assert index.query("aws_vpc.main", k=k) == []


def test_query_returns_empty_list_for_empty_token_query() -> None:
    index = _build_test_index([("aws_vpc.main", "aws_vpc.main")])

    assert index.query("???", k=5) == []


def test_query_returns_empty_list_when_no_document_has_token_overlap() -> None:
    index = _build_test_index([("aws_vpc.main", "aws_vpc.main")])

    assert index.query("zzz_nonexistent_zzz", k=5) == []


def test_query_returns_empty_list_for_empty_index() -> None:
    index = BM25Index(documents=[], model=None)

    assert index.query("aws_vpc.main", k=5) == []


def test_query_order_is_deterministic() -> None:
    index = _build_test_index(
        [
            ("example.c", "common"),
            ("example.a", "common"),
            ("example.b", "common"),
        ]
    )

    first_result = index.query("common", k=3)
    second_result = index.query("common", k=3)

    assert first_result == second_result
    assert [result.address for result in first_result] == [
        "example.a",
        "example.b",
        "example.c",
    ]


def test_query_returns_overlapping_documents_with_nonpositive_scores() -> None:
    index = _build_test_index(
        [
            ("addr.0", "common alpha"),
            ("addr.1", "common beta"),
            ("addr.2", "common gamma"),
        ]
    )

    results = index.query("common", k=10)

    assert {result.address for result in results} == {
        "addr.0",
        "addr.1",
        "addr.2",
    }
    assert all(result.score <= 0 for result in results)


def test_query_truncates_to_k_without_padding_nonmatching_documents() -> None:
    index = _build_test_index(
        [
            ("addr.0", "common alpha"),
            ("addr.1", "common beta"),
            ("addr.2", "common gamma"),
        ]
    )

    assert len(index.query("common", k=2)) == 2

    alpha_results = index.query("alpha", k=10)
    assert [result.address for result in alpha_results] == ["addr.0"]


def test_build_index_retrieves_exact_address_from_database(
    bm25_test_repositories: _BM25TestRepositories,
) -> None:
    bm25_index = build_index(bm25_test_repositories.reference_repo_id)
    expected_rows = db.fetch_bm25_documents(
        bm25_test_repositories.reference_repo_id
    )
    expected_embed_text_by_address = {
        row[1]: row[6]
        for row in expected_rows
    }

    results = bm25_index.query("aws_vpc.main", k=5)

    assert results[0].address == "aws_vpc.main"
    assert results[0].id == (
        bm25_test_repositories.reference_ids_by_address["aws_vpc.main"]
    )
    assert results[0].embed_text == (
        expected_embed_text_by_address["aws_vpc.main"]
    )
    assert results[0].embed_text != results[0].body


def test_build_index_keeps_repository_results_isolated(
    bm25_test_repositories: _BM25TestRepositories,
) -> None:
    reference_index = build_index(
        bm25_test_repositories.reference_repo_id
    )
    sample_index = build_index(bm25_test_repositories.sample_repo_id)

    reference_results = reference_index.query("aws", k=50)
    sample_results = sample_index.query("aws", k=50)
    reference_result_ids = {result.id for result in reference_results}
    sample_result_ids = {result.id for result in sample_results}

    assert reference_result_ids <= (
        bm25_test_repositories.reference_resource_ids
    )
    assert reference_result_ids.isdisjoint(
        bm25_test_repositories.sample_resource_ids
    )
    assert sample_result_ids <= bm25_test_repositories.sample_resource_ids
    assert sample_result_ids.isdisjoint(
        bm25_test_repositories.reference_resource_ids
    )

    reference_worker = reference_index.query(
        "aws_security_group.worker",
        k=1,
    )[0]
    sample_worker = sample_index.query(
        "aws_security_group.worker",
        k=1,
    )[0]

    assert reference_worker.id == (
        bm25_test_repositories.reference_ids_by_address[
            "aws_security_group.worker"
        ]
    )
    assert sample_worker.id == (
        bm25_test_repositories.sample_ids_by_address[
            "aws_security_group.worker"
        ]
    )
    assert reference_worker.id != sample_worker.id


def test_build_index_handles_database_repo_without_resources(
    bm25_test_repositories: _BM25TestRepositories,
) -> None:
    empty_index = build_index(bm25_test_repositories.empty_repo_id)

    assert empty_index.query("anything", k=5) == []
