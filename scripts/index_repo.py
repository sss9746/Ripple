import argparse
import re
import sys
from pathlib import Path

import git


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ripple import db

REPOS_DIR = PROJECT_ROOT / ".repos"


_GIT_URL_RE = re.compile(r"^(https?://|git@|ssh://)")


def is_git_url(source: str) -> bool:
    """Return whether source looks like a remote Git URL."""
    return bool(_GIT_URL_RE.match(source)) or source.endswith(".git")


def derive_name(source: str, is_url: bool) -> str:
    """Derive a repository name from a local path or Git URL."""
    name = source.rstrip("/").rsplit("/", 1)[-1]
    if is_url and name.endswith(".git"):
        name = name[: -len(".git")]
    return name


def resolve_repo_source(
    source: str,
    name: str | None = None,
    repos_dir: Path = REPOS_DIR,
) -> tuple[Path, str | None, str]:
    """Resolve a local path or clone a Git URL into the repository cache."""
    source_is_url = is_git_url(source)
    resolved_name = name or derive_name(source, source_is_url)

    if source_is_url:
        target = repos_dir / resolved_name
        if target.exists():
            raise FileExistsError(
                f"Clone target {target} already exists; remove it or choose a "
                "different --name"
            )

        target.parent.mkdir(parents=True, exist_ok=True)
        git.Repo.clone_from(source, target)
        return target, source, resolved_name

    local_path = Path(source).expanduser().resolve()
    if not local_path.is_dir():
        raise FileNotFoundError(
            f"Local path does not exist or is not a directory: {local_path}"
        )

    return local_path, None, resolved_name


def main(argv: list[str] | None = None) -> None:
    """Resolve and register a Terraform repository from command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Register a Terraform repo for indexing"
    )
    parser.add_argument("source", help="Local directory path or Git URL")
    parser.add_argument(
        "--name",
        help="Override the derived repo name (and clone directory, for Git URLs)",
    )
    args = parser.parse_args(argv)

    local_path, source_url, name = resolve_repo_source(
        args.source,
        name=args.name,
    )
    repo_id = db.insert_repo(
        name=name,
        source_url=source_url,
        local_path=str(local_path),
    )
    print(f"Registered repo id={repo_id} name={name} local_path={local_path}")


if __name__ == "__main__":
    main()
