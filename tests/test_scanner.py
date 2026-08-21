from pathlib import Path

from ripple.ingest.scanner import find_tf_files


FIXTURE_ROOT = (Path(__file__).parent / "fixtures" / "sample_repo").resolve()


def test_find_tf_files_applies_ignore_list() -> None:
    assert find_tf_files(FIXTURE_ROOT) == [
        FIXTURE_ROOT / "main.tf",
        FIXTURE_ROOT / "variables.tf",
    ]


def test_find_tf_files_returns_empty_list_for_empty_directory(
    tmp_path: Path,
) -> None:
    assert find_tf_files(tmp_path) == []
