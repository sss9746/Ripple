# Implementation Plan — Day 4: Reference Extraction

## 1. Objective

Extract the Terraform reference graph: for every `resources` row, find its outgoing
references (`aws_vpc.main.id`-style identifiers in its body), resolve each against the
same repo's address table, and write the resolvable ones to `edges`. Add
`dependents()`/`dependencies()` graph queries so "what references this block" and
"what does this block reference" are both answerable. This is the data blast-radius
questions run on; nothing consumes it yet (that's Day 8/13).

This is SPEC.md's Day 4 milestone, sitting on top of Day 1–3
(`ripple/config.py`, `ripple/db.py`, `scripts/index_repo.py`, `ripple/ingest/`,
`ripple/llm/`, `ripple/retrieval/`), all already implemented and verified.

## 2. Relevant SPEC.md requirements

- Section 11, Day 4: "`references.py` per section 9.2. Second indexing pass populating
  `edges`. `graph.py` with `dependents()` and `dependencies()` queries. Sanity check:
  pick a subnet, confirm its edge to the VPC exists and points the right way.
  **Done when:** you can list everything referencing a given block, and the counts
  look plausible against a manual read of the file."
- Section 9.2 (Reference extraction), quoted in full since every clause matters:
  ```python
  REF_RE = re.compile(
      r'\b(?:data\.)?([a-z][a-z0-9_]*)\.([a-z_][a-z0-9_-]*)'
      r'(?:\.[a-z_][a-z0-9_\[\].*-]*)?'
  )
  ```
  Rules:
  - Resolve each `(type, name)` pair against the `resources` table for the same repo.
    If it resolves, write an edge. If not, discard silently — most non-matches are
    attribute accesses on locals or variables.
  - **Exclude self-references.** Skip any edge where `source_id == target_id`.
  - Skip references inside comments.
  - `data.aws_ami.ubuntu` and `aws_ami.ubuntu` are different blocks. Include the
    `data.` prefix in the address when the block kind is `data`.
  - Deduplicate: one edge per `(source, target)` pair, keeping the first `ref_text`.
  - "Reference extraction runs as a second pass **after** all resources are inserted,
    because resolution needs the full address table."
- Section 7 (schema): `edges` — `id`, `repo_id`, `source_id` (block containing the
  reference), `target_id` (block being referenced), `ref_text` (`NOT NULL`). Indexed on
  `source_id` and `target_id`. **No uniqueness constraint** on `(repo_id, source_id,
  target_id)` — deduplication is an application-level responsibility (this plan's
  `seen` set in 5.3), not something Postgres enforces.
- Section 9.8 (Graph expansion — base queries only; the *pipeline wiring* with depth
  limits, `graph_max_added`, and "referenced by X" prompt annotations is Day 8/13, not
  this cycle):
  ```sql
  -- dependents: what references this block (blast radius)
  SELECT r.* FROM edges e JOIN resources r ON r.id = e.source_id
  WHERE e.target_id = $1;

  -- dependencies: what this block references
  SELECT r.* FROM edges e JOIN resources r ON r.id = e.target_id
  WHERE e.source_id = $1;
  ```
- Section 8 (repository layout) — **note the correct paths**: `references.py` lives
  under `ripple/ingest/` (`references.py` — "body text -> outgoing references"), but
  `graph.py` lives under `ripple/retrieval/` ("neighbor expansion"), *not*
  `ripple/ingest/`. `indexer.py` is described as orchestrating "parse, embed, write
  rows **and edges**" — the second indexing pass belongs there, using `references.py`'s
  pure functions.

## 3. Current implementation gaps

- `ripple/ingest/references.py` does not exist — nothing extracts references from a
  block's body.
- `edges` has never been written to. The table and its indexes exist (Day 1 schema)
  but every repo indexed so far (Days 1–3) has zero rows there.
- `ripple/retrieval/graph.py` does not exist — no `dependents()`/`dependencies()`.
- `ripple/db.py` has no way to bulk-write edges or to re-fetch a repo's resource
  bodies for the second pass.
- `scripts/index_repo.py` only registers a repo, parses it, and embeds it — it never
  extracts edges, so its output is silent about the graph entirely.

## 4. Exact files Codex should create or modify

Create:
- `ripple/ingest/references.py`
- `ripple/retrieval/graph.py`
- `tests/fixtures/reference_repo/main.tf`
- `tests/fixtures/reference_repo/variables.tf`
- `tests/test_references.py`
- `tests/test_graph.py`

Modify:
- `ripple/db.py` — add `replace_edges`, `fetch_resource_bodies`, `EdgeRowLike`.
- `ripple/ingest/indexer.py` — add `EdgeRow` and `index_edges(repo_id)`.
- `scripts/index_repo.py` — call `indexer.index_edges(repo_id)` after indexing
  resources; print a third output line.
- `tests/test_index_repo.py` — both `main()` tests must also monkeypatch
  `index_repo.indexer.index_edges`, and `test_main_registers_local_repo`'s exact
  `capsys` assertion must include the new third line (same class of change as Day 2's
  print-line addition — do not leave this for a later review pass).
- `tests/test_indexer.py` — add `index_edges` coverage using the new
  `tests/fixtures/reference_repo/` fixture (the existing `tests/fixtures/sample_repo/`
  fixture has zero cross-references between its blocks, so it can't exercise this
  logic — see 5.4/7 for why a new fixture was created instead of extending the old
  one).
- `tests/test_db.py` — add a `replace_edges` atomicity test (see 7): `edges` has no
  unique constraint the way `resources` does, so the test instead uses an
  impossible-by-construction `target_id = -1` to trigger a foreign-key violation and
  prove the rollback.

Do not modify: `sql/schema.sql`, `docker-compose.yml`, `.env.example`,
`requirements.txt`, `ripple/config.py`, `ripple/ingest/scanner.py`,
`ripple/ingest/parser.py`, `ripple/llm/*`, `ripple/retrieval/vector_store.py`,
`ripple/retrieval/pgvector_store.py`, `scripts/ask.py`,
`tests/fixtures/sample_repo/*` (Day 2's original fixture — leave it exactly as is),
`AGENTS.md`, `CLAUDE.md`, `README.md`, `tests/test_config.py`, `tests/test_scanner.py`,
`tests/test_parser.py`, `tests/test_embeddings.py`, `tests/test_generate.py`,
`tests/test_prompts.py`, `tests/test_pgvector_store.py`, `tests/test_ask.py`.

## 5. Step-by-step implementation instructions

### 5.1 `ripple/ingest/references.py`

```python
import re

from ripple.ingest.parser import HEREDOC_START_RE

REF_RE = re.compile(
    r'\b(?:data\.)?([a-z][a-z0-9_]*)\.([a-z_][a-z0-9_-]*)'
    r'(?:\.[a-z_][a-z0-9_\[\].*-]*)?'
)


def _mask_comments(text: str) -> str:
    """Blank out '#'/'//' line comments and '/* */' block comments with
    spaces. Strings and heredocs are skipped over — their contents are left
    completely untouched, since Terraform references commonly appear inside
    string interpolations and heredoc bodies (e.g. an IAM policy heredoc
    referencing another resource's ARN) — purely so that a '#' or '//'
    appearing inside one of them is never mistaken for the start of a real
    comment.
    """
    result = list(text)
    i = 0
    n = len(text)

    while i < n:
        ch = text[i]

        if ch == '"':
            i += 1
            while i < n and text[i] != '"':
                i += 2 if text[i] == "\\" else 1
            i += 1
            continue

        heredoc_match = HEREDOC_START_RE.match(text, i)
        if heredoc_match:
            marker = heredoc_match.group("marker")
            terminator_re = re.compile(
                rf"^[ \t]*{re.escape(marker)}\s*$", re.MULTILINE
            )
            terminator = terminator_re.search(text, heredoc_match.end())
            i = terminator.end() if terminator else n
            continue

        if ch == "#" or text[i : i + 2] == "//":
            newline = text.find("\n", i)
            end = newline if newline != -1 else n
            for j in range(i, end):
                result[j] = " "
            i = end
            continue

        if text[i : i + 2] == "/*":
            comment_end = text.find("*/", i + 2)
            end = comment_end + 2 if comment_end != -1 else n
            for j in range(i, end):
                if text[j] != "\n":
                    result[j] = " "
            i = end
            continue

        i += 1

    return "".join(result)


def extract_references(body: str) -> list[str]:
    """Return every outgoing reference's raw ref_text found in body, in
    order of appearance, including duplicates. Deduplication happens at
    resolution time (indexer.index_edges), not here — only *resolvable*
    duplicates should collapse to one edge.
    """
    masked = _mask_comments(body)
    return [match.group(0) for match in REF_RE.finditer(masked)]


def _resolve_reference_address(ref_text: str) -> str:
    """Given a raw ref_text (e.g. 'aws_vpc.main.id' or
    'data.aws_ami.ubuntu.id'), return the resources.address it would refer
    to ('aws_vpc.main' or 'data.aws_ami.ubuntu') if a block with that
    address exists. Does not touch the database — resolution against the
    real address table happens in indexer.index_edges.

    Private: the only caller is indexer.index_edges, which only ever passes
    ref_text values that came from extract_references and are therefore
    guaranteed to match REF_RE. Not exposed as a public, arbitrary-input-safe
    API — same convention as parser.py's _find_block_end/_address_for.
    """
    match = REF_RE.match(ref_text)
    resource_type, resource_name = match.group(1), match.group(2)
    if ref_text.startswith("data."):
        return f"data.{resource_type}.{resource_name}"
    return f"{resource_type}.{resource_name}"
```

`HEREDOC_START_RE` is imported from `ripple.ingest.parser` rather than redefined here
— same pattern, single source of truth, and `parser.py` has no reverse dependency on
`references.py` so this doesn't create a cycle.

`_mask_comments` and `_resolve_reference_address` are both private helpers, following
this codebase's existing convention (`parser.py`'s `_find_block_end`/`_address_for`).
`_mask_comments`'s effect is tested indirectly through `extract_references`'s public
behavior (comment-skipping); `_resolve_reference_address` is simple enough, and
self-contained enough, that it's fine to test directly by importing it from the module
(see 7) — Python doesn't enforce privacy, and there's real value in pinning its
address-derivation rules with direct examples.

### 5.2 `ripple/retrieval/graph.py`

```python
from dataclasses import dataclass

from ripple import db


@dataclass
class GraphNeighbor:
    id: int
    address: str
    file_path: str
    start_line: int
    end_line: int
    body: str
    ref_text: str


def dependents(resource_id: int) -> list[GraphNeighbor]:
    """Blast radius: every block that references resource_id."""
    with db.get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT r.id, r.address, r.file_path, r.start_line, r.end_line,
                       r.body, e.ref_text
                FROM edges e
                JOIN resources r ON r.id = e.source_id
                WHERE e.target_id = %s
                ORDER BY r.address
                """,
                (resource_id,),
            )
            rows = cursor.fetchall()
    return [GraphNeighbor(*row) for row in rows]


def dependencies(resource_id: int) -> list[GraphNeighbor]:
    """Everything resource_id itself references."""
    with db.get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT r.id, r.address, r.file_path, r.start_line, r.end_line,
                       r.body, e.ref_text
                FROM edges e
                JOIN resources r ON r.id = e.target_id
                WHERE e.source_id = %s
                ORDER BY r.address
                """,
                (resource_id,),
            )
            rows = cursor.fetchall()
    return [GraphNeighbor(*row) for row in rows]
```

`ORDER BY r.address` makes results deterministic — without it, row order for a given
`resource_id` is unspecified and could vary between runs, making both the tests in
section 7 and any future caller's output flaky/non-reproducible. `resource_id` alone
is otherwise sufficient — `resources.id`/`edges.id` are globally unique
(`SERIAL PRIMARY KEY`), so no `repo_id` filter is needed, matching section 9.8's SQL
exactly (`WHERE e.target_id = $1`, no repo scoping). `ref_text` is included in
`GraphNeighbor` even though section 9.8's minimal SQL (`r.*`) doesn't ask for it — it's
free from the same `JOIN` and makes the Day 4 "sanity check" (confirming an edge
"points the right way") much easier to verify by eye. This is *not* the "referenced by
X" prompt annotation from section 9.8 — that's a Day 8/13 pipeline concern; this is
just the raw query layer.

### 5.3 `ripple/ingest/indexer.py` additions

```python
from ripple.ingest import references

@dataclass
class EdgeRow:
    source_id: int
    target_id: int
    ref_text: str


def index_edges(repo_id: int) -> int:
    """Second indexing pass: extract reference edges between resources
    already written for repo_id. Must run after that repo's resources have
    been written (index_repo, or anything else that calls
    db.replace_resources) — resolution needs the full address table
    (SPEC.md 9.2).
    """
    resource_rows = db.fetch_resource_bodies(repo_id)
    address_to_id = {
        address: resource_id for resource_id, address, _ in resource_rows
    }

    seen: set[tuple[int, int]] = set()
    edges: list[EdgeRow] = []

    for source_id, _address, body in resource_rows:
        for ref_text in references.extract_references(body):
            target_address = references._resolve_reference_address(ref_text)
            target_id = address_to_id.get(target_address)

            if target_id is None or target_id == source_id:
                continue

            key = (source_id, target_id)
            if key in seen:
                continue
            seen.add(key)

            edges.append(
                EdgeRow(source_id=source_id, target_id=target_id, ref_text=ref_text)
            )

    db.replace_edges(repo_id, edges)
    return len(edges)
```

Deliberately a **separate, independently-callable function**, not folded into
`index_repo()` itself, even though section 8 describes `indexer.py` as orchestrating
"parse, embed, write rows and edges" as one conceptual responsibility. Keeping it
separate means: `index_repo()`'s existing return type (`int`, resource count) and every
test that already asserts against it (Day 2/3's `test_index_repo_round_trip_and_reindex`,
the empty-repository test) needs **zero changes**. `scripts/index_repo.py` calls both
functions in sequence (5.5), matching the two-separate-steps pattern it already
established for `db.insert_repo` + `indexer.index_repo` in Day 1/3.

`target_id is None` covers every "discard silently" case from section 9.2 in one
check: an unresolvable `(type, name)` (attribute access on a local/variable that
doesn't map to a real block — see section 10 for exactly which cases resolve and
which don't), or a resolvable-looking address that simply isn't in *this* repo.
`target_id == source_id` is the self-reference exclusion (a security group referencing
its own ID in an ingress rule is common, real Terraform — this is not a hypothetical
edge case). The `seen` set is the deduplication rule, keeping the first `ref_text` for
a given `(source, target)` pair since `extract_references` can return the same
resolvable reference more than once in one body.

Calling `index_edges` for a `repo_id` with zero resources (e.g. right after Day 3's
empty-repository short-circuit) is safe and returns `0` — `fetch_resource_bodies`
returns `[]`, the loop never runs, `db.replace_edges(repo_id, [])` just clears any
stale edges.

### 5.4 New fixture: `tests/fixtures/reference_repo/`

A **new, separate** fixture — not an extension of Day 2's `tests/fixtures/sample_repo/`
— because that fixture's blocks have zero cross-references between them (nothing in
its `main.tf`/`variables.tf` refers to anything else in the same file), and every
existing test in `test_parser.py`/`test_scanner.py`/`test_indexer.py` has hardcoded
assertions keyed to that fixture's exact block count and address set. Adding
references to it risks silently breaking those Day 2/3 tests instead of just adding
Day 4 coverage.

`tests/fixtures/reference_repo/main.tf`:
```hcl
resource "aws_vpc" "main" {
  cidr_block = var.cidr
}

resource "aws_subnet" "public" {
  vpc_id     = aws_vpc.main.id
  cidr_block = "10.0.1.0/24"
}

resource "aws_security_group" "worker" {
  vpc_id = aws_vpc.main.id

  ingress {
    security_groups = [aws_security_group.worker.id]
  }
}

data "aws_ami" "ubuntu" {
  most_recent = true
}

resource "aws_instance" "node" {
  ami       = data.aws_ami.ubuntu.id
  subnet_id = aws_subnet.public.id
  iam_role  = aws_iam_role.missing.name

  tags = {
    Name = local.prefix
  }
}
```

`tests/fixtures/reference_repo/variables.tf`:
```hcl
variable "cidr" {
  type    = string
  default = "10.0.0.0/16"
}

locals {
  prefix = "demo"
}
```

This fixture exercises every rule in section 9.2 with a clean, predictable expected
edge count:

| Reference (in body) | Resolves to | Edge written? |
|---|---|---|
| `var.cidr` (in `aws_vpc.main`) | `variable "cidr"` block | **Yes** — `aws_vpc.main -> var.cidr` |
| `aws_vpc.main.id` (in `aws_subnet.public`) | `aws_vpc.main` | **Yes** |
| `aws_vpc.main.id` (in `aws_security_group.worker`) | `aws_vpc.main` | **Yes** |
| `aws_security_group.worker.id` (in its own `ingress` block) | itself | **No** — self-reference, excluded |
| `data.aws_ami.ubuntu.id` (in `aws_instance.node`) | `data.aws_ami.ubuntu` | **Yes** |
| `aws_subnet.public.id` (in `aws_instance.node`) | `aws_subnet.public` | **Yes** |
| `aws_iam_role.missing.name` (in `aws_instance.node`) | nothing (no such block) | **No** — discarded silently |
| `local.prefix` (in `aws_instance.node`) | nothing (see section 10 — `local.X` can never resolve under this parser's `locals` addressing scheme) | **No** — discarded silently |

**Expected total: 5 edges.**

### 5.5 `ripple/db.py` additions

```python
class EdgeRowLike(Protocol):
    source_id: int
    target_id: int
    ref_text: str


def replace_edges(repo_id: int, rows: list[EdgeRowLike]) -> None:
    """Atomically replace all edges belonging to one repository."""
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM edges WHERE repo_id = %s", (repo_id,))
            if rows:
                cursor.executemany(
                    """
                    INSERT INTO edges (repo_id, source_id, target_id, ref_text)
                    VALUES (%s, %s, %s, %s)
                    """,
                    [
                        (repo_id, row.source_id, row.target_id, row.ref_text)
                        for row in rows
                    ],
                )


def fetch_resource_bodies(repo_id: int) -> list[tuple[int, str, str]]:
    """Return (id, address, body) for every resource row of repo_id — the
    'full address table' section 9.2 says resolution needs.
    """
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT id, address, body FROM resources WHERE repo_id = %s",
                (repo_id,),
            )
            return cursor.fetchall()
```

`replace_edges` follows the exact same pattern Day 3's review established for
`replace_resources`: no explicit `commit()`/`rollback()`, relying on `psycopg`'s
connection context manager to commit on clean exit or roll back automatically if an
exception propagates. Note `edges.source_id`/`edges.target_id` have `ON DELETE
CASCADE` foreign keys to `resources.id` — so `replace_resources`'s own `DELETE FROM
resources` (Day 2/3) *already* wipes any edges pointing at the deleted rows as a side
effect. `replace_edges`'s own `DELETE FROM edges WHERE repo_id = %s` is technically
redundant immediately after a fresh `index_repo()` call, but it's kept anyway so
`index_edges()` is correct and idempotent on its own, regardless of what a future
caller does or doesn't do first (see section 10).

### 5.6 `scripts/index_repo.py` — wire in edge extraction

`indexer` is already imported (`from ripple.ingest import indexer`, added Day 3) — no
new import is needed. Add two lines in `main()`, after the existing
`indexer.index_repo(...)` call:

```python
resource_count = indexer.index_repo(repo_id, str(local_path))
edge_count = indexer.index_edges(repo_id)

print(f"Registered repo id={repo_id} name={name} local_path={local_path}")
print(f"Indexed {resource_count} resource blocks")
print(f"Extracted {edge_count} reference edges")
```

This changes `main()`'s stdout again — same class of change as Day 2's second print
line. **Both existing `tests/test_index_repo.py` `main()` tests must be updated in
this same change**, not left for later (4, 7).

## 6. Interfaces, data structures, and error behavior

- `references.extract_references(body) -> list[str]` — pure, no I/O, never raises.
  Returns raw `ref_text` strings in order of appearance, duplicates included.
- `references._resolve_reference_address(ref_text) -> str` — pure, private. Assumes
  `ref_text` was produced by `REF_RE` (every real call site — `indexer.index_edges` —
  only ever passes values that came from `extract_references`, which guarantees this).
  Not designed to validate arbitrary external input; it isn't part of the module's
  public contract.
- `indexer.index_edges(repo_id) -> int` — returns the number of edges written for
  `repo_id`. Must be called after that repo's resources exist (typically right after
  `index_repo()`); calling it for a repo with no resources is safe and returns `0`.
  Does not raise for a repo with zero resolvable references. Propagates whatever
  `db.replace_edges` raises (e.g. a foreign-key violation from a malformed row — should
  not happen given `address_to_id` is built from the same table `target_id` values
  come from, but not defended against beyond that).
- `db.replace_edges(repo_id, rows)` — same atomicity contract as
  `db.replace_resources`: delete-then-insert in one transaction, no explicit
  commit/rollback, relying on the connection context manager. An empty `rows` list is
  valid and simply clears the repo's edges.
- `db.fetch_resource_bodies(repo_id) -> list[tuple[int, str, str]]` — `(id, address,
  body)` tuples, in whatever order Postgres returns them (no `ORDER BY` — order
  doesn't matter for building `address_to_id` or iterating sources).
- `graph.dependents(resource_id) -> list[GraphNeighbor]` / `graph.dependencies(...)` —
  return `[]` for a resource with no edges in that direction, or for a nonexistent
  `resource_id` (not an error case, just an empty join result). Never raise for an
  unknown id.

## 7. Required tests

`tests/test_references.py` (pure, no DB, no fixture files — inline body strings):
- Plain reference with a trailing attribute (`vpc_id = aws_vpc.main.id`) extracts
  `"aws_vpc.main.id"`.
- Data-prefixed reference (`ami = data.aws_ami.ubuntu.id`) extracts
  `"data.aws_ami.ubuntu.id"`.
- A reference inside a `#` comment, a `//` comment, and a `/* */` block comment: none
  of them appear in the result.
- A reference inside a double-quoted string interpolation (e.g.
  `name = "${aws_vpc.main.id}-sg"`): **is** extracted — strings are not comments.
- A reference inside a heredoc body (an IAM-policy-shaped `<<-EOF ... EOF` containing
  `aws_iam_role.example.arn`): **is** extracted, and a `#` character appearing
  elsewhere inside that same heredoc does not swallow real content after it (the exact
  bug class section 9.1 warned about for the parser, now checked for the reference
  extractor too).
- Multiple distinct references in one body are all returned, in order, with
  duplicates preserved (dedup is `index_edges`'s job, not `extract_references`'s).
- `_resolve_reference_address`: `"aws_vpc.main.id"` → `"aws_vpc.main"`;
  `"data.aws_ami.ubuntu.id"` → `"data.aws_ami.ubuntu"`; `"aws_vpc.main"` (no trailing
  attribute at all) → `"aws_vpc.main"` (the third capture group is optional).
- **Underscored names extract and resolve in full** — both the type group
  (`[a-z][a-z0-9_]*`) and the name group (`[a-z_][a-z0-9_-]*`) allow underscores in
  `REF_RE`, so a reference whose target name contains an underscore is not truncated:
  ```python
  def test_extract_and_resolve_reference_with_underscored_name() -> None:
      body = "policy = data.aws_iam_policy_document.dynamodb_endpoint_policy.json"

      extracted = references.extract_references(body)
      assert extracted == [
          "data.aws_iam_policy_document.dynamodb_endpoint_policy.json"
      ]
      assert references._resolve_reference_address(extracted[0]) == (
          "data.aws_iam_policy_document.dynamodb_endpoint_policy"
      )
  ```

`tests/test_indexer.py` additions (DB-dependent, skip-if-unreachable, using the new
`tests/fixtures/reference_repo/`):
- Index the fixture (`index_repo(repo_id, str(REFERENCE_FIXTURE_ROOT),
  embedder=_FakeEmbeddingProvider())`), then call `indexer.index_edges(repo_id)`;
  assert it returns `5` (per the table in 5.4).
- Query `edges` (or use `graph.dependents`/`dependencies`, once that module exists) and
  assert: `aws_subnet.public -> aws_vpc.main` exists with `ref_text ==
  "aws_vpc.main.id"`; `aws_security_group.worker -> aws_vpc.main` exists; **no** edge
  has `source_id == target_id` for this repo (the self-reference in `ingress` never
  became an edge); `aws_instance.node -> data.aws_ami.ubuntu` exists (proving the
  `data.` prefix round-trips correctly through resolution); nothing resolved for
  `aws_iam_role.missing` or `local.prefix`.
- Call `index_edges(repo_id)` a second time; assert the count and edge set are
  unchanged (idempotent replace, same convention as `replace_resources`).
- `test_index_edges_empty_repository` — call `indexer.index_edges` for a `repo_id`
  with zero resources (reuse Day 3's empty-repo pattern or a fresh throwaway repo with
  no resources indexed); assert it returns `0` without error.

`tests/test_graph.py` (DB-dependent, skip-if-unreachable; reuse the indexed
`reference_repo` fixture, or a small hand-inserted resources+edges set if that's
simpler to isolate):
- `dependencies(subnet_id)` returns exactly `[aws_vpc.main]` as a `GraphNeighbor`, with
  the correct `ref_text`.
- `dependents(vpc_id)` returns both `aws_security_group.worker` and `aws_subnet.public`
  (both reference the VPC), **in that exact order** — `ORDER BY r.address` sorts
  `"aws_security_group.worker"` before `"aws_subnet.public"` alphabetically, so this
  test asserts the full ordered list, not a set — this is the literal Day 4 "sanity
  check" from section 11, automated: a subnet's edge to the VPC exists (via
  `dependencies`) and points the right way (the VPC lists the subnet as a dependent,
  via `dependents`), not just one direction.
- `dependents`/`dependencies` for a resource with no edges in that direction (e.g.
  `data.aws_ami.ubuntu` has dependents but no dependencies) returns `[]`.
- Results are deterministic across repeated calls (call `dependents(vpc_id)` twice,
  assert identical ordered output) — the regression test for `ORDER BY r.address`
  actually mattering.

`tests/test_index_repo.py` — **update both existing `main()` tests**:
- `test_main_registers_local_repo`: add a `record_index_edges` stub (returning e.g.
  `3`), `monkeypatch.setattr(index_repo.indexer, "index_edges", record_index_edges)`,
  and update the `capsys` assertion to the full three-line output:
  ```
  Registered repo id=42 name=... local_path=...
  Indexed 6 resource blocks
  Extracted 3 reference edges
  ```
- `test_main_uses_explicit_name`: same `index_edges` monkeypatch (doesn't check
  `capsys`, but would otherwise hit a real, unmocked database).

`tests/test_db.py` — **required** addition, `test_replace_edges_rolls_back_on_insert_failure`:
`edges` has no unique constraint to violate (unlike `resources`), so the natural
failure mode to test instead is a **foreign-key violation** — use `target_id = -1` (a
value structurally guaranteed to never exist, since `resources.id` is a `SERIAL` and
can never be negative — preferred over an arbitrary large positive number, which is
merely improbable rather than impossible) and confirm `db.replace_edges` raises
(`psycopg.errors.ForeignKeyViolation`) while leaving any previously-committed edges for
that `repo_id` untouched, mirroring
`test_replace_resources_rolls_back_on_insert_failure`'s structure exactly.

Run `python -m pytest` after implementation; all tests must pass. DB-dependent tests
skip cleanly if Postgres isn't reachable, same convention as every prior day.

## 8. Acceptance criteria

- `python -m pytest` passes with no failures.
- `tests/fixtures/sample_repo/`-based tests (Day 2/3) are completely unaffected —
  their block counts, addresses, and embedding assertions are unchanged.
- Indexing `tests/fixtures/reference_repo/` produces exactly 5 edges, matching the
  table in 5.4, with the self-reference and both unresolvable references correctly
  excluded.
- `python scripts/index_repo.py <path> --name ...` now prints three lines, the third
  being `Extracted N reference edges`.
- **Manual sanity check against the real corpus** (SPEC.md's own Day 4 "Done when"
  wording), using a real, already-verified relationship from
  `.repos/terraform-aws-vpc/examples/complete/main.tf`: `aws_security_group.rds`
  contains `vpc_id = module.vpc.vpc_id` (confirmed present at that file's line 214
  during Day 2 verification). After re-indexing that repo:
  - `graph.dependencies(<aws_security_group.rds's id>)` must include `module.vpc`.
  - `graph.dependents(<module.vpc's id>)` must include `aws_security_group.rds` (and
    likely other resources in that file referencing `module.vpc.*` outputs).
  - This is a real edge pointing the right way, verifiable by reading the file
    directly — exactly SPEC.md's own acceptance bar.

## 9. Explicit non-goals

- Wiring graph expansion into the retrieval pipeline or prompt (the actual "add
  dependents/dependencies of the top-N reranked results to the context, marked with
  their relationship" behavior from section 9.8) — that's Day 8 (first pipeline
  wiring) and Day 13 (the dedicated graph-expansion day) with real depth/count limits
  (`graph_seed_n`, `graph_max_added` from `RetrievalConfig`). This cycle only builds
  the query layer those days will call.
- Depth-2+ traversal. `dependents`/`dependencies` are depth-1 only, matching section
  9.8's explicit warning against blind depth-2 expansion (unrelated to *when* it's
  wired in — the functions themselves simply don't support a depth parameter yet).
- "Referenced by: X" annotations on retrieved blocks — a prompt-formatting concern for
  whichever day actually calls `graph.py`, not this cycle.
- Making `local.X` references resolve against `locals` blocks. Day 2's parser stores
  one address per `locals { ... }` *block* (`locals:<file>:<line>`, since the block
  itself has no header label), not one address per named local value defined inside
  it. Resolving `local.name_prefix` to the *specific* named entry would require
  parsing inside `locals` blocks at the individual-assignment level — a parser change,
  not a references.py change, and out of scope here. `local.X` references are expected
  to always fall into the "discard silently" bucket.
- `PineconeStore`, BM25, RRF, reranking, query rewriting, `pipeline.py`,
  `RetrievalConfig`-driven toggling, the FastAPI app — all still not built, unchanged
  from Day 3's non-goals.

## 10. Risks or ambiguities

- **`var.X` and `module.X.Y` references can resolve to real edges; `local.X` never
  can.** This is a natural, unplanned consequence of Day 2's addressing scheme: a
  `variable "region" {}` block got address `var.region` and a `module "vpc" {}` block
  got address `module.vpc` — both of which happen to exactly match Terraform's own
  reference syntax for those kinds, so `var.region` and `module.vpc.vpc_id` genuinely
  resolve and produce real, meaningful edges (not just resource-to-resource edges).
  `locals { ... }` blocks, by contrast, got address `locals:<file>:<line>` (no natural
  per-value address exists, since a single `locals` block defines many named values)
  — which can never match `local.<name>`'s reference syntax. Both outcomes are
  spec-compliant (section 9.2 explicitly allows silent non-matches), but worth
  understanding rather than assuming all non-resource references behave the same way.
- **`replace_edges`'s `DELETE` is redundant most of the time.** `resources`'s own
  cascade already wipes old edges when `replace_resources` deletes old resource rows.
  `replace_edges` still does its own `DELETE FROM edges WHERE repo_id = %s` for
  defensive correctness/idempotency if ever called independently of a fresh
  `index_repo()` — harmless, just worth knowing it's usually a no-op in the normal
  `index_repo` → `index_edges` sequence.
- **No unique constraint on `edges`.** Unlike `resources`'s `UNIQUE (repo_id,
  address)`, nothing in the schema stops duplicate `(source_id, target_id)` rows from
  being inserted — deduplication is entirely the `seen` set in `index_edges`. If a
  future change calls `db.replace_edges` directly with pre-duplicated rows (bypassing
  `index_edges`), nothing at the database layer will catch it.
- **New fixture directory, not an extension of Day 2's.** Deliberate, to avoid any
  risk of breaking Day 2/3's hardcoded block-count and address-set assertions — see
  5.4. Costs a bit of duplication (two small fixture repos instead of one) in exchange
  for zero cross-cycle breakage risk.
