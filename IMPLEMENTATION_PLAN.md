# Implementation Plan — Day 2: HCL Parsing

## 1. Objective

Parse Terraform `.tf` files into structurally-bounded `resources` rows with verified,
byte-exact line ranges: a file scanner that applies the SPEC ignore list, a two-pass HCL
parser (structural validation via `python-hcl2` + positional brace-matching for line
numbers), and an indexer that writes the parsed blocks into the `resources` table. Wire
this into `scripts/index_repo.py` so registering a repo also indexes it.

This is SPEC.md's Day 2 milestone, sitting directly on top of the Day 1 foundation
(`ripple/config.py`, `ripple/db.py`, `scripts/index_repo.py`) already implemented and
verified.

## 2. Relevant SPEC.md requirements

- Section 9.1: `python-hcl2` returns no line numbers — this is called out as "the single
  biggest gotcha in the project." Required approach: a structural pass (`hcl2.load()`)
  to validate well-formed HCL, plus a positional pass that regex-matches block headers
  and brace-matches forward to find each block's closing brace, explicitly skipping
  braces inside double-quoted strings, `#`/`//` comments, and heredocs (`<<EOF ... EOF`).
  The exact header regex to use:
  ```python
  BLOCK_RE = re.compile(
      r'^(resource|data|module|variable|output|locals)\s*'
      r'(?:"([^"]+)"\s*)?(?:"([^"]+)"\s*)?\{',
      re.MULTILINE,
  )
  ```
  Section 9.1 requires a test asserting that for every extracted block, the source
  file's lines `start_line` through `end_line` begin with the block header and end with
  a closing brace.
- Section 5: ignore `.git/`, `.terraform/`, `*.tfstate`, `*.tfstate.backup`,
  `.terraform.lock.hcl`. Index `examples/` before the module root, since the module
  root's bodies are full of `var.`/`count`/`for_each` and don't have concrete
  references yet (that matters starting Day 4, but the corpus choice is made now).
- Section 7: `resources` schema — `block_kind`, `resource_type` (nullable),
  `resource_name` (nullable), `address` (`NOT NULL`), `file_path`, `start_line`,
  `end_line`, `body`, `embed_text` (`NOT NULL`), `embedding` (nullable —
  `vector(1536)`), with a `UNIQUE (repo_id, address)` index.
- Section 9.3: what gets embedded — a header (address, file path, block type) prepended
  to the body, body truncated over ~6000 characters with truncation logged. `embed_text`
  is what actually gets stored/embedded; Day 2 must produce this text even though the
  embedding vector itself is Day 3's job (`embedding` stays `NULL` until then).
- Section 9.2 (reference extraction — for scoping only, not implemented this cycle):
  confirms `data.aws_ami.ubuntu` and `aws_ami.ubuntu` are different addresses, and that
  the `data.` prefix belongs in the address for `data` blocks. This plan reuses that
  addressing convention for `resources` rows now, since Day 4's edge resolution depends
  on addresses being right already.
- Section 11, Day 2: "scanner.py... parser.py... Insert resources rows with correct
  address, file_path, start_line, end_line, body... Test: for every row, body equals the
  source file's lines start_line..end_line. **Done when:** the VPC examples directory
  parses to correct blocks and that test passes."
- Section 8 (repository layout): `ripple/ingest/scanner.py`, `ripple/ingest/parser.py`,
  `ripple/ingest/indexer.py` (indexer also does embedding/edges later — Day 2 only needs
  the parse-and-insert-resources slice of it).

## 3. Current implementation gaps

- `ripple/ingest/` package does not exist at all — no scanner, parser, or indexer.
- `ripple/db.py` only has `get_connection` and `insert_repo`; nothing writes to
  `resources`.
- `scripts/index_repo.py` registers a `repos` row and stops — it never parses or indexes
  anything (Day 1 explicitly left this as a "skeleton").
- No fixture Terraform files exist under `tests/` to exercise the parser offline.
- `.repos/terraform-aws-vpc` (cloned during Day 1 verification) is available locally as
  the real corpus for manual acceptance checking, but nothing indexes it yet.

## 4. Exact files Codex should create or modify

Create:
- `ripple/ingest/__init__.py`
- `ripple/ingest/scanner.py`
- `ripple/ingest/parser.py`
- `ripple/ingest/indexer.py`
- `tests/fixtures/sample_repo/main.tf`
- `tests/fixtures/sample_repo/variables.tf`
- `tests/fixtures/sample_repo/.terraform/should_be_ignored.tf`
- `tests/fixtures/sample_repo/terraform.tfstate`
- `tests/test_scanner.py`
- `tests/test_parser.py`
- `tests/test_indexer.py`

