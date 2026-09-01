from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

from ripple import db
from ripple.ingest import indexer
from ripple.retrieval import graph


REFERENCE_FIXTURE_ROOT = (
    Path(__file__).parent / "fixtures" / "reference_repo"
).resolve()


class _FakeEmbeddingProvider:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 1536 for _ in texts]


class _RecordingCursor:
    def __init__(self, queries: list[str]) -> None:
        self.queries = queries

    def __enter__(self) -> "_RecordingCursor":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def execute(
        self,
        query: str,
        params: tuple[object, ...],
    ) -> None:
        self.queries.append(query)

    def fetchall(self) -> list[tuple]:
        return []


class _RecordingConnection:
    def __init__(self, queries: list[str]) -> None:
        self.queries = queries

    def __enter__(self) -> "_RecordingConnection":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def cursor(self) -> _RecordingCursor:
        return _RecordingCursor(self.queries)


@pytest.fixture(scope="module")
def resource_ids() -> Iterator[dict[str, int]]:
    try:
        connection = db.get_connection()
    except (RuntimeError, psycopg.OperationalError):
        pytest.skip("database not reachable")
    else:
        connection.close()

    try:
        repo_id = db.insert_repo(
            name="pytest-day4-graph",
            source_url=None,
            local_path=str(REFERENCE_FIXTURE_ROOT),
        )
    except psycopg.OperationalError:
        pytest.skip("database not reachable")

    try:
        assert indexer.index_repo(
            repo_id,
            str(REFERENCE_FIXTURE_ROOT),
            embedder=_FakeEmbeddingProvider(),
        ) == 7
        assert indexer.index_edges(repo_id) == 5

        yield {
            address: resource_id
            for resource_id, address, _body in db.fetch_resource_bodies(repo_id)
        }
    finally:
        with db.get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM repos WHERE id = %s", (repo_id,))


def test_dependencies_returns_referenced_block(
    resource_ids: dict[str, int],
) -> None:
    subnet_id = resource_ids["aws_subnet.public"]

    neighbors = graph.dependencies(subnet_id)

    assert [neighbor.address for neighbor in neighbors] == ["aws_vpc.main"]
    assert neighbors[0].ref_text == "aws_vpc.main.id"
    assert neighbors[0].file_path == "main.tf"
    assert neighbors[0].body.startswith('resource "aws_vpc" "main"')
    assert neighbors[0].embed_text.startswith(
        "aws_vpc.main\nFile: main.tf\nType: aws_vpc"
    )
    assert neighbors[0].embed_text != neighbors[0].body


def test_dependents_returns_referencing_blocks_in_stable_order(
    resource_ids: dict[str, int],
) -> None:
    vpc_id = resource_ids["aws_vpc.main"]

    first_result = graph.dependents(vpc_id)
    second_result = graph.dependents(vpc_id)

    assert [neighbor.address for neighbor in first_result] == [
        "aws_security_group.worker",
        "aws_subnet.public",
    ]
    assert [neighbor.ref_text for neighbor in first_result] == [
        "aws_vpc.main.id",
        "aws_vpc.main.id",
    ]
    assert second_result == first_result


def test_graph_queries_order_duplicate_edges_by_reference_text(
    resource_ids: dict[str, int],
) -> None:
    subnet_id = resource_ids["aws_subnet.public"]
    vpc_id = resource_ids["aws_vpc.main"]
    inserted_edge_ids: list[int] = []

    try:
        with db.get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT repo_id FROM resources WHERE id = %s",
                    (vpc_id,),
                )
                repo_id = cursor.fetchone()[0]

                for ref_text in (
                    "aws_vpc.main.alpha",
                    "aws_vpc.main.zeta",
                ):
                    cursor.execute(
                        """
                        INSERT INTO edges
                            (repo_id, source_id, target_id, ref_text)
                        VALUES (%s, %s, %s, %s)
                        RETURNING id
                        """,
                        (repo_id, subnet_id, vpc_id, ref_text),
                    )
                    inserted_edge_ids.append(cursor.fetchone()[0])

        expected_ref_texts = [
            "aws_vpc.main.alpha",
            "aws_vpc.main.id",
            "aws_vpc.main.zeta",
        ]

        for _attempt in range(2):
            dependent_refs = [
                neighbor.ref_text
                for neighbor in graph.dependents(vpc_id)
                if neighbor.id == subnet_id
            ]
            dependency_refs = [
                neighbor.ref_text
                for neighbor in graph.dependencies(subnet_id)
                if neighbor.id == vpc_id
            ]

            assert dependent_refs == expected_ref_texts
            assert dependency_refs == expected_ref_texts
    finally:
        if inserted_edge_ids:
            with db.get_connection() as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "DELETE FROM edges WHERE id = ANY(%s::int[])",
                        (inserted_edge_ids,),
                    )


