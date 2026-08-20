from pathlib import Path

import pytest

from scripts import index_repo


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("https://github.com/x/y", True),
        ("git@github.com:x/y.git", True),
        ("ssh://git@host/x/y.git", True),
        ("/tmp/local-repo", False),
    ],
)
def test_is_git_url(source: str, expected: bool) -> None:
    assert index_repo.is_git_url(source) is expected


@pytest.mark.parametrize(
    ("source", "is_url", "expected"),
    [
        (
            "https://github.com/terraform-aws-modules/terraform-aws-vpc.git",
            True,
            "terraform-aws-vpc",
        ),
        ("/tmp/foo/bar", False, "bar"),
    ],
)
def test_derive_name(source: str, is_url: bool, expected: str) -> None:
    assert index_repo.derive_name(source, is_url) == expected


def test_resolve_local_repo(tmp_path: Path) -> None:
    local_repo = tmp_path / "terraform-repo"
    local_repo.mkdir()

    assert index_repo.resolve_repo_source(str(local_repo)) == (
        local_repo.resolve(),
        None,
        "terraform-repo",
    )


def test_resolve_missing_local_repo(tmp_path: Path) -> None:
    missing_repo = tmp_path / "missing"

    with pytest.raises(FileNotFoundError):
        index_repo.resolve_repo_source(str(missing_repo))


def test_resolve_git_url_clones_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "https://github.com/example/terraform-repo.git"
    clone_calls: list[tuple[str, Path]] = []

    def record_clone(clone_source: str, target: Path) -> None:
        clone_calls.append((clone_source, target))

    monkeypatch.setattr(index_repo.git.Repo, "clone_from", record_clone)

    assert index_repo.resolve_repo_source(source, repos_dir=tmp_path) == (
        tmp_path / "terraform-repo",
        source,
        "terraform-repo",
    )
    assert clone_calls == [(source, tmp_path / "terraform-repo")]


def test_resolve_git_url_rejects_existing_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (tmp_path / "terraform-repo").mkdir()

    def unexpected_clone(*args: object) -> None:
        raise AssertionError(f"clone should not be called: {args}")

    monkeypatch.setattr(index_repo.git.Repo, "clone_from", unexpected_clone)

    with pytest.raises(FileExistsError, match="different --name"):
        index_repo.resolve_repo_source(
            "https://github.com/example/terraform-repo.git",
            repos_dir=tmp_path,
        )


def test_explicit_name_avoids_clone_target_collision(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = "https://github.com/example/terraform-repo.git"
    (tmp_path / "terraform-repo").mkdir()
    clone_calls: list[tuple[str, Path]] = []

    def record_clone(clone_source: str, target: Path) -> None:
        clone_calls.append((clone_source, target))

    monkeypatch.setattr(index_repo.git.Repo, "clone_from", record_clone)

    assert index_repo.resolve_repo_source(
        source,
        name="custom-repo",
        repos_dir=tmp_path,
    ) == (tmp_path / "custom-repo", source, "custom-repo")
    assert clone_calls == [(source, tmp_path / "custom-repo")]


def test_main_registers_local_repo(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    insert_calls: list[dict[str, str | None]] = []

    def record_insert(
        name: str,
        source_url: str | None,
        local_path: str,
    ) -> int:
        insert_calls.append(
            {
                "name": name,
                "source_url": source_url,
                "local_path": local_path,
            }
        )
        return 42

    monkeypatch.setattr(index_repo.db, "insert_repo", record_insert)

    index_repo.main([str(tmp_path)])

    assert insert_calls == [
        {
            "name": tmp_path.name,
            "source_url": None,
            "local_path": str(tmp_path),
        }
    ]
    assert capsys.readouterr().out == (
        f"Registered repo id=42 name={tmp_path.name} local_path={tmp_path}\n"
    )


def test_main_uses_explicit_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    insert_calls: list[dict[str, str | None]] = []

    def record_insert(
        name: str,
        source_url: str | None,
        local_path: str,
    ) -> int:
        insert_calls.append(
            {
                "name": name,
                "source_url": source_url,
                "local_path": local_path,
            }
        )
        return 43

    monkeypatch.setattr(index_repo.db, "insert_repo", record_insert)

    index_repo.main([str(tmp_path), "--name", "custom"])

    assert insert_calls == [
        {
            "name": "custom",
            "source_url": None,
            "local_path": str(tmp_path),
        }
    ]