Modify:
- `ripple/db.py` — add `replace_resources` (atomic delete + bulk insert).
- `scripts/index_repo.py` — call the indexer after registering the repo.
- `tests/test_index_repo.py` — the `main()` tests must monkeypatch
  `index_repo.indexer.index_repo` and update their exact-output assertions, since
  `main()` now prints a second line (see 5.5 and 7).
- `tests/test_db.py` — add an atomicity test for `replace_resources` (see 7).

Do not modify: `sql/schema.sql`, `docker-compose.yml`, `.env.example`,
`requirements.txt`, `ripple/config.py`, `AGENTS.md`, `CLAUDE.md`, `README.md`,
`tests/test_config.py`. `tests/test_index_repo.py` and `tests/test_db.py` are
explicitly in scope this cycle (see above) — everything else from Day 1 stays as is.

## 5. Step-by-step implementation instructions

### 5.1 `ripple/ingest/scanner.py`

```python
import fnmatch
from pathlib import Path

IGNORED_DIR_NAMES = {".git", ".terraform"}
IGNORED_FILE_PATTERNS = ("*.tfstate", "*.tfstate.backup", ".terraform.lock.hcl")


def find_tf_files(root: Path) -> list[Path]:
    """Return every .tf file under root, applying the SPEC section 5 ignore list."""
    root = Path(root)
    results = []
    for path in sorted(root.rglob("*.tf")):
        rel_parts = path.relative_to(root).parts
        if any(part in IGNORED_DIR_NAMES for part in rel_parts[:-1]):
            continue
        if any(fnmatch.fnmatch(path.name, pattern) for pattern in IGNORED_FILE_PATTERNS):
            continue
        results.append(path)
    return results
```

`*.tf` globbing already excludes `*.tfstate`/`.terraform.lock.hcl` by extension; the
`IGNORED_FILE_PATTERNS` check is kept anyway so the ignore list from section 5 is
explicit and self-documenting rather than an accidental side effect of the glob suffix.

### 5.2 `ripple/ingest/parser.py`

This is the file the "Done when" criterion actually hinges on. Two passes per section
9.1: `hcl2.load()` first, purely to validate the file is well-formed HCL (raise
`ValueError` and abort that file's parse if it isn't — do not silently skip malformed
files; surfacing a bad file loudly is more useful than a silently incomplete index at
this stage). Then a regex + brace-matching positional pass drives every field that goes
into the database, including line numbers.

