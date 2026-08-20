import psycopg
import pytest

from ripple import db


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
