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
    embed_text: str
    ref_text: str


_DIRECTION_RANKS = {
    "dependent": 0,
    "dependency": 1,
}


def fetch_neighbors(
    repo_id: int,
    seed_ids: list[int],
    directions: tuple[str, ...] = ("dependent", "dependency"),
) -> dict[int, dict[str, list[GraphNeighbor]]]:
    """Batch-fetch one-hop neighbors for multiple graph seeds."""
    unique_seed_ids = list(dict.fromkeys(seed_ids))
    unknown_directions = set(directions) - set(_DIRECTION_RANKS)

    if unknown_directions:
        unknown = ", ".join(sorted(unknown_directions))
        raise ValueError(f"Unsupported graph direction(s): {unknown}")

    requested_directions = tuple(
        direction
        for direction in _DIRECTION_RANKS
        if direction in directions
    )
    if not unique_seed_ids or not requested_directions:
        return {}

    branches: list[str] = []
    params: list[object] = []

    if "dependent" in requested_directions:
        branches.append(
            """
            SELECT
                edges.target_id AS origin_id,
                0 AS direction_rank,
                resource.id AS resource_id,
                resource.address,
                resource.file_path,
                resource.start_line,
                resource.end_line,
                resource.body,
                resource.embed_text,
                edges.ref_text,
                edges.id AS edge_id
            FROM edges
            JOIN resources AS resource
                ON resource.id = edges.source_id
            WHERE edges.target_id = ANY(%s::int[])
              AND edges.repo_id = %s
              AND resource.repo_id = %s
            """
        )
        params.extend((unique_seed_ids, repo_id, repo_id))

    if "dependency" in requested_directions:
        branches.append(
            """
            SELECT
                edges.source_id AS origin_id,
                1 AS direction_rank,
                resource.id AS resource_id,
                resource.address,
                resource.file_path,
                resource.start_line,
                resource.end_line,
                resource.body,
                resource.embed_text,
                edges.ref_text,
                edges.id AS edge_id
            FROM edges
            JOIN resources AS resource
                ON resource.id = edges.target_id
            WHERE edges.source_id = ANY(%s::int[])
              AND edges.repo_id = %s
              AND resource.repo_id = %s
            """
        )
        params.extend((unique_seed_ids, repo_id, repo_id))

    query = " UNION ALL ".join(branches)
    query += """
        ORDER BY
            origin_id,
            direction_rank,
            address,
            ref_text,
            resource_id,
            edge_id
    """

    with db.pooled_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(query, tuple(params))
            rows = cursor.fetchall()

    result: dict[int, dict[str, list[GraphNeighbor]]] = {}
    for row in rows:
        (
            origin_id,
            direction_rank,
            resource_id,
            address,
            file_path,
            start_line,
            end_line,
            body,
            embed_text,
            ref_text,
            _edge_id,
        ) = row
        relationship = (
            "dependent" if direction_rank == 0 else "dependency"
        )
        neighbor = GraphNeighbor(
            id=resource_id,
            address=address,
            file_path=file_path,
            start_line=start_line,
            end_line=end_line,
            body=body,
            embed_text=embed_text,
            ref_text=ref_text,
        )
        result.setdefault(origin_id, {}).setdefault(
            relationship,
            [],
        ).append(neighbor)

    return result


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
                    resource.embed_text,
                    edges.ref_text
                FROM edges
                JOIN resources AS resource
                    ON resource.id = edges.source_id
                WHERE edges.target_id = %s
                ORDER BY
                    resource.address,
                    edges.ref_text,
                    resource.id,
                    edges.id
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
                    resource.embed_text,
                    edges.ref_text
                FROM edges
                JOIN resources AS resource
                    ON resource.id = edges.target_id
                WHERE edges.source_id = %s
                ORDER BY
                    resource.address,
                    edges.ref_text,
                    resource.id,
                    edges.id
                """,
                (resource_id,),
            )
            rows = cursor.fetchall()

    return [GraphNeighbor(*row) for row in rows]