```python
import re
from dataclasses import dataclass
from pathlib import Path

import hcl2

BLOCK_RE = re.compile(
    r'^(resource|data|module|variable|output|locals)\s*'
    r'(?:"([^"]+)"\s*)?(?:"([^"]+)"\s*)?\{',
    re.MULTILINE,
)

HEREDOC_START_RE = re.compile(r'<<-?(?P<marker>[A-Za-z_][A-Za-z0-9_]*)')


@dataclass
class ParsedBlock:
    block_kind: str
    resource_type: str | None
    resource_name: str | None
    address: str
    file_path: str
    start_line: int
    end_line: int
    body: str


def _find_block_end(text: str, open_brace_index: int) -> int:
    """Return the index of the '}' that closes the '{' at open_brace_index.

    Skips braces inside '#'/'//' line comments, '/* */' block comments,
    double-quoted strings (respecting backslash escapes), and heredocs.
    """
    depth = 0
    i = open_brace_index
    n = len(text)
    while i < n:
        ch = text[i]

        if ch == "#" or text[i : i + 2] == "//":
            newline = text.find("\n", i)
            i = newline if newline != -1 else n
            continue

        if text[i : i + 2] == "/*":
            end = text.find("*/", i + 2)
            i = end + 2 if end != -1 else n
            continue

        if ch == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue

        heredoc_match = HEREDOC_START_RE.match(text, i)
        if heredoc_match:
            marker = heredoc_match.group("marker")
            terminator_re = re.compile(rf"^[ \t]*{re.escape(marker)}\s*$", re.MULTILINE)
            terminator = terminator_re.search(text, heredoc_match.end())
            i = terminator.end() if terminator else n
            continue

        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return i

        i += 1

    raise ValueError(f"Unbalanced braces starting at index {open_brace_index}")


def _address_for(
    kind: str,
    resource_type: str | None,
    resource_name: str | None,
    file_path: str,
    start_line: int,
) -> str:
    if kind == "resource":
        return f"{resource_type}.{resource_name}"
    if kind == "data":
        return f"data.{resource_type}.{resource_name}"
    if kind == "module":
        return f"module.{resource_name}"
    if kind == "variable":
        return f"var.{resource_name}"
    if kind == "output":
        return f"output.{resource_name}"
    # locals blocks have no header label at all; disambiguate by position so
    # multiple `locals {}` blocks (rare but legal HCL) never collide.
    return f"locals:{file_path}:{start_line}"


def parse_file(path: Path, repo_root: Path) -> list[ParsedBlock]:
    """Parse one .tf file into ParsedBlock rows with verified line ranges."""
    text = path.read_text()

    with path.open() as fh:
        try:
            hcl2.load(fh)
        except Exception as exc:
            raise ValueError(f"{path}: not valid HCL: {exc}") from exc

    lines = text.splitlines()
    file_path = str(path.relative_to(repo_root))
    blocks: list[ParsedBlock] = []

    for match in BLOCK_RE.finditer(text):
        kind, label1, label2 = match.group(1), match.group(2), match.group(3)

        if kind in ("resource", "data"):
            resource_type, resource_name = label1, label2
        elif kind in ("module", "variable", "output"):
            resource_type, resource_name = None, label1
        else:
            resource_type, resource_name = None, None

        open_brace_index = match.end() - 1
        close_brace_index = _find_block_end(text, open_brace_index)

        start_line = text.count("\n", 0, match.start()) + 1
        end_line = text.count("\n", 0, close_brace_index) + 1
        body = "\n".join(lines[start_line - 1 : end_line])

        address = _address_for(kind, resource_type, resource_name, file_path, start_line)

        blocks.append(
            ParsedBlock(
                block_kind=kind,
                resource_type=resource_type,
                resource_name=resource_name,
                address=address,
                file_path=file_path,
                start_line=start_line,
                end_line=end_line,
                body=body,
            )
        )

    return blocks
```

Implementation notes:
- `hcl2.load()` takes a file object, not a path or string — open it explicitly as shown
  (matches the exact usage in SPEC.md section 9.1).
- `BLOCK_RE` is anchored with `^` and `re.MULTILINE`, so a block header must start at
  column 0 of its line. This matches real Terraform formatting (`terraform fmt` always
  puts block headers at column 0) and is the same regex SPEC.md prescribes verbatim.
- `match.end() - 1` is the index of the block's opening `{` itself, since the regex
  consumes it — `_find_block_end` starts depth-counting from exactly that character.
- Do not catch the `ValueError` from `_find_block_end` or from the `hcl2.load()`
  validation inside `parse_file` — let both propagate to the caller (see 5.3, which
  documents how `indexer.index_repo` handles this).

### 5.3 `ripple/ingest/indexer.py`

```python
from dataclasses import dataclass
from pathlib import Path

from ripple import db
from ripple.ingest import parser, scanner

MAX_EMBED_BODY_CHARS = 6000


@dataclass
class ResourceRow:
    block_kind: str
    resource_type: str | None
    resource_name: str | None
    address: str
    file_path: str
    start_line: int
    end_line: int
    body: str
    embed_text: str


def build_embed_text(block: parser.ParsedBlock) -> str:
    """Section 9.3: a header the vector should capture, then the (possibly
    truncated) body. Truncation is logged so silent data loss is visible.

    The "Type:" line uses resource_type ("aws_security_group") to match the
    worked example in SPEC.md section 9.3 exactly. resource_type is None for
    module/variable/output/locals blocks, so block_kind ("module", "locals",
    ...) is the fallback for those.
    """
    type_label = block.resource_type or block.block_kind
    header = f"{block.address}\nFile: {block.file_path}\nType: {type_label}\n\n"
    body = block.body
    if len(body) > MAX_EMBED_BODY_CHARS:
        print(
            f"WARNING: truncating embed_text body for {block.address} "
            f"({len(body)} -> {MAX_EMBED_BODY_CHARS} chars)"
        )
        body = body[:MAX_EMBED_BODY_CHARS]
    return header + body


def index_repo(repo_id: int, local_path: str) -> int:
    """Parse every .tf file under local_path and replace resources for repo_id.

    Full re-index, not incremental (SPEC section 2 explicitly rules out
    incremental indexing at this corpus size). db.replace_resources deletes the
    old rows and inserts the new ones in a single transaction, so a failed
    insert (e.g. a duplicate address) leaves the previous index untouched
    instead of leaving repo_id with no resources at all.
    """
    root = Path(local_path)
    blocks = [
        block
        for file_path in scanner.find_tf_files(root)
        for block in parser.parse_file(file_path, root)
    ]

    rows = [
        ResourceRow(
            block_kind=b.block_kind,
            resource_type=b.resource_type,
            resource_name=b.resource_name,
            address=b.address,
            file_path=b.file_path,
            start_line=b.start_line,
            end_line=b.end_line,
            body=b.body,
            embed_text=build_embed_text(b),
        )
        for b in blocks
    ]

    db.replace_resources(repo_id, rows)
    return len(rows)
```

