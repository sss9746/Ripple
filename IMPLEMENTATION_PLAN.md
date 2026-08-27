# Implementation Plan — Day 7: Buffer and Consolidation

## 0. Process note for this cycle

Same as Days 5–6: **`SPEC.md` is read-only.** Nothing below proposes editing it.

This cycle is smaller and different in character from Days 1–6: SPEC.md's own Day 7
is a **buffer day**, not a new-feature day. Section 11 says, verbatim: "Absorb
slippage from Days 2 and 4. If on schedule: tests for the parser, reference
extractor, and tokenizer. **Done when:** everything from Week 1 runs from a clean
`docker compose up` plus one script."

This plan takes that literally rather than manufacturing busywork: it (1) closes the
one piece of real, explicitly-flagged technical debt this project has actually
accumulated, (2) adds a small number of *genuinely missing* regression tests for the
parser and reference extractor — not padding, since both are already well-covered —
and (3) actually verifies the "clean `docker compose up`" claim, which **no prior
day's acceptance check has ever done**, because every prior day's manual check ran
against the real Supabase-backed database (see section 3).

## 1. Objective

Absorb the one real piece of deferred technical debt on the books
(`PgVectorStore.query`'s missing `k <= 0` guard, explicitly flagged in Day 6's
review as "worth fixing at the source eventually; not done here"), add two small,
targeted regression tests closing genuine gaps in parser and reference-extractor
coverage, and — for the first time in this project — actually prove the system
reproduces from a clean `docker compose up` using the local `pgvector/pgvector:pg16`
fallback, not the Supabase instance every prior day's checks have quietly relied on.

This is SPEC.md's Day 7 milestone, sitting on top of Day 1–6, all already
implemented and verified (119 passing tests as of Day 6, commit `3400ff5`).

## 2. Relevant SPEC.md requirements

- Section 11, Day 7: "Absorb slippage from Days 2 and 4. If on schedule: tests for
  the parser, reference extractor, and tokenizer. **Done when:** everything from
  Week 1 runs from a clean `docker compose up` plus one script."
- Section 4 (Stack): "Docker Compose — local pgvector/pgvector:pg16 fallback — same
  `schema.sql`, same `DATABASE_URL` var, so `docker compose up` alone still
  reproduces the system from scratch without a Supabase or Pinecone account." This
  is the literal claim section 8's "Done when" is checking — see section 3 for why
  it has never actually been exercised.
- Section 12 (Risk register) named Days 2 and 4 as the two likely slippage sources
  when SPEC.md was written, before implementation started: "HCL line numbers (Day
  2)" and (implicitly, via the reference-extraction regex) Day 4's `REF_RE`. Both
  did in fact need real fixes — but those fixes already happened, during Day 2's and
  Day 4's *own* review cycles, not left outstanding. See section 3 for what's
  actually still open versus what's already closed.

## 3. Current implementation gaps — an honest accounting, not a mirror of section 2

**What SPEC.md named as likely Day 2/4 slippage is already closed**, not deferred:
- Day 2's line-range/heredoc/comment handling was hardened through its own review
  cycle (`tests/test_parser.py`'s `test_heredoc_comment_and_string_braces_do_not_end_block_early`,
  `test_block_bodies_match_exact_source_lines`, etc.) before Day 2 was ever marked
  done.
- Day 4's `REF_RE` had a real bug (the list-bracket-swallowing issue) caught and
  fixed during Day 4's own review, with regression tests
  (`test_does_not_include_surrounding_list_bracket`,
  `test_preserves_balanced_reference_index`) already in `tests/test_references.py`.

Pretending there's unresolved "Day 2/4 slippage" to absorb would be manufacturing
work SPEC.md didn't actually ask for. There is real, honestly-identifiable slippage
elsewhere, though:

- **`ripple/retrieval/pgvector_store.py`'s `PgVectorStore.query()` has no guard
  against `k <= 0`.** Verified directly against the real database in this planning
  pass: `SELECT 1 LIMIT 0` returns `[]` cleanly, but `SELECT 1 LIMIT -1` raises
  `psycopg.errors.InvalidRowCountInLimitClause: LIMIT must not be negative`. Day 6's
  `pipeline.py` works around this by never letting a non-positive `vector_k` reach
  `PgVectorStore.query` at all — but the guard was explicitly deferred at the
  *source*, in Day 6's own words: "worth fixing at the source eventually; not done
  here to keep this cycle's file list unchanged from the original Day 6 scope."
  `pgvector_store.py` has otherwise been untouched (and explicitly "do not modify")
  since Day 3. This cycle is the first one that reopens it, for this one fix only.
