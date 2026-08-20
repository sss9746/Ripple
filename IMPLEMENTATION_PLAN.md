# Implementation Plan — Day 1 Foundation: config, db connection, repo registration

## 1. Objective

Stand up the project foundation that every later stage (parsing, retrieval, evaluation,
API) depends on: the `RetrievalConfig` ablation-switch dataclass, a Postgres connection
helper, and a CLI script that resolves a Terraform repo source (local path or git URL)
and registers it as a row in the `repos` table.

This is SPEC.md's Day 1 milestone. `docker-compose.yml`, `sql/schema.sql`,
`.env.example`, `requirements.txt`, and `.gitignore` already satisfy the rest of Day 1's
checklist — only `config.py`, `db.py`, and `scripts/index_repo.py` are missing.

## 2. Relevant SPEC.md requirements

- Section 8 (Repository layout): `ripple/config.py` — `RetrievalConfig` dataclass, the
  ablation switches. `ripple/db.py` — connection pool / query helpers.
  `scripts/index_repo.py` — the indexing entry point.
- Section 9.11: exact `RetrievalConfig` field list and defaults (reproduced below).
- Section 11, Day 1: "`docker-compose.yml`... `sql/schema.sql`... `config.py`, `db.py`
  with a connection helper... `scripts/index_repo.py` skeleton that clones or resolves a
  local path and inserts a `repos` row. **Done when:** `docker compose up` yields a
  database with all four tables, and the script registers a repo."
- Section 3, constraint 2: every retrieval stage must be independently toggleable via a
  config object, designed from day one — this is why `RetrievalConfig` is built in full
  now rather than incrementally.
- Section 3, constraint 5: no API keys in the repo; environment variables only.
- Section 7: `repos` table schema — `id`, `name`, `source_url`, `local_path`,
  `indexed_at`.

## 3. Current implementation gaps

- `ripple/config.py` does not exist. No `RetrievalConfig` dataclass anywhere.
- `ripple/db.py` does not exist. Nothing reads `DATABASE_URL` or opens a Postgres
  connection.
- `scripts/index_repo.py` does not exist (the `scripts/` directory is empty).
- `tests/` is empty — no coverage exists yet.
- `.gitignore` has no entry for a local clone cache directory (needed once
  `index_repo.py` can clone git URLs).

## 4. Exact files Codex should create or modify

Create:
- `ripple/config.py`
- `ripple/db.py`
- `scripts/index_repo.py`
- `tests/test_config.py`
- `tests/test_db.py`
- `tests/test_index_repo.py`

Modify:
- `.gitignore` — add the local clone cache directory used by `index_repo.py`.

Do not modify: `sql/schema.sql`, `docker-compose.yml`, `.env.example`,
`requirements.txt`, `AGENTS.md`, `CLAUDE.md`, `README.md`.

## 5. Step-by-step implementation instructions

### 5.1 `ripple/config.py`

Reproduce the `RetrievalConfig` dataclass exactly as specified in section 9.11 — field
names, types, and defaults must match verbatim, since later days (and the ablation
study) depend on these exact names:

```python
from dataclasses import dataclass
from typing import Literal


@dataclass
class RetrievalConfig:
    vector_backend: Literal["pgvector", "pinecone"] = "pgvector"
    use_vector: bool = True
    use_bm25: bool = True
    use_rrf: bool = True
    use_rerank: bool = True
    use_graph: bool = True
    use_rewrite: bool = True
    vector_k: int = 30
    bm25_k: int = 30
    rrf_k: int = 60
    rerank_top_n: int = 50
    final_k: int = 8
    graph_seed_n: int = 3
    graph_max_added: int = 10
```

No other logic belongs in this file for Day 1. Do not add validation, presets, or a
second config class — those are not called for by the spec at this stage.

### 5.2 `ripple/db.py`

Responsibilities: load `DATABASE_URL` from the environment (via `python-dotenv`,
already a dependency) and provide a connection helper plus one query helper for
inserting a `repos` row. Do not add a pooling library (`psycopg_pool` is not in
`requirements.txt`); a plain `psycopg.connect()` per call satisfies Day 1's "connection
helper" requirement (section 11, Day 1 — the module docstring in section 8 says "pool",
but the Day 1 checklist only asks for a helper; a real pool is not required until
performance demands it in a later day).