`index_repo` does not catch exceptions from `scanner.find_tf_files`,
`parser.parse_file`, or `db.replace_resources` — a malformed file, an unbalanced-brace
bug, or a database collision aborts the whole run loudly, on purpose (see section 6).

### 5.4 `ripple/db.py` additions

Add one function; do not touch `get_connection` or `insert_repo`.

```python
from typing import Protocol


class ResourceRowLike(Protocol):
    block_kind: str
    resource_type: str | None
    resource_name: str | None
    address: str
    file_path: str
    start_line: int
    end_line: int
    body: str
    embed_text: str


def replace_resources(repo_id: int, rows: list[ResourceRowLike]) -> None:
    """Atomically replace all resources for repo_id: delete the existing rows
    and bulk-insert the new ones in a single transaction. embedding is left
    NULL (Day 3 populates it). If the insert fails — e.g. a UNIQUE
    (repo_id, address) collision — the delete is rolled back too, so a failed
    re-index never leaves repo_id with an empty or partial resources set.
    """
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM resources WHERE repo_id = %s", (repo_id,))
            if rows:
                cur.executemany(
                    """
                    INSERT INTO resources
                        (repo_id, block_kind, resource_type, resource_name,
                         address, file_path, start_line, end_line, body,
                         embed_text)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    """,
                    [
                        (
                            repo_id, r.block_kind, r.resource_type,
                            r.resource_name, r.address, r.file_path,
                            r.start_line, r.end_line, r.body, r.embed_text,
                        )
                        for r in rows
                    ],
                )
```

`ResourceRowLike` is a structural `Protocol`, not `ripple.ingest.indexer.ResourceRow`
itself — `db.py` must not import from `ripple.ingest`, since `indexer.py` already
imports `db`; importing back would be circular. Any object with those nine attributes
(the real `ResourceRow` dataclass from 5.3 included) satisfies it.

No explicit `commit()`/`rollback()` call here, and deliberately so: `psycopg`'s
connection context manager (`with get_connection() as conn:`) already commits the
transaction on a clean exit from the block and rolls it back automatically if an
exception propagates out of it, without closing the connection either way. Adding an
explicit `conn.commit()` inside the block would just double-commit on the success path
(harmless but redundant) and an explicit `except: conn.rollback(); raise` would
similarly duplicate the rollback the context manager already performs — so neither is
written here. The caller (`indexer.index_repo`) still sees any exception uncaught,
since nothing in this function catches it.

### 5.5 `scripts/index_repo.py` — wire in indexing

Add one import and two lines in `main()`, after the existing `db.insert_repo(...)`
call and before the final `print`:

```python
from ripple.ingest import indexer

# ... inside main(), after repo_id = db.insert_repo(...):
resource_count = indexer.index_repo(repo_id, str(local_path))
print(f"Registered repo id={repo_id} name={name} local_path={local_path}")
print(f"Indexed {resource_count} resource blocks")
```

Keep the two `print` calls as separate lines in that order (`Registered repo...` then
`Indexed N resource blocks`). This changes `main()`'s stdout, so
`tests/test_index_repo.py`'s `test_main_registers_local_repo` — which asserts the exact
`capsys` output from Day 1 — must be updated in the same change (see section 7): it now
needs to monkeypatch `index_repo.indexer.index_repo` (so it doesn't hit a real
database) and assert both lines. `test_main_uses_explicit_name` doesn't check `capsys`
output but does call `main()`, so it also needs the same `indexer.index_repo`
monkeypatch or it will attempt a real, unmocked database connection.

