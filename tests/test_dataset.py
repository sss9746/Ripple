import json
from collections import Counter
from pathlib import Path

import pytest

from ripple.evaluation import dataset
from ripple.evaluation.dataset import (
    BenchmarkEntry,
    load_benchmark,
    validate_addresses_exist,
)


def write_benchmark(tmp_path: Path, data: object) -> Path:
    path = tmp_path / "benchmark.json"
    path.write_text(json.dumps(data))
    return path


def test_load_benchmark_accepts_valid_entry(tmp_path: Path) -> None:
    path = write_benchmark(
        tmp_path,
        [
            {
                "id": "q001",
                "question": "What creates the VPC?",
                "expected": ["aws_vpc.main"],
                "category": "lookup",
            }
        ],
    )

    assert load_benchmark(path) == [
        BenchmarkEntry(
            id="q001",
            question="What creates the VPC?",
            expected=["aws_vpc.main"],
            category="lookup",
        )
    ]


def test_load_benchmark_rejects_non_array(tmp_path: Path) -> None:
    path = write_benchmark(
        tmp_path,
        {
            "id": "q001",
            "question": "What creates the VPC?",
            "expected": ["aws_vpc.main"],
            "category": "lookup",
        },
    )

    with pytest.raises(ValueError, match="must be a JSON array"):
        load_benchmark(path)


def test_load_benchmark_rejects_missing_fields(tmp_path: Path) -> None:
    path = write_benchmark(
        tmp_path,
        [
            {
                "id": "q001",
                "question": "What creates the VPC?",
            }
        ],
    )

    with pytest.raises(
        ValueError,
        match="missing fields: category, expected",
    ):
        load_benchmark(path)


def test_load_benchmark_rejects_non_object_entry(tmp_path: Path) -> None:
    path = write_benchmark(tmp_path, ["not an object"])

    with pytest.raises(ValueError, match="must be a JSON object"):
        load_benchmark(path)


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("id", 1),
        ("question", 1),
        ("expected", "aws_vpc.main"),
        ("category", 1),
    ],
)
def test_load_benchmark_rejects_wrong_field_types(
    tmp_path: Path,
    field: str,
    invalid_value: object,
) -> None:
    entry = {
        "id": "q001",
        "question": "What creates the VPC?",
        "expected": ["aws_vpc.main"],
        "category": "lookup",
    }
    entry[field] = invalid_value
    path = write_benchmark(tmp_path, [entry])

    with pytest.raises(ValueError):
        load_benchmark(path)


@pytest.mark.parametrize(
    ("field", "invalid_value", "message"),
    [
        ("id", "", "invalid id"),
        ("id", "question-one", "expected format q001"),
        ("question", "   ", "invalid question"),
        ("expected", [], "invalid expected list"),
        ("expected", ["   "], "non-empty strings"),
        ("category", "security", "invalid category"),
    ],
)
def test_load_benchmark_rejects_invalid_field_values(
    tmp_path: Path,
    field: str,
    invalid_value: object,
    message: str,
) -> None:
    entry = {
        "id": "q001",
        "question": "What creates the VPC?",
        "expected": ["aws_vpc.main"],
        "category": "lookup",
    }
    entry[field] = invalid_value
    path = write_benchmark(tmp_path, [entry])

    with pytest.raises(ValueError, match=message):
        load_benchmark(path)


def test_load_benchmark_rejects_duplicate_ids(tmp_path: Path) -> None:
    entry = {
        "id": "q001",
        "question": "What creates the VPC?",
        "expected": ["aws_vpc.main"],
        "category": "lookup",
    }
    path = write_benchmark(tmp_path, [entry, entry.copy()])

    with pytest.raises(ValueError, match="duplicate id"):
        load_benchmark(path)


def test_load_benchmark_rejects_duplicate_expected_addresses(
    tmp_path: Path,
) -> None:
    path = write_benchmark(
        tmp_path,
        [
            {
                "id": "q001",
                "question": "What creates the VPC?",
                "expected": ["aws_vpc.main", "aws_vpc.main"],
                "category": "lookup",
            }
        ],
    )

    with pytest.raises(ValueError, match="duplicate expected addresses"):
        load_benchmark(path)


def test_validate_addresses_exist_accepts_known_addresses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested_repo_ids: list[int] = []

    def fake_fetch_resource_addresses(repo_id: int) -> list[str]:
        requested_repo_ids.append(repo_id)
        return ["aws_vpc.main", "aws_subnet.public"]

    monkeypatch.setattr(
        dataset.db,
        "fetch_resource_addresses",
        fake_fetch_resource_addresses,
    )
    entries = [
        BenchmarkEntry(
            id="q001",
            question="What creates the VPC?",
            expected=["aws_vpc.main"],
            category="lookup",
        ),
        BenchmarkEntry(
            id="q002",
            question="What depends on the VPC?",
            expected=["aws_subnet.public"],
            category="relational",
        ),
    ]

    assert validate_addresses_exist(entries, repo_id=42) is None
    assert requested_repo_ids == [42]


def test_validate_addresses_exist_reports_every_missing_pair(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        dataset.db,
        "fetch_resource_addresses",
        lambda repo_id: ["aws_vpc.main"],
    )
    entries = [
        BenchmarkEntry(
            id="q001",
            question="What creates the VPC and subnet?",
            expected=["aws_vpc.main", "aws_subnet.missing"],
            category="lookup",
        ),
        BenchmarkEntry(
            id="q002",
            question="What creates the database?",
            expected=["aws_db_instance.missing"],
            category="lookup",
        ),
    ]

    with pytest.raises(ValueError) as exc_info:
        validate_addresses_exist(entries, repo_id=42)

    message = str(exc_info.value)
    assert "repo_id=42" in message
    assert "q001: aws_subnet.missing" in message
    assert "q002: aws_db_instance.missing" in message
    assert "aws_vpc.main" not in message


def test_real_benchmark_has_balanced_category_mix() -> None:
    benchmark_path = Path(__file__).parents[1] / "data" / "benchmark.json"
    entries = load_benchmark(benchmark_path)
    counts = Counter(entry.category for entry in entries)

    assert len(entries) == 40
    assert counts["lookup"] >= 12
    assert counts["relational"] >= 8
    assert counts["blast_radius"] >= 6
    assert counts["attribute"] >= 5