```python
import os

import psycopg
from dotenv import load_dotenv

load_dotenv()


def get_connection() -> psycopg.Connection:
    """Open a new connection to DATABASE_URL. Raises RuntimeError if unset."""
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL environment variable is not set")
    return psycopg.connect(database_url)


def insert_repo(name: str, source_url: str | None, local_path: str) -> int:
    """Insert a row into repos and return its id."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO repos (name, source_url, local_path)
                VALUES (%s, %s, %s)
                RETURNING id
                """,
                (name, source_url, local_path),
            )
            repo_id = cur.fetchone()[0]
        conn.commit()
    return repo_id
```

`indexed_at` is intentionally left `NULL` — it gets set once real indexing (parsing +
embedding, Day 2/3) completes. Do not set it in this script.

Note for the implementer: `load_dotenv()` at import time means `os.environ.get` picks
up `.env` automatically; do not hardcode a default `DATABASE_URL` — an unset variable
must raise, not silently fall back, so misconfiguration is caught immediately rather
than connecting to nothing.

### 5.3 `scripts/index_repo.py`

Split into a pure, testable resolution function and a thin `main()`. This keeps the
git-clone and DB-insert side effects out of the unit-testable logic.

```python
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
    return bool(_GIT_URL_RE.match(source)) or source.endswith(".git")


def derive_name(source: str, is_url: bool) -> str:
    stem = source.rstrip("/").rsplit("/", 1)[-1]
    if is_url and stem.endswith(".git"):
        stem = stem[: -len(".git")]
    return stem


def resolve_repo_source(
    source: str, name: str | None = None, repos_dir: Path = REPOS_DIR
) -> tuple[Path, str | None, str]:
    """Resolve `source` to (local_path, source_url, resolved_name).

    `name`, if given, overrides the name derived from `source` — for a git URL this
    also changes the clone target directory (`repos_dir/<name>`), so passing a
    different `--name` is a real way to avoid a clone-target collision, not just a
    cosmetic rename.

    If `source` looks like a git URL, clone it into `repos_dir/<resolved_name>`.
    Otherwise treat it as a local path and validate it exists and is a directory.
    """
    is_url = is_git_url(source)
    resolved_name = name or derive_name(source, is_url)

    if is_url:
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
        raise FileNotFoundError(f"Local path does not exist or is not a directory: {local_path}")
    return local_path, None, resolved_name


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Register a Terraform repo for indexing")
    parser.add_argument("source", help="Local directory path or git URL")
    parser.add_argument("--name", help="Override the derived repo name (and clone directory, for git URLs)")
    args = parser.parse_args(argv)

    local_path, source_url, name = resolve_repo_source(args.source, name=args.name)

    repo_id = db.insert_repo(name=name, source_url=source_url, local_path=str(local_path))
    print(f"Registered repo id={repo_id} name={name} local_path={local_path}")


if __name__ == "__main__":
    main()
```

Implementation notes:
- **Import path**: this script lives in `scripts/`, a sibling of the `ripple/` package,
  and the project has no `pyproject.toml`/`setup.py` (no editable install exists to put
  `ripple` on `sys.path`). Invoking it the documented way, `python
  scripts/index_repo.py <source>`, sets `sys.path[0]` to `scripts/`, not the project
  root, so a bare `from ripple import db` fails with `ModuleNotFoundError` regardless of
  the caller's cwd. The snippet above inserts `PROJECT_ROOT` (computed from `__file__`,
  so it is correct no matter what cwd the script is invoked from) at the front of
  `sys.path` *before* importing `ripple`. This keeps the documented invocation working
  with no packaging changes and no reliance on `PYTHONPATH`. Do not drop this shim or
  reorder the import above it.
- `git.Repo.clone_from` comes from `GitPython` (already in `requirements.txt`).
- This script only registers the repo row. It must not attempt to parse `.tf` files,
  compute embeddings, or write `resources`/`edges` rows — that is Day 2 and Day 3 work.
- Re-running the script for the same source always inserts a new `repos` row (no
  dedup/upsert logic). This is acceptable for a Day 1 skeleton — see Non-goals.

