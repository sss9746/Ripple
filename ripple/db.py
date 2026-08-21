import os
from typing import Protocol

import psycopg
from dotenv import load_dotenv


load_dotenv()


def get_connection() -> psycopg.Connection:
    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    return psycopg.connect(database_url)


def insert_repo(name: str, source_url: str | None, local_path: str) -> int:
    """Insert a repository row and return its generated ID."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO repos (name, source_url, local_path)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (name, source_url, local_path),
            )
            repo_id = cursor.fetchone()[0]
        connection.commit()

    return repo_id


class ResourceRowLike(Protocol):
    block_kind: str
    resource_type: str | None
    resource_name: str | None
    address: str
    file_path: str
    start_line: int
    end_line: int
    body: str
    embed_text: str


def replace_resources(repo_id: int, rows: list[ResourceRowLike]) -> None:
    """Atomically replace all resource rows belonging to one repository."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM resources WHERE repo_id = %s", (repo_id,))

            if rows:
                cursor.executemany(
                    """
                    INSERT INTO resources
                        (repo_id, block_kind, resource_type, resource_name,
                         address, file_path, start_line, end_line, body,
                         embed_text)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            repo_id,
                            row.block_kind,
                            row.resource_type,
                            row.resource_name,
                            row.address,
                            row.file_path,
                            row.start_line,
                            row.end_line,
                            row.body,
                            row.embed_text,
                        )
                        for row in rows
                    ],
                )
