import os
from typing import Protocol

import psycopg
from dotenv import load_dotenv
from pgvector import Vector
from pgvector.psycopg import register_vector


load_dotenv()


def get_connection() -> psycopg.Connection:
    database_url = os.environ.get("DATABASE_URL")

    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set")

    connection = psycopg.connect(database_url)
    register_vector(connection)
    return connection


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
    embedding: list[float]


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
                         embed_text, embedding)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
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
                            Vector(row.embedding),
                        )
                        for row in rows
                    ],
                )


class EdgeRowLike(Protocol):
    source_id: int
    target_id: int
    ref_text: str


def replace_edges(repo_id: int, rows: list[EdgeRowLike]) -> None:
    """Atomically replace all edges belonging to one repository."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "DELETE FROM edges WHERE repo_id = %s",
                (repo_id,),
            )

            if rows:
                cursor.executemany(
                    """
                    INSERT INTO edges
                        (repo_id, source_id, target_id, ref_text)
                    VALUES (%s, %s, %s, %s)
                    """,
                    [
                        (
                            repo_id,
                            row.source_id,
                            row.target_id,
                            row.ref_text,
                        )
                        for row in rows
                    ],
                )


def fetch_resource_bodies(
    repo_id: int,
) -> list[tuple[int, str, str]]:
    """Return each resource's ID, address, and body for one repository."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, address, body
                FROM resources
                WHERE repo_id = %s
                """,
                (repo_id,),
            )
            return cursor.fetchall()
