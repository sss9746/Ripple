from pathlib import Path

import psycopg
import pytest

from ripple import db
from ripple.ingest import indexer, parser, scanner
from ripple.ingest.indexer import (
    MAX_EMBED_BODY_CHARS,
    build_embed_text,
    index_repo,
)
from ripple.ingest.parser import ParsedBlock


FIXTURE_ROOT = (Path(__file__).parent / "fixtures" / "sample_repo").resolve()


class _FakeEmbeddingProvider:
    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * 1536 for _ in texts]


def _block(
    *,
    block_kind: str = "resource",
    resource_type: str | None = "aws_security_group",
    resource_name: str | None = "worker",
    address: str = "aws_security_group.worker",
    body: str = 'resource "aws_security_group" "worker" {}',
) -> ParsedBlock:
    return ParsedBlock(
        block_kind=block_kind,
        resource_type=resource_type,
        resource_name=resource_name,
        address=address,
        file_path="main.tf",
        start_line=1,
        end_line=1,
        body=body,
    )


def test_build_embed_text_uses_resource_type() -> None:
    result = build_embed_text(_block())

    assert result.splitlines()[0] == "aws_security_group.worker"
    assert result.splitlines()[1] == "File: main.tf"
    assert result.splitlines()[2] == "Type: aws_security_group"


def test_build_embed_text_falls_back_to_block_kind() -> None:
    result = build_embed_text(
        _block(
            block_kind="locals",
            resource_type=None,
            resource_name=None,
            address="locals:main.tf:1",
            body="locals {}",
        )
    )

    assert result.splitlines()[2] == "Type: locals"


def test_build_embed_text_truncates_only_search_body(
    capsys: pytest.CaptureFixture[str],
) -> None:
    original_body = "x" * (MAX_EMBED_BODY_CHARS + 1)
    block = _block(body=original_body)

    result = build_embed_text(block)
    header = (
        "aws_security_group.worker\n"
        "File: main.tf\n"
        "Type: aws_security_group\n\n"
    )

    assert result == header + ("x" * MAX_EMBED_BODY_CHARS)
    assert block.body == original_body
    assert capsys.readouterr().out == (
        "WARNING: truncating embed_text body for aws_security_group.worker "
        f"({MAX_EMBED_BODY_CHARS + 1} -> {MAX_EMBED_BODY_CHARS} chars)\n"
    )


def test_index_repo_round_trip_and_reindex() -> None:
    expected_blocks = {
        block.address: block
        for file_path in scanner.find_tf_files(FIXTURE_ROOT)
        for block in parser.parse_file(file_path, FIXTURE_ROOT)
    }

    try:
        connection = db.get_connection()
    except (RuntimeError, psycopg.OperationalError):
        pytest.skip("database not reachable")
    else:
        connection.close()

    try:
        repo_id = db.insert_repo(
            name="pytest-day2-indexer",
            source_url=None,
            local_path=str(FIXTURE_ROOT),
        )
    except psycopg.OperationalError:
        pytest.skip("database not reachable")

    try:
        resource_count = index_repo(
            repo_id,
            str(FIXTURE_ROOT),
            embedder=_FakeEmbeddingProvider(),
        )

        assert resource_count == len(expected_blocks) == 6

        with db.get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    """
                    SELECT address, block_kind, resource_type, resource_name,
                           file_path, start_line, end_line, body, embed_text,
                           embedding
                    FROM resources
                    WHERE repo_id = %s
                    ORDER BY address
                    """,
                    (repo_id,),
                )
                saved_rows = cursor.fetchall()

        assert len(saved_rows) == 6

        for saved_row in saved_rows:
            (
                address,
                block_kind,
                resource_type,
                resource_name,
                file_path,
                start_line,
                end_line,
                body,
                embed_text,
                embedding,
            ) = saved_row
            expected = expected_blocks[address]

            assert block_kind == expected.block_kind
            assert resource_type == expected.resource_type
            assert resource_name == expected.resource_name
            assert file_path == expected.file_path
            assert start_line == expected.start_line
            assert end_line == expected.end_line
            assert body == expected.body
            assert embed_text
            assert embedding.to_list() == [0.0] * 1536

        second_count = index_repo(
            repo_id,
            str(FIXTURE_ROOT),
            embedder=_FakeEmbeddingProvider(),
        )
        assert second_count == 6

        with db.get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT count(*) FROM resources WHERE repo_id = %s",
                    (repo_id,),
                )
                assert cursor.fetchone()[0] == 6
    finally:
        with db.get_connection() as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM repos WHERE id = %s", (repo_id,))


def test_index_repo_empty_repository_never_requires_embedder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    calls: list[tuple[int, list[object]]] = []

    def record_replace(repo_id: int, rows: list[object]) -> None:
        calls.append((repo_id, rows))

    monkeypatch.setattr(indexer.db, "replace_resources", record_replace)

    result = indexer.index_repo(123, str(tmp_path))

    assert result == 0
    assert calls == [(123, [])]
