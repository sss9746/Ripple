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
    embedding: list[float]


@dataclass
class _EdgeRow:
    source_id: int
    target_id: int
    ref_text: str


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
        embedding=[0.0] * 1536,
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
        embedding=[0.0] * 1536,
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
        embedding=[0.0] * 1536,
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


def test_replace_edges_rolls_back_on_insert_failure() -> None:
    try:
        connection = db.get_connection()
    except (RuntimeError, psycopg.OperationalError):
        pytest.skip("database not reachable")
    else:
        connection.close()

    try:
        repo_id = db.insert_repo(
            name="pytest-atomic-edge-replace",
            source_url=None,
            local_path="/tmp/pytest-atomic-edge-replace",
        )
    except psycopg.OperationalError:
        pytest.skip("database not reachable")

    def resource_row(name: str) -> _ResourceRow:
        return _ResourceRow(
            block_kind="resource",
            resource_type="aws_instance",
            resource_name=name,
            address=f"aws_instance.{name}",
            file_path="main.tf",
            start_line=1,
            end_line=3,
            body=f'resource "aws_instance" "{name}" {{}}',
            embed_text=f"aws_instance.{name}",
            embedding=[0.0] * 1536,
        )

    try:
        db.replace_resources(
            repo_id,
            [
                resource_row("source"),
                resource_row("target"),
                resource_row("doomed"),
            ],
        )

        resource_ids = {
            address: resource_id
            for resource_id, address, _body in db.fetch_resource_bodies(repo_id)
        }
        source_id = resource_ids["aws_instance.source"]
        target_id = resource_ids["aws_instance.target"]
        doomed_id = resource_ids["aws_instance.doomed"]

        original_edge = _EdgeRow(
            source_id=source_id,
            target_id=target_id,
            ref_text="aws_instance.target.id",
        )
        db.replace_edges(repo_id, [original_edge])

        with db.get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM resources WHERE id = %s", (doomed_id,))

        invalid_edge = _EdgeRow(
            source_id=source_id,
            target_id=doomed_id,
            ref_text="aws_instance.doomed.id",
        )
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            db.replace_edges(repo_id, [invalid_edge])

        with db.get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT source_id, target_id, ref_text
                    FROM edges
                    WHERE repo_id = %s
                    """,
                    (repo_id,),
                )
                assert cursor.fetchall() == [
                    (
                        source_id,
                        target_id,
                        "aws_instance.target.id",
                    )
                ]
    finally:
        with db.get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM repos WHERE id = %s", (repo_id,))


def test_insert_query_log_round_trip() -> None:
    try:
        repo_id = db.insert_repo(
            name="pytest-day6-query-log",
            source_url=None,
            local_path="/tmp/pytest-day6-query-log",
        )
    except (RuntimeError, psycopg.OperationalError):
        pytest.skip("database not reachable")

    config_json = {
        "requested": {
            "use_vector": True,
            "use_bm25": True,
            "use_rrf": True,
        },
        "executed": {
            "vector": True,
            "bm25": True,
            "fusion": True,
            "fusion_method": "rrf",
        },
    }
    stages_json = {
        "vector": [
            {"id": 1, "address": "aws_vpc.main", "score": 0.95}
        ],
        "bm25": [
            {"id": 1, "address": "aws_vpc.main", "score": 8.5}
        ],
        "fusion": [
            {"id": 1, "address": "aws_vpc.main", "score": 0.032}
        ],
        "final": [
            {"id": 1, "address": "aws_vpc.main", "score": 0.032}
        ],
    }
    latency_json = {
        "vector_query_ms": 12.5,
        "bm25_ms": 3.25,
        "fusion_ms": 0.5,
        "total_ms": 16.25,
    }

    try:
        answered_log_id = db.insert_query_log(
            repo_id=repo_id,
            question="What creates the VPC?",
            config_json=config_json,
            stages_json=stages_json,
            latency_json=latency_json,
            answer="aws_vpc.main creates the VPC.",
        )
        unanswered_log_id = db.insert_query_log(
            repo_id=repo_id,
            question="Unknown question",
            config_json=config_json,
            stages_json={"final": []},
            latency_json={"total_ms": 1.0},
            answer=None,
        )

        with db.get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT id, question, config_json, stages_json,
                           latency_json, answer
                    FROM query_logs
                    WHERE repo_id = %s
                    ORDER BY id
                    """,
                    (repo_id,),
                )
                rows = cursor.fetchall()

        assert rows == [
            (
                answered_log_id,
                "What creates the VPC?",
                config_json,
                stages_json,
                latency_json,
                "aws_vpc.main creates the VPC.",
            ),
            (
                unanswered_log_id,
                "Unknown question",
                config_json,
                {"final": []},
                {"total_ms": 1.0},
                None,
            ),
        ]
    finally:
        with db.get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM repos WHERE id = %s", (repo_id,))


def test_fetch_resource_addresses_is_scoped_to_repo() -> None:
    try:
        connection = db.get_connection()
    except (RuntimeError, psycopg.OperationalError):
        pytest.skip("database not reachable")
    else:
        connection.close()

    repo_ids: list[int] = []

    def resource_row(address: str) -> _ResourceRow:
        resource_type, resource_name = address.split(".", maxsplit=1)
        return _ResourceRow(
            block_kind="resource",
            resource_type=resource_type,
            resource_name=resource_name,
            address=address,
            file_path="main.tf",
            start_line=1,
            end_line=3,
            body=f'resource "{resource_type}" "{resource_name}" {{}}',
            embed_text=address,
            embedding=[0.0] * 1536,
        )

    try:
        requested_repo_id = db.insert_repo(
            name="pytest-fetch-addresses-requested",
            source_url=None,
            local_path="/tmp/pytest-fetch-addresses-requested",
        )
        repo_ids.append(requested_repo_id)

        other_repo_id = db.insert_repo(
            name="pytest-fetch-addresses-other",
            source_url=None,
            local_path="/tmp/pytest-fetch-addresses-other",
        )
        repo_ids.append(other_repo_id)

        db.replace_resources(
            requested_repo_id,
            [
                resource_row("aws_vpc.zeta"),
                resource_row("aws_subnet.alpha"),
            ],
        )
        db.replace_resources(
            other_repo_id,
            [resource_row("aws_instance.other")],
        )

        assert db.fetch_resource_addresses(requested_repo_id) == [
            "aws_subnet.alpha",
            "aws_vpc.zeta",
        ]
    finally:
        if repo_ids:
            with db.get_connection() as connection:
                with connection.cursor() as cursor:
                    for repo_id in repo_ids:
                        cursor.execute(
                            "DELETE FROM repos WHERE id = %s",
                            (repo_id,),
                        )
