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


def test_dependencies_returns_empty_list_when_block_has_none(
    resource_ids: dict[str, int],
) -> None:
    ami_id = resource_ids["data.aws_ami.ubuntu"]

    assert graph.dependencies(ami_id) == []