- **The `docker compose` reproducibility claim (section 4) has never actually been
  exercised.** Confirmed in this planning pass: `.env`'s `DATABASE_URL` points at a
  Supabase-hosted Postgres instance (`aws-0-us-west-2.pooler.supabase.com`), not the
  local `db` service `docker-compose.yml` defines on port `5434`. Every prior day's
  manual acceptance check ran against that real Supabase database. Docker itself
  isn't even running in the current environment (`docker ps` fails — no daemon
  socket). This means the specific claim section 4 makes — "`docker compose up`
  alone still reproduces the system from scratch" — is, as far as this project's own
  history shows, **untested**, not just unexercised recently. This is exactly what a
  buffer day should catch.
- **Minor, genuine test-coverage gaps** (not "missing tests" broadly — both areas are
  already well-covered): the parser has no test for two blocks with **zero blank
  lines between them** (every existing fixture has a blank line separating blocks,
  so an off-by-one bleed between adjacent blocks' `end_line`/next `start_line`
  wouldn't be caught); the reference extractor has no test for a **chained
  bracket-then-attribute** reference (`aws_instance.node[0].id` — an index access
  *followed by* a further attribute access), which is precisely the shape Day 4's
  `REF_RE` fix was about and deserves one more concrete regression case beyond what
  `test_preserves_balanced_reference_index` already covers (`module.vpc.private_subnets[0]`,
  which ends at the bracket rather than continuing past it).
- **The BM25 tokenizer needs no additional tests this cycle.** `tests/test_bm25.py`
  already has 20 tests covering casing, hyphens, short-part filtering, consecutive
  delimiters, terraform-syntax separators, the section-0a duplication fix, and
  `embed_text`-shaped multi-token strings. Padding this further would be busywork,
  not consolidation — noted explicitly so its absence from this plan isn't mistaken
  for an oversight.

## 4. Exact files Codex (or you) should create or modify

Modify:
- `ripple/retrieval/pgvector_store.py` — add a `k <= 0` guard to `PgVectorStore.query`
  (first change to this file since Day 3).
- `tests/test_pgvector_store.py` — add the corresponding regression test.
- `tests/test_parser.py` — add the adjacent-blocks-no-blank-line test.
- `tests/test_references.py` — add the chained bracket-then-attribute test.

Create: nothing. This is a consolidation cycle, not a new-module cycle.

Do not modify: everything else — `sql/schema.sql`, `docker-compose.yml`,
`.env.example`, `requirements.txt`, `ripple/config.py`, `ripple/ingest/*`,
`ripple/llm/*`, `ripple/retrieval/vector_store.py`, `ripple/retrieval/bm25.py`,
`ripple/retrieval/graph.py`, `ripple/retrieval/fusion.py`,
`ripple/retrieval/pipeline.py`, `scripts/index_repo.py`, `scripts/ask.py`, `SPEC.md`,
`AGENTS.md`, `CLAUDE.md`, `README.md`, `.env` (never touch real credentials — see
section 5, Step 3, for how the docker-compose check avoids this entirely), and every
other existing test file.

## 5. Step-by-step implementation order

### Step 1 — `PgVectorStore.query`'s `k <= 0` guard

```python
    def query(
        self,
        repo_id: int,
        embedding: list[float],
        k: int,
    ) -> list[RetrievedBlock]:
        if k <= 0:
            return []

        vector_param = Vector(embedding)
        # ... unchanged from here down
```

One line, at the top of the existing method, before `Vector(embedding)` is even
constructed. Mirrors `BM25Index.query`'s identical `k <= 0 -> []` convention from Day
5 exactly, so both `VectorStore` implementations now agree on what a non-positive `k`
means, independent of whether a caller goes through `pipeline.py` or calls
`PgVectorStore` directly.

**This does not make `pipeline.py`'s own `config.vector_k > 0` check redundant —
read this before removing anything.** `pipeline.py`'s check skips constructing an
`EmbeddingProvider` and making the OpenAI embedding call entirely when
`vector_k <= 0`; this new guard only protects the database call *after* an embedding
already exists. The two guards protect different costs (an API call vs. a malformed
SQL clause) and both stay exactly as they are — this is an additive fix to close the
gap for callers that bypass `pipeline.py`, not a refactor of `pipeline.py` itself.

The test for this guard belongs in `tests/test_pgvector_store.py`, and must prove the
short-circuit happens *before* any real work, not just that the return value happens
to be `[]`:

```python
@pytest.mark.parametrize("k", [0, -1])
def test_query_short_circuits_for_nonpositive_k(
    monkeypatch: pytest.MonkeyPatch,
    k: int,
) -> None:
    def _unexpected_vector(*args: object, **kwargs: object) -> None:
        raise AssertionError("Vector(...) must not be constructed for k <= 0")

    def _unexpected_connection(*args: object, **kwargs: object) -> None:
        raise AssertionError("db.get_connection() must not be called for k <= 0")

    monkeypatch.setattr(
        "ripple.retrieval.pgvector_store.Vector", _unexpected_vector
    )
    monkeypatch.setattr(db, "get_connection", _unexpected_connection)

    result = PgVectorStore().query(
        repo_id=1, embedding=[0.0] * EMBEDDING_DIM, k=k
    )

    assert result == []
```

This is an **offline unit test — no database needed at all**, which is a stronger
proof than a database-backed version would be: it shows `k <= 0` never reaches
`Vector(...)` or `db.get_connection()` in the first place, for *any* `repo_id` or
embedding, not just that a particular real query happened to come back empty. A
separate database-backed version would only prove a weaker fact this test already
implies, so none is added — closing this out as a real decision, not left open.
`Vector` must be patched via its string path (`"ripple.retrieval.pgvector_store.Vector"`)
since `pgvector_store.py` imports the name directly (`from pgvector import Vector`);
patching `pgvector.Vector` itself would not affect the already-bound reference in
`pgvector_store`'s own namespace. `db.get_connection` can be patched directly on the
already-imported `db` module, since `pgvector_store.py` calls `db.get_connection()`
by attribute lookup at call time, not a name captured at import time.

### Step 2 — Two targeted regression tests

`tests/test_parser.py` — adjacent blocks, no blank line between them:

```python
def test_adjacent_blocks_with_no_blank_line_have_disjoint_ranges(
    tmp_path: Path,
) -> None:
    source = (
        'resource "aws_vpc" "main" {\n'
        "  cidr_block = \"10.0.0.0/16\"\n"
        "}\n"
        'resource "aws_subnet" "public" {\n'
        "  vpc_id = aws_vpc.main.id\n"
        "}\n"
    )
    tf_file = tmp_path / "main.tf"
    tf_file.write_text(source)

    blocks = parse_file(tf_file, tmp_path)

    assert [(b.address, b.start_line, b.end_line) for b in blocks] == [
        ("aws_vpc.main", 1, 3),
        ("aws_subnet.public", 4, 6),
    ]
```

`tests/test_references.py` — a chained bracket-then-attribute reference:

```python
def test_extracts_reference_with_index_then_attribute() -> None:
    body = "value = aws_instance.node[0].private_ip"

    assert references.extract_references(body) == [
        "aws_instance.node[0].private_ip"
    ]
```

Both are small, offline, no fixtures needed — matching the existing style of both
test files (`test_invalid_hcl_raises_value_error` already writes an ad hoc file to
`tmp_path` rather than using the shared fixtures; this follows the same pattern
rather than risking the shared `sample_repo`/`reference_repo` fixtures other days'
tests depend on).

### Step 3 — The actual "clean `docker compose up`" check

**Read this section fully before running anything in it.** It involves
`docker compose down -v`. The whole point is to never let it touch the real
database, and to never touch the *normal* local `ripple` Compose project's own
containers or volume either — this check runs under its own, isolated Compose
project name, `ripple-day7-check`, specifically so `down -v` only ever deletes data
this check itself created.

`DATABASE_URL` is overridden **at the shell level, for individual commands only** —
never by editing the tracked `.env` file, and never by pointing anything at the real
Supabase instance. `psycopg`'s `load_dotenv()` (called in
`db.py`/`embeddings.py`/`generate.py`) does not override an already-set shell
environment variable, so this is both sufficient and safe — no code change needed.

