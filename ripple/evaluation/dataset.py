import json
import re
from dataclasses import dataclass
from pathlib import Path

from ripple import db


VALID_CATEGORIES = {
    "lookup",
    "relational",
    "blast_radius",
    "attribute",
}


@dataclass
class BenchmarkEntry:
    """One labeled retrieval question from the benchmark.

    Blast-radius questions include the subject block and all direct dependents in
    ``expected``, following SPEC.md's q002 example. Relational questions include
    only the subject's direct dependencies, not the subject itself.
    """

    id: str
    question: str
    expected: list[str]
    category: str


def load_benchmark(path: Path) -> list[BenchmarkEntry]:
    data = json.loads(path.read_text())

    if not isinstance(data, list):
        raise ValueError("benchmark must be a JSON array")

    entries: list[BenchmarkEntry] = []
    seen_ids: set[str] = set()

    for index, item in enumerate(data):
        if not isinstance(item, dict):
            raise ValueError(
                f"benchmark entry at index {index} must be a JSON object"
            )

        required_fields = {"id", "question", "expected", "category"}
        missing_fields = required_fields - item.keys()

        if missing_fields:
            missing = ", ".join(sorted(missing_fields))
            raise ValueError(
                f"benchmark entry at index {index} is missing fields: {missing}"
            )

        entry_id = item["id"]
        question = item["question"]
        expected = item["expected"]
        category = item["category"]

        location = f"benchmark entry at index {index}"

        if not isinstance(entry_id, str) or not entry_id:
            raise ValueError(f"{location} has an invalid id")

        if re.fullmatch(r"q\d{3}", entry_id) is None:
            raise ValueError(
                f"{location} has id {entry_id!r}; expected format q001"
            )

        location = f"benchmark entry {entry_id!r} at index {index}"

        if entry_id in seen_ids:
            raise ValueError(f"{location} has a duplicate id")

        if not isinstance(question, str) or not question.strip():
            raise ValueError(f"{location} has an invalid question")

        if not isinstance(expected, list) or not expected:
            raise ValueError(f"{location} has an invalid expected list")

        if not all(
            isinstance(address, str) and address.strip()
            for address in expected
        ):
            raise ValueError(
                f"{location} expected must contain non-empty strings"
            )

        if len(expected) != len(set(expected)):
            raise ValueError(
                f"{location} contains duplicate expected addresses"
            )

        if not isinstance(category, str) or category not in VALID_CATEGORIES:
            raise ValueError(
                f"{location} has invalid category {category!r}"
            )

        seen_ids.add(entry_id)
        entries.append(
            BenchmarkEntry(
                id=entry_id,
                question=question,
                expected=expected,
                category=category,
            )
        )

    return entries


def validate_addresses_exist(
    entries: list[BenchmarkEntry],
    repo_id: int,
) -> None:
    """Raise if any expected benchmark address is absent from the repository."""
    existing_addresses = set(db.fetch_resource_addresses(repo_id))
    missing = [
        (entry.id, address)
        for entry in entries
        for address in entry.expected
        if address not in existing_addresses
    ]

    if missing:
        details = ", ".join(
            f"{entry_id}: {address}"
            for entry_id, address in missing
        )
        raise ValueError(
            f"benchmark contains addresses missing from repo_id={repo_id}: "
            f"{details}"
        )