### 5.4 `.gitignore`

Add:

```
.repos/
```

This is the local clone cache directory used by `resolve_repo_source`. Never commit
cloned Terraform repos.

## 6. Interfaces, data structures, and error behavior

- `RetrievalConfig` — dataclass, all fields keyword-settable, defaults exactly as in
  section 9.11. No custom `__post_init__` validation.
- `db.get_connection() -> psycopg.Connection` — raises `RuntimeError` if `DATABASE_URL`
  is unset or empty. Never returns `None`.
- `db.insert_repo(name: str, source_url: str | None, local_path: str) -> int` — returns
  the new `repos.id`. Propagates any `psycopg` exception (e.g. connection refused) to
  the caller unchanged; do not swallow or wrap it.
- `index_repo.is_git_url(source: str) -> bool` — pure, no I/O.
- `index_repo.derive_name(source: str, is_url: bool) -> str` — pure, no I/O.
- `index_repo.resolve_repo_source(source, name=None, repos_dir=REPOS_DIR) -> tuple[Path, str | None, str]`
  — `name`, when given, overrides the derived name and (for a git URL) the clone target
  directory (`repos_dir/<name>`). Raises `FileNotFoundError` if a local path does not
  exist or is not a directory; raises `FileExistsError` if the clone target directory
  already exists (the message tells the caller to remove it or pass a different
  `--name`, which is now actually true since `name` changes the target); propagates
  `git.exc.GitCommandError` on clone failure unchanged.
- `index_repo.main(argv=None) -> None` — parses args, calls
  `resolve_repo_source(args.source, name=args.name)`, inserts the repo row using the
  name `resolve_repo_source` returns, prints one confirmation line to stdout in the
  exact format `Registered repo id={id} name={name} local_path={path}`. On any exception
  raised by `resolve_repo_source` or `db.insert_repo`, let it propagate (argparse/Python
  will print the traceback and exit non-zero) — no bespoke error swallowing in this
  skeleton.
- **Import path**: `scripts/index_repo.py` inserts the project root onto `sys.path`
  before `from ripple import db` (see 5.3) so the documented invocation
  (`python scripts/index_repo.py <source>`) works from any cwd without a packaging
  step. `tests/test_index_repo.py` does not need this shim itself — running the test
  suite via `python -m pytest` from the project root already puts the project root on
  `sys.path`, making both `ripple` and `scripts` (as an implicit namespace package, no
  `__init__.py` required) importable directly.

## 7. Required tests

`tests/test_config.py`:
- Instantiate `RetrievalConfig()` with no args and assert every field equals its
  section-9.11 default (one assertion per field, or a single dict comparison via
  `dataclasses.asdict`).

`tests/test_db.py`:
- `get_connection` raises `RuntimeError` when `DATABASE_URL` is unset (use
  `monkeypatch.delenv("DATABASE_URL", raising=False)`).
- Integration test for `insert_repo`: attempt a real connection using `DATABASE_URL`
  (from `.env`/environment); if connection fails, `pytest.skip("database not
  reachable")` rather than failing — do not fabricate a pass. When reachable, insert a
  repo, assert the returned id is an `int`, then read the row back and assert `name`,
  `source_url`, `local_path` match, and clean up the inserted row afterward (`DELETE
  FROM repos WHERE id = %s`).

`tests/test_index_repo.py`:
- `is_git_url`: table-test `https://github.com/x/y`, `git@github.com:x/y.git`,
  `ssh://git@host/x/y.git`, and a plain local path — assert True/True/True/False.
- `derive_name`: `https://github.com/terraform-aws-modules/terraform-aws-vpc.git` →
  `terraform-aws-vpc`; a local path `/tmp/foo/bar` → `bar`.
- `resolve_repo_source` with a local `tmp_path` directory fixture: returns
  `(resolved_path, None, dirname)`.
- `resolve_repo_source` with a nonexistent local path: raises `FileNotFoundError`.
- `resolve_repo_source` with a git URL: monkeypatch `git.Repo.clone_from` to a stub
  that records its call args instead of hitting the network; assert it's called with
  `(source, repos_dir / name)` and the function returns `(repos_dir / name, source,
  name)`.