This is written as one script, not a loose sequence of copy-pasted commands, for one
specific reason: **teardown must run on every exit path, success or failure**
(finding 7) — but teardown must never do more than the user actually consented to.
`trap cleanup EXIT` fires on *every* exit, including a declined confirmation, so
`cleanup()` cannot unconditionally run the isolated project's `down -v` — if it did,
declining that specific confirmation would still delete the isolated volume via the
trap, which is exactly backwards. `MANAGE_ISOLATED` tracks whether that consent was
actually given: it starts `false`, `cleanup()`'s isolated `down -v` is gated on it
being `true`, and it is only ever set `true` immediately before the confirmed `down
-v` in step 2 below actually runs. Every early exit before that point — Docker
unreachable, ports blocked, or the user declining any prompt up to and including
step 2's own confirmation — leaves `MANAGE_ISOLATED=false`, so `cleanup()` skips the
isolated teardown entirely in those cases; there is nothing to tear down that this
run itself created. The normal-service restoration logic is independent of this
flag and always runs if `STOPPED_NORMAL_SERVICES` is non-empty, regardless of how
far the script got afterward — stopping those services has its own, separate
confirmation (step 1), and restoring them is never conditional on what happens to
the isolated project.

**Two corrections below, both found by actually running this script for the first
time — read this before assuming the script as previously written was fine.** The
underlying acceptance check itself passed: `index_repo.py` and `ask.py` genuinely
reproduced the full pipeline from a clean, isolated local `docker compose up`. But
getting there required two manual workarounds that the script should have handled
itself:

