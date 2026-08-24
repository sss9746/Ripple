from dataclasses import dataclass

from ripple import db


@dataclass
class GraphNeighbor:
    """A Terraform block connected through a reference edge."""

    id: int
    address: str
    file_path: str
    start_line: int
    end_line: int
    body: str
    ref_text: str


def dependents(resource_id: int) -> list[GraphNeighbor]:
    """Return every block that references the given resource."""
    with db.get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    resource.id,
                    resource.address,
                    resource.file_path,
                    resource.start_line,
                    resource.end_line,
                    resource.body,
                    edges.ref_text
                FROM edges
                JOIN resources AS resource
                    ON resource.id = edges.source_id
                WHERE edges.target_id = %s
                ORDER BY resource.address
                """,
                (resource_id,),
            )
            rows = cursor.fetchall()

    return [GraphNeighbor(*row) for row in rows]


def dependencies(resource_id: int) -> list[GraphNeighbor]:
    """Return every block referenced by the given resource."""
    with db.get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    resource.id,
                    resource.address,
                    resource.file_path,
                    resource.start_line,
                    resource.end_line,
                    resource.body,
                    edges.ref_text
                FROM edges
                JOIN resources AS resource
                    ON resource.id = edges.target_id
                WHERE edges.source_id = %s
                ORDER BY resource.address
                """,
                (resource_id,),
            )
            rows = cursor.fetchall()

    return [GraphNeighbor(*row) for row in rows]