## 6. Interfaces, data structures, and error behavior

- `scanner.find_tf_files(root: Path) -> list[Path]` — pure filesystem read, no writes.
  Returns an empty list for a root with no `.tf` files; never raises for a missing
  ignored subdirectory (it just won't be walked).
- `parser.ParsedBlock` — dataclass, all fields required (no defaults). `resource_type`
  and `resource_name` are `None` for kinds where SPEC/HCL doesn't provide them (see
  5.2's `_address_for`).
- `parser.parse_file(path, repo_root) -> list[ParsedBlock]` — raises `ValueError` if
  `hcl2.load()` rejects the file as invalid HCL, or if brace-matching finds unbalanced
  braces. Both are allowed to propagate uncaught; this plan deliberately does not add
  per-file try/except resilience (see section 9).
- `indexer.build_embed_text(block: ParsedBlock) -> str` — pure, no I/O beyond a
  `print()` warning on truncation. Never raises. The "Type:" line is
  `block.resource_type` when present (resource/data blocks), else
  `block.block_kind` (module/variable/output/locals).
- `indexer.index_repo(repo_id: int, local_path: str) -> int` — returns the number of
  resource rows written. Raises whatever `parser.parse_file` or `db.replace_resources`
  raises, uncaught. Since all files are parsed *before* `db.replace_resources` is
  called, a malformed-HCL failure never touches the database at all; since
  `replace_resources` runs delete+insert in one transaction, a database-level failure
  (e.g. a duplicate address) also leaves the previous index completely untouched —
  there is no state where a failed `index_repo` call leaves `repo_id` with zero or
  partial resources.
- `db.replace_resources(repo_id, rows)` — atomic: delete existing rows for `repo_id`,
  then bulk-insert `rows`, in one transaction, relying on `psycopg`'s connection
  context manager to commit on success or roll back automatically if an exception
  propagates (no explicit `commit()`/`rollback()` call in the function body — see 5.4).
  Raises the underlying `psycopg` exception (e.g. a `UniqueViolation` on
  `(repo_id, address)`) uncaught if two rows collide; see the acceptance-criteria note
  about indexing one example directory at a time. Passing an
  empty `rows` list is valid and simply clears `repo_id`'s resources.

## 7. Required tests

`tests/fixtures/sample_repo/` (new, hand-crafted, offline — no network/DB needed to
exercise the parser and scanner):
- `main.tf` containing: one `resource` block, one `data` block, one `module` block, and
  one block whose body contains a heredoc with braces inside it
  (`policy = <<-EOF ... { "Statement": [...] } ... EOF`), a `#` comment containing a
  brace, and a string value containing an escaped quote and a brace.
- `variables.tf` containing two `variable` blocks and one `locals` block.
- `.terraform/should_be_ignored.tf` — must never appear in scan results.
- `terraform.tfstate` — not a `.tf` file, included to confirm it's never picked up.

`tests/test_scanner.py`:
- `find_tf_files` on `tests/fixtures/sample_repo` returns exactly `main.tf` and
  `variables.tf` (as resolved `Path`s), excluding everything under `.terraform/` and
  the `.tfstate` file.
- `find_tf_files` on an empty `tmp_path` directory returns `[]`.

`tests/test_parser.py` (this is the test SPEC.md section 9.1 explicitly requires):
- For every `ParsedBlock` returned by `parse_file` on each fixture file: re-read the
  source file, slice `lines[start_line-1:end_line]`, and assert it equals
  `block.body.splitlines()` — i.e., the stored body is exactly the source file's lines
  `start_line..end_line`, nothing more or less.
- For every block: assert the first line of `body` starts with `block.block_kind` and
  the last line of `body`, stripped, ends with `"}"`.
- Address correctness per kind: `resource "aws_security_group" "worker"` →
  `aws_security_group.worker`; `data "aws_ami" "ubuntu"` → `data.aws_ami.ubuntu`;
  `module "vpc"` → `module.vpc`; `variable "region"` → `var.region`; the `locals` block
  → `resource_name is None` and address starts with `"locals:"`.
- The heredoc-containing block: assert its `body` includes the full heredoc content
  (including the brace lines inside it) and that the block's `end_line` is the line
  with the resource's own closing `}`, not the line inside the heredoc that happens to
  contain a `}`.
- The comment-with-a-brace and string-with-a-brace cases: assert the block's `end_line`
  is unaffected by those braces (same reasoning — regression test for the exact bug
  SPEC.md 9.1 warns about).
- `parse_file` on a file with a deliberately unbalanced brace (a new small fixture, or
  a fixture written inline via `tmp_path` in the test itself): raises `ValueError`.

`tests/test_indexer.py` (integration, DB-dependent — follow the Day 1 pattern: attempt
a connection and `pytest.skip("database not reachable")` if it fails, never fabricate a
pass):
- Register a throwaway `repos` row (reuse `db.insert_repo`), call
  `indexer.index_repo(repo_id, "tests/fixtures/sample_repo")`, assert the returned
  count matches the number of blocks `parser.parse_file` finds across the fixture
  files, then query `resources` for that `repo_id` and assert each row's `address`,
  `file_path`, `start_line`, `end_line`, `body` match what the parser produced directly
  and that `embed_text` is non-empty and `embedding` is `NULL`.
- Call `indexer.index_repo` a second time for the same `repo_id` and assert the row
  count in `resources` for that `repo_id` is unchanged (not doubled) — proves the
  atomic replace path works as a re-index, not just as a one-time insert.
- `build_embed_text` (pure, no DB needed — can run without the skip guard): for a
  `ParsedBlock` with `block_kind="resource"`, `resource_type="aws_security_group"`,
  assert `result.splitlines()[2]` is exactly `"Type: aws_security_group"` (not
  `"Type: resource"`) — the header is `address` (line 0), `File: ...` (line 1), then
  `Type: ...` (line 2), so the "Type:" line is the *third* line of `embed_text`, not
  the second. For a `ParsedBlock` with `block_kind="locals"`, `resource_type=None`,
  assert `result.splitlines()[2]` is exactly `"Type: locals"` — proves the
  resource_type-with-block_kind-fallback rule from SPEC.md 9.3.
- Clean up: delete the `resources` rows (cascade-safe) and the `repos` row created for
  the test, same as `tests/test_db.py`'s pattern.

`tests/test_db.py` additions — atomicity of `replace_resources`:
- Using a throwaway `repos` row, call `replace_resources(repo_id, [row_a, row_b])` with
  two valid, distinct-address rows; confirm both are present via a `SELECT`.
- Call `replace_resources(repo_id, [row_c, row_c])` where `row_c` is repeated twice
  (same `address` in both elements of the list — guaranteed to trip the
  `UNIQUE (repo_id, address)` constraint on the second `INSERT`); assert this raises
  (e.g. `psycopg.errors.UniqueViolation` or the broader `psycopg.Error`).
- Re-`SELECT` for `repo_id` and assert the table still contains exactly `row_a` and
  `row_b` — proving the failed call's `DELETE` was rolled back rather than left
  committed. This is the direct regression test for the atomicity requirement.
- Clean up the `repos` row (cascades to `resources`) afterward.
- Skip this whole group with `pytest.skip("database not reachable")` if the initial
  connection attempt fails, same as the rest of `test_db.py`.

Run `python -m pytest` after implementation; all tests must pass (DB-dependent tests
skip cleanly if Postgres isn't reachable, matching the Day 1 convention).

## 8. Acceptance criteria

- `python -m pytest` passes with no failures.
- Every `ParsedBlock`/`resources` row satisfies: source lines `start_line..end_line`
  begin with the block header and end with `}` — verified by `tests/test_parser.py`.
- Running `python scripts/index_repo.py .repos/terraform-aws-vpc/examples/complete
  --name vpc-complete` (the real corpus already cloned locally from Day 1) prints both
  `Registered repo id=...` and `Indexed N resource blocks`, and `resources` in the
  database has `N` new rows for that `repo_id` with correct `address`/`file_path`/line
  ranges spot-checkable against the actual files.
- **Index one example subdirectory at a time, not the whole `examples/` tree.** Indexing
  `examples/` as a single repo collides on `UNIQUE (repo_id, address)` —
  `data.aws_availability_zones.available` (and possibly others) appears in multiple
  sibling example directories. `examples/complete` alone has no such collisions. This is
  consistent with SPEC section 5's own framing of "the examples" as independent flat
  root configurations, not one combined corpus.
- Calling `indexer.index_repo(repo_id, local_path)` twice **with the same `repo_id`**
  does not duplicate rows (verified by the indexer integration test). This is a
  property of `index_repo`/`replace_resources` themselves, exercised directly in the
  test — it is *not* something re-running the `scripts/index_repo.py` CLI
  demonstrates: each CLI invocation calls `db.insert_repo` (Day 1 behavior, unchanged
  this cycle), which always inserts a brand-new `repos` row with a new `repo_id`, so
  running the CLI twice for the same source path produces two independent repos with
  two independent resource sets, never a `repo_id` collision for `replace_resources`
  to resolve.
- `embed_text` is populated (non-empty, header + body per section 9.3) for every row,
  with the "Type:" line using `resource_type` for resource/data blocks (matching
  SPEC.md 9.3's worked example exactly) and `block_kind` only as the fallback for
  module/variable/output/locals; `embedding` remains `NULL` for every row — Day 3's
  job, not this cycle's.
- A failed `replace_resources` call (e.g. triggered by a duplicate address) leaves
  `repo_id`'s existing `resources` rows exactly as they were before the call — verified
  by the new `test_db.py` atomicity test.
- `python scripts/index_repo.py <local-dir>` still prints exactly two lines
  (`Registered repo id=...` then `Indexed N resource blocks`), and
  `tests/test_index_repo.py` is updated to assert both.

## 9. Explicit non-goals

- Reference extraction / `edges` population (Day 4) — `parser.py`/`indexer.py` this
  cycle only ever write to `resources`.
- Embedding generation, `embeddings.py`, populating the `embedding` column (Day 3).
- Any retrieval (vector, BM25, RRF, rerank, graph) — none of `RetrievalConfig`'s stages
  are touched this cycle.
- Resilience to malformed HCL: a bad file aborts the whole `index_repo` call with an
  uncaught `ValueError`. No per-file skip-and-continue logic. If this proves too brittle
  against a real-world repo later, that's a deliberate future decision, not an oversight
  to silently paper over now.
- `tree-sitter-hcl` (SPEC's stated alternative to the two-pass regex approach) — not
  taken; the regex/brace-matching approach is validated well enough by the required
  tests to not need the half-day of grammar setup SPEC.md mentions as the tradeoff.
- Any change to `ripple/ingest/clone.py` as named in SPEC section 8's module layout —
  Day 1 implemented the clone/resolve-local-path logic directly inside
  `scripts/index_repo.py`'s `resolve_repo_source` rather than as a separate
  `ripple/ingest/clone.py` module. This plan does not refactor that; `indexer.py` only
  needs a `local_path` string, which `scripts/index_repo.py` already has, so there's no
  functional need to relocate that code this cycle.

## 10. Risks or ambiguities

- **`locals` block addressing is a judgment call.** SPEC.md never specifies an address
  format for non-resource/data blocks. This plan uses `module.<name>`, `var.<name>`,
  `output.<name>`, and `locals:<file_path>:<start_line>` — reasonable and internally
  consistent, but not spec-mandated. If a future day's reference-resolution logic
  (section 9.2, which only discusses resolving `(type, name)` pairs against
  resource/data addresses) needs a different convention for these other kinds, this
  addressing scheme may need revisiting then. Low risk in practice: SPEC's benchmark
  categories (section 10.1) are all about resource/data blocks, not module/variable/
  output/locals.
- **Malformed-HCL abort behavior.** Choosing to let `parse_file` raise uncaught (rather
  than skip-and-log per file) means one bad file blocks indexing the entire repo. This
  matches "never fabricate" in spirit (no silent partial index) but could be annoying
  against a large real-world repo with one quirky file. Flag to the user if that
  tradeoff is unwelcome — the fix later is a narrow try/except in `index_repo` around
  the per-file parse call, not a parser rewrite.
- **`python-hcl2` library limitations on exotic syntax.** The structural-pass validation
  step (`hcl2.load()`) trusts the library to correctly accept/reject files. If a real
  file uses HCL syntax `python-hcl2` doesn't support (some `dynamic` block shapes,
  certain newer function-call forms), `parse_file` will raise even though the file is
  valid Terraform. If this happens against the real corpus, treat it as a per-file
  problem to investigate, not a sign the two-pass design is wrong.
- **`.repos/terraform-aws-vpc` is local-only, gitignored state.** The acceptance
  criteria reference it for manual verification, but it won't exist in a fresh clone or
  CI environment — that's expected (SPEC.md's corpus is fetched via `index_repo.py`
  itself, not checked into the repo), just worth knowing before assuming the acceptance
  check is reproducible without first running Day 1's registration step against that
  URL.