- **`PYTHON_BIN`, not a bare `python`/`python3`.** The first run failed outright with
  `python: command not found` — this machine has no `python` on `PATH`, only
  `python3`, and even `python3` alone would bypass the project's virtualenv
  dependencies (`psycopg`, `openai`, etc. live in `.venv`, not the system
  interpreter). `PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"`, validated with an
  executable check before anything else runs, fixes both problems at once and lets
  the variable be overridden (`PYTHON_BIN=python3.11 ...`) if a project's virtualenv
  ever lives somewhere other than `.venv`.
- **Line-by-line service capture, not `read -a` or `mapfile`.** The first run's
  `NORMAL_RUNNING` was two lines (`adminer` and `db`, since the normal project has
  both services running), but `read -r -a STOPPED_NORMAL_SERVICES <<< "$NORMAL_RUNNING"`
  only reads the *first* line into the array — `db` was silently dropped, and the
  printed "Restoring normal ripple service(s) this run stopped: adminer" message
  only ever named one of the two. In that actual run, `db` still came back up
  anyway — but only as a side effect of `docker-compose.yml`'s `adminer: depends_on:
  [db]`, which made `docker compose start adminer` revive `db` too. **Restoration
  must not rely on that coincidence** — two services with no `depends_on`
  relationship between them would leave one orphaned in a stopped state. `mapfile`
  would read every line correctly, but stock macOS ships bash 3.2, which has no
  `mapfile`/`readarray` at all — so this uses a `while IFS= read -r service; do ...
  done <<< "$NORMAL_RUNNING"` loop instead, which is bash-3.2-compatible and reads
  every line.

```bash
#!/usr/bin/env bash
set -euo pipefail

PROJECT="ripple-day7-check"
LOCAL_DATABASE_URL="postgresql://ripple:ripple@localhost:5434/ripple"
REPO_NAME="day7-docker-check"
PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
STOPPED_NORMAL_SERVICES=()
MANAGE_ISOLATED=false

if [ ! -x "$PYTHON_BIN" ]; then
  echo "Python virtual environment not found at $PYTHON_BIN."
  echo "Create/activate the project virtual environment or set PYTHON_BIN explicitly."
  exit 1
fi

cleanup() {
  if [ "$MANAGE_ISOLATED" = true ]; then
    echo "--- Tearing down isolated project ($PROJECT) ---"
    docker compose -p "$PROJECT" down -v || true
  fi
  if [ "${#STOPPED_NORMAL_SERVICES[@]}" -gt 0 ]; then
    echo "--- Restoring normal ripple service(s) this run stopped: ${STOPPED_NORMAL_SERVICES[*]} ---"
    docker compose start "${STOPPED_NORMAL_SERVICES[@]}" || true
  fi
}
trap cleanup EXIT

# 0. Docker prerequisite. Start Docker Desktop first if this fails, then re-run.
if ! docker info >/dev/null 2>&1; then
  echo "Docker daemon is not reachable. Start Docker Desktop and retry."
  echo "Day 7 acceptance cannot run until Docker is available."
  exit 1
fi

# 1. Port-conflict precondition. docker-compose.yml hardcodes 5434/8080 (not
#    parameterized; this plan does not add an override), so the isolated project
#    still needs these two host ports free.
if lsof -i :5434 -sTCP:LISTEN >/dev/null 2>&1 || lsof -i :8080 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "Port 5434 and/or 8080 is already in use:"
  lsof -i :5434 -i :8080 -sTCP:LISTEN

  NORMAL_RUNNING=$(docker compose ps --services --status running 2>/dev/null || true)
  if [ -n "$NORMAL_RUNNING" ]; then
    echo
    echo "The normal (non-isolated) ripple Compose project appears to own these"
    echo "ports -- currently running: $NORMAL_RUNNING"
    read -p "Stop these normal ripple services reversibly (no -v) so the isolated check can bind the ports? [y/N] " CONFIRM_STOP
    if [ "$CONFIRM_STOP" != "y" ]; then
      echo "Aborting -- ports are not free and stopping was not confirmed."
      exit 1
    fi
    # Bash-3.2-compatible line-by-line capture (stock macOS ships bash 3.2,
    # which has no `mapfile`/`readarray`). `read -a` reads only a single line
    # and would silently drop every service after the first whenever
    # $NORMAL_RUNNING has more than one line -- this loop reads every line.
    while IFS= read -r service; do
      if [ -n "$service" ]; then
        STOPPED_NORMAL_SERVICES+=("$service")
      fi
    done <<< "$NORMAL_RUNNING"
    docker compose stop
  else
    echo
    echo "Ports 5434/8080 are held by something other than the normal ripple"
    echo "project. Stop here and resolve that yourself -- this check will not"
    echo "try to identify or terminate an unrelated process."
    exit 1
  fi