- `resolve_repo_source` with a git URL whose target directory already exists
  (`tmp_path`-created): raises `FileExistsError`, and `git.Repo.clone_from` is not
  called (monkeypatch it to raise `AssertionError` if invoked).
- `resolve_repo_source` with a git URL, an existing target directory for the derived
  name, but a *different* explicit `name=` passed in: monkeypatch `git.Repo.clone_from`
  to a recording stub; assert it clones into `repos_dir / <the passed name>` (not the
  colliding derived-name directory) and succeeds — this is the regression test proving
  the `FileExistsError` message's "pass a different --name" advice actually works.
- `main()`: monkeypatch `db.insert_repo` to a stub returning a fixed id and record its
  call args; monkeypatch `resolve_repo_source` (or use a real local `tmp_path`) so no
  network/DB I/O occurs; run `main([str(tmp_path)])`; assert the stub was called with
  the expected `name`/`source_url`/`local_path` and that the printed line matches the
  documented format (use `capsys`).
- `main()` with `--name`: run `main([str(tmp_path), "--name", "custom"])` and assert
  `db.insert_repo` was called with `name="custom"` — confirming `main()` uses the name
  `resolve_repo_source` returns rather than re-deriving it.

Run `python -m pytest` after implementation; all tests must pass (DB-dependent test
skips cleanly if Postgres isn't running).

## 8. Acceptance criteria

- `docker compose up` still yields a database with all four tables (unchanged from
  today — verify no regression).
- `python scripts/index_repo.py <local-dir>` registers a new row in `repos` and prints
  `Registered repo id=<id> name=<name> local_path=<path>`.
- `python scripts/index_repo.py <git-url>` clones into `.repos/<name>` and registers a
  row with `source_url` set to the given URL.
- `RetrievalConfig()` field names/defaults match section 9.11 exactly.
- `python -m pytest` passes with no failures (DB-dependent tests skip gracefully if
  Postgres is not running locally).
- No secrets are written to any file; `.env` remains untouched and ignored.

## 9. Explicit non-goals

- HCL parsing, `resources`/`edges` population (Day 2 and Day 4).
- Embedding generation, vector search (Day 3).
- BM25, RRF, reranking, graph expansion, query rewriting (Days 5, 6, 9, 12, 13, 15).
- FastAPI app / `api/main.py` (Day 17).
- Benchmark dataset, metrics, eval runner (Days 8–14).
- A real connection pool (`psycopg_pool` or similar) — a plain per-call connection is
  sufficient at this stage; revisit only if performance requires it.
- Idempotent repo registration (dedup by `local_path`/`source_url`) — every invocation
  inserts a new `repos` row; no unique constraint exists on those columns in the schema,
  and the spec does not ask for one.
- Setting `repos.indexed_at` — that belongs to the day the indexer actually finishes
  writing resources/edges.

## 10. Risks or ambiguities

- **Clone cache location**: SPEC.md's repository layout (section 8) does not specify
  where cloned repos should live. This plan introduces `.repos/` at the project root
  (gitignored) as a reasonable default; the user may prefer a different location (e.g.
  under `data/`), which would just mean changing `REPOS_DIR` in `scripts/index_repo.py`
  and the `.gitignore` entry.
- **"Connection pool" vs "connection helper" wording mismatch**: section 8's module
  docstring says db.py provides a "connection pool," while section 11 Day 1 says
  "connection helper." This plan takes the narrower Day 1 reading and defers real
  pooling; flag to the user if they expect pooling now (it would mean adding
  `psycopg_pool` to `requirements.txt`, a dependency change AGENTS.md requires
  explaining).
- **`is_git_url` heuristic**: scp-style SSH URLs without an explicit `ssh://` prefix
  (`git@host:path.git`) are matched via the `git@` prefix check, not the `.git` suffix
  alone, so a local directory literally named `something.git` would be misclassified as
  a remote URL. Acceptable at this corpus size (SPEC targets one known GitHub repo) but
  worth flagging.
- **No re-index / upsert semantics yet**: running `index_repo.py` twice for the same
  repo creates two independent `repos` rows with separate `id`s. This matches the "no
  incremental indexing" non-goal in SPEC section 2, but could surprise a user expecting
  idempotent re-runs.
