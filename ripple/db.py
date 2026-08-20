import os

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