fi

# 2. Confirm before deleting the ISOLATED project's own volume (created fresh in
#    step 3 below). This cannot touch the normal ripple project's pgdata volume or
#    the Supabase database -- `-p` gives it entirely separate containers/volumes.
read -p "About to run 'docker compose -p $PROJECT down -v' (isolated project only). Continue? [y/N] " CONFIRM_DOWN
if [ "$CONFIRM_DOWN" != "y" ]; then
  echo "Aborting before any destructive command ran."
  exit 1
fi
# Consent is now given -- only from this point on may cleanup() delete the
# isolated project's volume on exit (including on a later failure).
MANAGE_ISOLATED=true
docker compose -p "$PROJECT" down -v

# 3. Bring up a genuinely fresh, isolated local Postgres + pgvector.
docker compose -p "$PROJECT" up -d

# 4. Bounded readiness wait -- docker-compose.yml has no healthcheck, so `up -d`
#    returning does not mean the database is ready. 30 attempts, 1 second apart;
#    never loop forever.
READY=false
for attempt in $(seq 1 30); do
  if docker compose -p "$PROJECT" exec -T db pg_isready -U ripple >/dev/null 2>&1; then
    READY=true
    break
  fi
  sleep 1
done
if [ "$READY" != "true" ]; then
  echo "Postgres did not become ready after 30 attempts. Container logs:"
  docker compose -p "$PROJECT" logs db
  echo "Day 7 acceptance FAILED: local database never became ready."
  exit 1
fi

# 5. Deterministic schema verification -- query for the four expected tables and
#    the vector extension, rather than eyeballing `\dt`.
DATABASE_URL="$LOCAL_DATABASE_URL" "$PYTHON_BIN" -c "
from ripple import db

