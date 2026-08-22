from pgvector import Vector

from ripple import db
from ripple.retrieval.vector_store import RetrievedBlock


class PgVectorStore:
    """Default vector store backed by PostgreSQL and pgvector."""

    def upsert(self, repo_id: int, rows) -> None:
        db.replace_resources(repo_id, rows)

    def delete_namespace(self, repo_id: int) -> None:
        db.replace_resources(repo_id, [])

    def query(
        self,
        repo_id: int,
        embedding: list[float],
        k: int,
    ) -> list[RetrievedBlock]:
        vector_param = Vector(embedding)

        with db.get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, address, file_path, start_line, end_line, body,
                           1 - (embedding <=> %s) AS score
                    FROM resources
                    WHERE repo_id = %s
                      AND embedding IS NOT NULL
                    ORDER BY embedding <=> %s
                    LIMIT %s
                    """,
                    (vector_param, repo_id, vector_param, k),
                )
                rows = cursor.fetchall()

        return [
            RetrievedBlock(
                id=row[0],
                address=row[1],
                file_path=row[2],
                start_line=row[3],
                end_line=row[4],
                body=row[5],
                score=row[6],
            )
            for row in rows
        ]
