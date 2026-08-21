from dataclasses import dataclass

import psycopg
import pytest

from ripple import db


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


def test_get_connection_requires_database_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)

    with pytest.raises(
        RuntimeError,
        match="DATABASE_URL environment variable is not set",
    ):
        db.get_connection()


def test_insert_repo_round_trip() -> None:
    try:
        repo_id = db.insert_repo(
            name="pytest-day1-repo",
            source_url="https://example.com/pytest-day1-repo.git",
            local_path="/tmp/pytest-day1-repo",
        )
    except (RuntimeError, psycopg.OperationalError):
        pytest.skip("database not reachable")

    assert isinstance(repo_id, int)

    with db.get_connection() as connection:
        with connection.cursor() as cursor:
            try:
                cursor.execute(
                    """
                    SELECT name, source_url, local_path
                    FROM repos
                    WHERE id = %s
                    """,
                    (repo_id,),
                )
                assert cursor.fetchone() == (
                    "pytest-day1-repo",
                    "https://example.com/pytest-day1-repo.git",
                    "/tmp/pytest-day1-repo",
                )
            finally:
                cursor.execute("DELETE FROM repos WHERE id = %s", (repo_id,))
        connection.commit()


def test_replace_resources_rolls_back_on_insert_failure() -> None:
    try:
        connection = db.get_connection()
    except (RuntimeError, psycopg.OperationalError):
        pytest.skip("database not reachable")
    else:
        connection.close()

    try:
        repo_id = db.insert_repo(
            name="pytest-atomic-replace",
            source_url=None,
            local_path="/tmp/pytest-atomic-replace",
        )
    except psycopg.OperationalError:
        pytest.skip("database not reachable")

    row_a = _ResourceRow(
        block_kind="resource",
        resource_type="aws_instance",
        resource_name="a",
        address="aws_instance.a",
        file_path="main.tf",
        start_line=1,
        end_line=3,
        body='resource "aws_instance" "a" {}',
        embed_text="aws_instance.a",
    )
    row_b = _ResourceRow(
        block_kind="resource",
        resource_type="aws_instance",
        resource_name="b",
        address="aws_instance.b",
        file_path="main.tf",
        start_line=5,
        end_line=7,
        body='resource "aws_instance" "b" {}',
        embed_text="aws_instance.b",
    )
    duplicate_row = _ResourceRow(
        block_kind="resource",
        resource_type="aws_instance",
        resource_name="duplicate",
        address="aws_instance.duplicate",
        file_path="main.tf",
        start_line=9,
        end_line=11,
        body='resource "aws_instance" "duplicate" {}',
        embed_text="aws_instance.duplicate",
    )

    try:
        db.replace_resources(repo_id, [row_a, row_b])

        with pytest.raises(psycopg.errors.UniqueViolation):
            db.replace_resources(repo_id, [duplicate_row, duplicate_row])

        with db.get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT address
                    FROM resources
                    WHERE repo_id = %s
                    ORDER BY address
                    """,
                    (repo_id,),
                )
                assert cursor.fetchall() == [
                    ("aws_instance.a",),
                    ("aws_instance.b",),
                ]
    finally:
        with db.get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM repos WHERE id = %s", (repo_id,))