with db.get_connection() as conn, conn.cursor() as cur:
    cur.execute(
        \"SELECT table_name FROM information_schema.tables \"
        \"WHERE table_schema = 'public'\"
    )
    tables = {row[0] for row in cur.fetchall()}
    cur.execute(\"SELECT extname FROM pg_extension WHERE extname = 'vector'\")
    has_vector_extension = cur.fetchone() is not None

expected_tables = {'repos', 'resources', 'edges', 'query_logs'}
missing = expected_tables - tables
assert not missing, f'missing tables: {missing}'
assert has_vector_extension, 'vector extension is not installed'
print('schema OK:', sorted(expected_tables), '| vector extension: OK')
"

# 6. Register and index a small repo against the LOCAL, isolated database only,
#    using the real CLI script. Confirm before this line -- it makes one real
#    OpenAI embedding request (one batched call covering every block parsed from
#    the fixture).
read -p "Continue with indexing (makes one real OpenAI embedding request)? [y/N] " CONFIRM_INDEX
if [ "$CONFIRM_INDEX" != "y" ]; then
  echo "Aborting before any OpenAI call was made."
  exit 1
fi
INDEX_OUTPUT=$(DATABASE_URL="$LOCAL_DATABASE_URL" \
  "$PYTHON_BIN" scripts/index_repo.py tests/fixtures/reference_repo --name "$REPO_NAME")
echo "$INDEX_OUTPUT"

# Prefer the id index_repo.py actually printed. Repo names are not unique in the
# schema, so if that parse ever fails, fall back to the most recently created row
# with this name (ORDER BY id DESC LIMIT 1) rather than an ambiguous bare lookup.
#
# The `|| true` is required, not decorative: under `set -o pipefail`, if grep
# finds no match it exits 1, and that non-zero status propagates as the whole
# pipeline's exit status even though `head`/`cut` themselves exit 0. Without
# `|| true`, `set -e` would treat that as this assignment "failing" and kill the
# script right here -- before the fallback query below ever gets a chance to run.
# `|| true` only affects whether the *script* treats this line as fatal; grep's
# (empty) stdout is still captured into REPO_ID either way, so the `-z` check
# below still correctly detects "no match" and proceeds to the fallback.
REPO_ID=$(echo "$INDEX_OUTPUT" | grep -oE 'id=[0-9]+' | head -1 | cut -d= -f2) || true
if [ -z "$REPO_ID" ]; then
  echo "Could not parse repo id from index_repo.py output; querying for it instead."
  REPO_ID=$(DATABASE_URL="$LOCAL_DATABASE_URL" "$PYTHON_BIN" -c "
from ripple import db

with db.get_connection() as conn, conn.cursor() as cur:
    cur.execute(
        \"SELECT id FROM repos WHERE name = '$REPO_NAME' ORDER BY id DESC LIMIT 1\"
    )
    row = cur.fetchone()
    print(row[0] if row else '')
")
fi
if [ -z "$REPO_ID" ]; then
  echo "Day 7 acceptance FAILED: could not determine repo id after indexing."
  exit 1
fi
echo "Using repo_id=$REPO_ID"

# 7. Confirm resources were actually indexed and at least one reference edge was
#    extracted -- proving Day 2 and Day 4's work reproduces here too, not just
#    that the repos row exists.
DATABASE_URL="$LOCAL_DATABASE_URL" "$PYTHON_BIN" -c "
from ripple import db

repo_id = $REPO_ID
with db.get_connection() as conn, conn.cursor() as cur:
    cur.execute('SELECT count(*) FROM resources WHERE repo_id = %s', (repo_id,))
    resource_count = cur.fetchone()[0]
    cur.execute('SELECT count(*) FROM edges WHERE repo_id = %s', (repo_id,))
    edge_count = cur.fetchone()[0]

print('resources:', resource_count, '| edges:', edge_count)
assert resource_count > 0, 'no resources were indexed'
assert edge_count > 0, 'no reference edges were extracted'
"

# 8. Ask a real question through the actual CLI script. Confirm before this line --
#    it makes a second OpenAI embedding request (the question) plus one generation
#    request.
read -p "Continue with asking (one more embedding request plus one generation request)? [y/N] " CONFIRM_ASK
if [ "$CONFIRM_ASK" != "y" ]; then
  echo "Aborting before the generation call was made."
  exit 1
fi
DATABASE_URL="$LOCAL_DATABASE_URL" \
  "$PYTHON_BIN" scripts/ask.py "$REPO_ID" "What does aws_vpc.main create?"

# 9. Strengthened query-log check -- prove the system actually retrieved evidence
#    and generated an answer, not merely that the expected JSON keys exist.
DATABASE_URL="$LOCAL_DATABASE_URL" "$PYTHON_BIN" -c "
from ripple import db

repo_id = $REPO_ID
with db.get_connection() as conn, conn.cursor() as cur:
    cur.execute(
        'SELECT stages_json, answer FROM query_logs WHERE repo_id = %s '
        'ORDER BY id DESC LIMIT 1',
        (repo_id,),
    )
    row = cur.fetchone()

assert row is not None, 'no query_logs row was written'
stages, answer = row

assert answer is not None and answer.strip(), 'answer is empty or NULL'
assert len(stages.get('final', [])) >= 1, 'final stage has no results'
for stage_name in ('vector', 'bm25', 'fusion'):
    assert stages.get(stage_name), f'{stage_name} stage is missing or empty'

print('query_logs OK -- answer present, final/vector/bm25/fusion all non-empty')
"

echo "Day 7 local docker-compose acceptance check PASSED."
# `cleanup` (isolated teardown, then restoring any normal services) runs
# automatically here via the EXIT trap -- nothing further to do.
```

**Cost, stated precisely:** step 6 (indexing) makes one embedding API request (the
fixture's few blocks batch into a single call); step 8 (asking) makes a second
embedding request (the question itself) plus one generation request. Total:
**approximately two embedding API requests and one generation API request** — the
same small-cost category as every prior day's manual checks.

**If the script exits non-zero at any point — Docker unreachable, ports blocked,
readiness timeout, missing tables/extension, zero resources or edges, or a failed
query-log assertion — that is section 4's reproducibility claim genuinely not
holding. Day 7 cannot be marked complete in that case.** Report the actual failure
message the script printed and decide on a fix as a separate, deliberate step. Do
not silently patch `schema.sql` or `docker-compose.yml` to make the check pass — if
the fix turns out to belong there, that is a new decision to make explicitly, not
something to slip into this consolidation cycle's diff. Teardown still runs via the
trap regardless of where the script stopped — normal-project services (if any were
stopped) are restored, and the isolated project's volume is always removed, on
every failure path as well as on success.

## 6. Interfaces, data structures, and error behavior

- `PgVectorStore.query(repo_id, embedding, k)` — now returns `[]` immediately for
  `k <= 0`, without constructing a `Vector` or making any database call. Behavior for
  `k > 0` is completely unchanged. This makes `PgVectorStore` and `BM25Index` (Day 5)
  agree exactly on non-positive-`k` semantics.
- No other public interface changes this cycle. `parse_file`, `extract_references`,
  `pipeline.run_pipeline`, `ask()` — all unchanged; only new test coverage is added
  around them.

## 7. Required tests

- `tests/test_pgvector_store.py` — add `test_query_short_circuits_for_nonpositive_k`
  (Step 1's code block), parametrized over `k=0` and `k=-1` — **2 collected test
  items**. Fully offline: proves via monkeypatches that neither `Vector(...)` nor
  `db.get_connection()` is ever reached, not just that the return value happens to be
  `[]`. No database-backed counterpart is added (Step 1 explains why one wouldn't add
  meaningful separate coverage).
- `tests/test_parser.py` — the adjacent-blocks test (Step 2) — **1 collected test
  item** — proving no off-by-one bleed between two blocks separated by nothing but a
  newline.
- `tests/test_references.py` — the chained-bracket-then-attribute test (Step 2) —
  **1 collected test item**.
- **Full-suite regression check**: `python -m pytest` must still show all
  pre-existing tests passing, plus these 4 new collected items: **119 before this
  cycle, 123 after** (119 + 2 parametrized `k` cases + 1 parser test + 1 references
  test). This cycle adds tests and fixes one function; it does not change behavior
  anywhere else, so nothing else should move.

Run `python -m pytest` after implementation. **Every test this cycle adds is fully
offline** — no `OPENAI_API_KEY`, no real network, and (per the redesign above) no
database connection either, including the `PgVectorStore` test, which used to need
one and no longer does.

## 8. Acceptance criteria

- `python -m pytest` passes with no failures, full suite, **123 tests** (119 + 2
  parametrized `PgVectorStore` cases + 1 parser test + 1 references test).
- `PgVectorStore().query(repo_id, embedding, k=0)` and `k=-1` both return `[]`
  **without ever calling `Vector(...)` or `db.get_connection()`** — proven by
  monkeypatches that raise if either is attempted (Step 1), not merely by observing
  an empty return value.
- **The literal Day 7 "Done when," run for the first time this project has ever run
  it — read section 5, Step 3 in full before attempting, including its Docker
  prerequisite and port-conflict precondition:** with Docker Desktop running
  (`docker info` reachable) and using an isolated Compose project
  (`ripple-day7-check`, never the normal `ripple` project's own containers/volume),
  a fresh `docker compose up` (local `pgvector/pgvector:pg16`, not Supabase), waited
  on with a *bounded* readiness check (30 attempts, not forever), produces all 4
  tables *and* the `vector` extension — verified by querying
  `information_schema.tables`/`pg_extension` directly, not by eyeballing `\dt`.
  `"$PYTHON_BIN" scripts/index_repo.py` and `"$PYTHON_BIN" scripts/ask.py` — the
  actual CLI scripts, not `-c` shortcuts, run through the project's own virtualenv
  interpreter — both pointed at that local database via a shell-level `DATABASE_URL`
  override, successfully index a repo (verified:
  `resources` count > 0 *and* `edges` count > 0, proving Day 2 and Day 4's work
  reproduces here too) and answer a question (verified: a `query_logs` row exists
  whose `stages_json["final"]` has at least one result, whose `vector`/`bm25`/
  `fusion` stages are each present *and non-empty*, and whose `answer` column is a
  non-empty string — proving the system actually retrieved evidence and generated a
  real answer, not just that some JSON keys happen to exist) — end to end, from a
  genuinely empty database, using only `docker compose up` plus the two existing
  scripts. The repo id used throughout comes from `index_repo.py`'s own printed
  output (with a name-based fallback query that explicitly handles non-unique
  names via `ORDER BY id DESC LIMIT 1`), never a hardcoded id.
  **Report the actual result. If any part of this fails, Day 7 is not complete** —
  see Step 3's closing paragraph for what to do instead of silently patching
  `schema.sql`/`docker-compose.yml` to force a pass. Do not assume success because
  the Supabase path has worked all along; those are different databases running the
  same `schema.sql`, and this is the first time the local one has been exercised.
  Teardown (isolated project's volume, plus restoring any normal-project services
  this run stopped) happens automatically on every exit path, success or failure,
  via the script's `trap`-based cleanup.

**This check has, in fact, already passed once** — with the pre-`PYTHON_BIN` script,
requiring a manual `python` → `python3` substitution to get past step 6, and manual
confirmation afterward that both normal services (`db` and `adminer`) had genuinely
come back up rather than trusting the script's own restoration message. `index_repo.py`
reported "Indexed 7 resource blocks, Extracted 5 reference edges" and `ask.py`
returned a real, correctly-cited answer about `aws_vpc.main`; the query-log check
passed. **Day 7 is not being marked incomplete** — the two fixes above are
portability corrections to the script itself, so the next run doesn't need that
manual intervention, not evidence the underlying check ever failed.

## 9. Explicit non-goals

- Any new feature, module, or pipeline stage. Day 7 is buffer/consolidation per
  SPEC.md itself — Day 8 (benchmark construction) is next.
- Manufacturing "Day 2/4 slippage" that doesn't actually exist. Section 3 explains
  why both were already closed during their own review cycles.
- Adding more tokenizer/BM25 tests. Already thoroughly covered (section 3).
- Fixing `.env` to point at the local docker-compose database, or vice versa.
  Whichever database this project uses day to day is a separate decision from
  proving the *fallback* path works — this cycle only proves the fallback works, it
  doesn't switch to it.
- Any change to `docker-compose.yml` or `sql/schema.sql` — section 5, Step 3's check
  is about *verifying* the existing setup, not modifying it. If the check fails,
  that's a finding to report and decide on separately, not something to silently
  patch mid-cycle.
- Modifying `SPEC.md`.

## 10. Risks, ambiguities, and things flagged for your review

- **"One script" (section 11's exact wording) is slightly ambiguous** — by Day 7,
  the project has *two* scripts (`index_repo.py` since Day 1, `ask.py` since Day 3).
  Read as "each capability is reachable via a single script invocation" (index via
  one script, ask via another), not "the whole project has exactly one script,"
  since the latter reading would contradict Day 3's own deliverable. Flagged rather
  than silently assumed, since it's SPEC.md's phrasing, not this plan's.
- **The docker-compose check has now actually been run and passed** (section 8) —
  this section is no longer a prediction. The run that first exposed the two bugs
  this revision fixes (bare `python` not on `PATH`; `db` silently dropped from
  `STOPPED_NORMAL_SERVICES`) is the same run whose `index_repo.py`/`ask.py` output
  is quoted in section 8 — the acceptance check passed despite those two script bugs,
  because they were worked around manually rather than being failures of the
  underlying system.
- **If the docker-compose check fails**, the likely causes, roughly in order of
  probability: (a) `schema.sql`'s `CREATE EXTENSION IF NOT EXISTS vector` requires
  the `pgvector/pgvector:pg16` image specifically (already the image
  `docker-compose.yml` uses, so low risk); (b) port `5434`/`8080` already bound —
  Step 3's precondition check (`lsof`) catches this before the isolated project
  attempts to start, distinguishes the normal `ripple` project (asks to stop it
  reversibly, records exactly which services, restores only those) from anything
  else (stops and asks the user rather than touching an unrelated process); (c) a
  real drift between `schema.sql` and whatever the live Supabase schema actually
  looks like today, if anything was ever changed by hand against Supabase directly
  rather than through `schema.sql`. (c) would be the most important finding of this
  entire cycle if it happens, since it would mean the two databases this project can
  run against have silently diverged.
- **The isolated Compose project (`ripple-day7-check`) still shares host ports with
  the normal `ripple` project**, since `docker-compose.yml`'s port mappings aren't
  parameterized and this plan doesn't add an override file to change that. Isolation
  here means separate containers/volumes, not separate ports — Step 3's precondition
  check and reversible, service-scoped `stop`/`start` around the normal project is
  how that's handled without modifying `docker-compose.yml`.
- **Step 3's script uses `read -p` for its confirmation gates, which requires an
  interactive terminal.** If this is ever run non-interactively (piped, or invoked
  by an agent without a TTY attached), `read -p` will block waiting for input rather
  than proceeding or failing fast. This is intentional — every gated point spends
  real money or runs a destructive command, so silently defaulting to "yes" when
  unattended would be worse than blocking. Run it interactively.
- **The `k <= 0` fix reopens a file every prior day explicitly marked "do not
  modify."** That restriction existed to keep each day's diff scoped; Day 7's whole
  purpose is closing exactly this kind of deliberately-deferred item, so reopening it
  now — for this one line only — is the plan working as intended, not a violation of
  the pattern.