def test_dependencies_returns_empty_list_when_block_has_none(
    resource_ids: dict[str, int],
) -> None:
    ami_id = resource_ids["data.aws_ami.ubuntu"]

    assert graph.dependencies(ami_id) == []


def test_graph_queries_use_total_deterministic_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[str] = []

    monkeypatch.setattr(
        graph.db,
        "get_connection",
        lambda: _RecordingConnection(queries),
    )

    assert graph.dependents(1) == []
    assert graph.dependencies(1) == []

    normalized_queries = [
        " ".join(query.split())
        for query in queries
    ]
    expected_order = (
        "ORDER BY resource.address, edges.ref_text, "
        "resource.id, edges.id"
    )

    assert len(normalized_queries) == 2
    assert all(
        expected_order in query
        for query in normalized_queries
    )


def test_fetch_neighbors_matches_legacy_helpers(
    resource_ids: dict[str, int],
) -> None:
    vpc_id = resource_ids["aws_vpc.main"]
    subnet_id = resource_ids["aws_subnet.public"]

    with db.get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT repo_id FROM resources WHERE id = %s",
                (vpc_id,),
            )
            repo_id = cursor.fetchone()[0]

    result = graph.fetch_neighbors(repo_id, [vpc_id, subnet_id])

    assert result[vpc_id]["dependent"] == graph.dependents(vpc_id)
    assert result.get(vpc_id, {}).get("dependency", []) == (
        graph.dependencies(vpc_id)
    )
    assert result.get(subnet_id, {}).get("dependent", []) == (
        graph.dependents(subnet_id)
    )
    assert result[subnet_id]["dependency"] == graph.dependencies(subnet_id)


@pytest.mark.parametrize(
    ("seed_ids", "directions"),
    [([], ("dependent", "dependency")), ([1], ())],
)
def test_fetch_neighbors_empty_inputs_skip_database(
    monkeypatch: pytest.MonkeyPatch,
    seed_ids: list[int],
    directions: tuple[str, ...],
) -> None:
    monkeypatch.setattr(
        graph.db,
        "pooled_connection",
        lambda: pytest.fail("database should not be called"),
    )

    assert graph.fetch_neighbors(3, seed_ids, directions) == {}


def test_fetch_neighbors_uses_one_deterministic_union_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[str] = []
    monkeypatch.setattr(
        graph.db,
        "pooled_connection",
        lambda: _RecordingConnection(queries),
    )

    assert graph.fetch_neighbors(3, [2, 1, 2]) == {}

    assert len(queries) == 1
    normalized_query = " ".join(queries[0].split())
    assert normalized_query.count("UNION ALL") == 1
    assert normalized_query.count("= ANY(%s::int[])") == 2
    assert normalized_query.count("edges.repo_id = %s") == 2
    assert normalized_query.count("resource.repo_id = %s") == 2
    assert "edges.id AS edge_id" in normalized_query
    assert (
        "ORDER BY origin_id, direction_rank, address, ref_text, "
        "resource_id, edge_id"
    ) in normalized_query


def test_fetch_neighbors_rejects_unknown_direction() -> None:
    with pytest.raises(ValueError, match="Unsupported graph direction"):
        graph.fetch_neighbors(3, [1], ("sideways",))
