from dataclasses import dataclass

import psycopg
import pytest

from ripple import db
from ripple.llm.embeddings import EMBEDDING_DIM
from ripple.retrieval.pgvector_store import PgVectorStore
from ripple.retrieval.vector_store import RetrievedBlock


@dataclass
class _ResourceRow:
    block_kind: str
    resource_type: str | None
    resource_name: str | None
    address: str
    file_path: str
    start_line: int
    end_line: int
    body: str
    embed_text: str
    embedding: list[float]


def _vector(first: float = 0.0, second: float = 0.0) -> list[float]:
    return [first, second] + [0.0] * (EMBEDDING_DIM - 2)


def _row(name: str, embedding: list[float]) -> _ResourceRow:
    address = f"aws_instance.{name}"
    return _ResourceRow(
        block_kind="resource",
        resource_type="aws_instance",
        resource_name=name,
        address=address,
        file_path="main.tf",
        start_line=1,
        end_line=3,
        body=f'resource "aws_instance" "{name}" {{}}',
        embed_text=address,
        embedding=embedding,
    )


@pytest.mark.parametrize("k", [0, -1])
def test_query_short_circuits_for_nonpositive_k(
    monkeypatch: pytest.MonkeyPatch,
    k: int,
) -> None:
    def _unexpected_vector(*args: object, **kwargs: object) -> None:
        raise AssertionError("Vector(...) must not be constructed for k <= 0")

    def _unexpected_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("db.get_connection() must not be called for k <= 0")

    monkeypatch.setattr(
        "ripple.retrieval.pgvector_store.Vector",
        _unexpected_vector,
    )
    monkeypatch.setattr(db, "get_connection", _unexpected_connection)

    result = PgVectorStore().query(
        repo_id=1,
        embedding=[0.0] * EMBEDDING_DIM,
        k=k,
    )

    assert result == []


def test_pgvector_store_query_limit_null_filter_and_delete() -> None:
    try:
        repo_id = db.insert_repo(
            name="pytest-pgvector-store",
            source_url=None,
            local_path="/tmp/pytest-pgvector-store",
        )
    except (RuntimeError, psycopg.OperationalError):
        pytest.skip("database not reachable")

    store = PgVectorStore()
    close = _vector(first=1.0)
    middle = _vector(second=1.0)
    far = _vector(first=-1.0)

    try:
        store.upsert(
            repo_id,
            [
                _row("close", close),
                _row("far", far),
            ],
        )

        results = store.query(repo_id, close, k=2)

        assert all(isinstance(result, RetrievedBlock) for result in results)
        assert [result.address for result in results] == [
            "aws_instance.close",
            "aws_instance.far",
        ]
        assert results[0].score == pytest.approx(1.0)
        assert results[1].score == pytest.approx(-1.0)
        assert results[0].embed_text == "aws_instance.close"
        assert results[0].embed_text != results[0].body

        store.upsert(
            repo_id,
            [
                _row("close", close),
                _row("middle", middle),
                _row("far", far),
            ],
        )

        limited_results = store.query(repo_id, close, k=2)

        assert [result.address for result in limited_results] == [
            "aws_instance.close",
            "aws_instance.middle",
        ]

        with db.get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO resources
                        (repo_id, block_kind, resource_type, resource_name,
                         address, file_path, start_line, end_line, body,
                         embed_text)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    (
                        repo_id,
                        "resource",
                        "aws_instance",
                        "without_embedding",
                        "aws_instance.without_embedding",
                        "main.tf",
                        5,
                        7,
                        'resource "aws_instance" "without_embedding" {}',
                        "aws_instance.without_embedding",
                    ),
                )

        all_results = store.query(repo_id, close, k=10)

        assert "aws_instance.without_embedding" not in {
            result.address for result in all_results
        }
        assert len(all_results) == 3

        store.delete_namespace(repo_id)

        assert store.query(repo_id, close, k=10) == []
    finally:
        with db.get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM repos WHERE id = %s", (repo_id,))
