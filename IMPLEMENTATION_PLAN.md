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

**Precondition — port conflicts.** `docker-compose.yml` hardcodes host ports `5434`
and `8080`; they are not parameterized, and this plan does not modify
`docker-compose.yml` to add an override. An isolated *project name* does not imply
isolated *ports* — if the normal `ripple` Compose project is already running and
holding those ports, this check's `up -d` will fail to bind them.

```bash
# Check first. Do not skip this.
lsof -i :5434 -i :8080
```

- If nothing is listening: proceed directly to step 1.
- If the **normal** `ripple` project's own `db`/`adminer` containers hold the ports:
  stop them *reversibly* — `docker compose stop` (no `-v`, so their `pgdata` volume
  is untouched) — before continuing, and restart them (`docker compose start`) after
  this check's teardown (last step below).
- If something unrelated holds the ports: resolve that separately. Do not proceed by
  changing `docker-compose.yml`'s port mappings.

```bash
# 1. Confirm before this line — it deletes the ISOLATED project's own volume only
#    (created fresh in step 2 below). It cannot touch the normal `ripple` project's
#    `pgdata` volume or the Supabase database, since -p gives it entirely separate
#    containers and volumes under the hood.
docker compose -p ripple-day7-check down -v

# 2. Bring up a genuinely fresh, isolated local Postgres + pgvector.
docker compose -p ripple-day7-check up -d

# 3. Wait for Postgres to actually accept connections. docker-compose.yml has no
#    healthcheck, so `up -d` returning does not mean the database is ready yet.
until docker compose -p ripple-day7-check exec -T db pg_isready -U ripple; do
  sleep 1
done

# 4. Verify the schema actually applied: all 4 tables, AND the vector extension
#    specifically -- `\dt` alone does not prove the extension exists.
docker compose -p ripple-day7-check exec -T db \
  psql -U ripple -d ripple -c '\dt'
docker compose -p ripple-day7-check exec -T db \
  psql -U ripple -d ripple -c \
  "SELECT extname FROM pg_extension WHERE extname = 'vector';"

# 5. Register and index a small repo against the LOCAL, isolated database only,
#    using the real CLI script. Confirm before this line -- it makes one real
#    OpenAI embedding request (one batched call covering every block parsed from
#    the fixture).
DATABASE_URL=postgresql://ripple:ripple@localhost:5434/ripple \
  python scripts/index_repo.py tests/fixtures/reference_repo --name day7-docker-check
```

Note the `Registered repo id=<N> ...` line this prints. **Do not assume `<N>` is
`1`** — a fresh database still auto-increments from whatever `schema.sql`'s `SERIAL`
sequence starts at, and nothing guarantees this is the very first row ever inserted
(e.g. if this check is ever re-run after only `stop`/`start`, not `down -v`). If the
printed id wasn't captured, recover it independently instead of guessing:

```bash
docker compose -p ripple-day7-check exec -T db psql -U ripple -d ripple -t -c \
  "SELECT id FROM repos WHERE name = 'day7-docker-check';"
```

```bash
# 6. Confirm resources were actually indexed and at least one reference edge was
#    extracted -- proving Day 2 and Day 4's work reproduces here too, not just that
#    the repos row exists. Substitute the real id from step 5 for <REPO_ID>.
DATABASE_URL=postgresql://ripple:ripple@localhost:5434/ripple python3 -c "
from ripple import db
repo_id = <REPO_ID>
with db.get_connection() as conn, conn.cursor() as cur:
    cur.execute('SELECT count(*) FROM resources WHERE repo_id = %s', (repo_id,))
    resource_count = cur.fetchone()[0]
    cur.execute('SELECT count(*) FROM edges WHERE repo_id = %s', (repo_id,))
    edge_count = cur.fetchone()[0]
print('resources:', resource_count, '| edges:', edge_count)
assert resource_count > 0 and edge_count > 0
"

# 7. Ask a real question through the actual CLI script. Confirm before this line --
#    it makes a second OpenAI embedding request (the question) plus one generation
#    request.
DATABASE_URL=postgresql://ripple:ripple@localhost:5434/ripple \
  python scripts/ask.py <REPO_ID> "What does aws_vpc.main create?"

# 8. Confirm ask() actually wrote a query_logs row, and that its stages_json shows
#    the Day 6 pipeline actually ran vector, bm25, fusion, and produced a final list
#    -- not just that some row exists.
DATABASE_URL=postgresql://ripple:ripple@localhost:5434/ripple python3 -c "
from ripple import db
repo_id = <REPO_ID>
with db.get_connection() as conn, conn.cursor() as cur:
    cur.execute(
        'SELECT stages_json FROM query_logs WHERE repo_id = %s '
        'ORDER BY id DESC LIMIT 1',
        (repo_id,),
    )
    row = cur.fetchone()
assert row is not None, 'no query_logs row was written'
stages = row[0]
print(sorted(stages.keys()))
assert {'vector', 'bm25', 'fusion', 'final'} <= set(stages.keys())
"

# 9. Teardown -- always run this, isolated project only, never the normal one.
docker compose -p ripple-day7-check down -v
```

If the precondition check required stopping the normal project's own containers to
free the ports, restart them now: `docker compose start`.

**Cost, stated precisely (not "one embedding call"):** step 5 (indexing) makes one
embedding API request (the fixture's few blocks batch into a single call); step 7
(asking) makes a second embedding request (the question itself) plus one generation
request. Total: **approximately two embedding API requests and one generation API
request** — the same small-cost category as every prior day's manual checks, just
counted correctly this time.

**If any of steps 4, 6, or 8 fail — fewer than 4 tables, the extension missing, zero
resources or zero edges after indexing, or no query_logs row / missing stage keys
after asking — that is section 4's reproducibility claim genuinely not holding. Day 7
cannot be marked complete in that case.** Report the actual failure and decide on a
fix as a separate, deliberate step. Do not silently patch `schema.sql` or
`docker-compose.yml` to make the check pass — if the fix turns out to belong there,
that is a new decision to make explicitly, not something to slip into this
consolidation cycle's diff.

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
  it — read section 5, Step 3 in full before attempting, including its port-conflict
  precondition:** using an isolated Compose project (`ripple-day7-check`, never the
  normal `ripple` project's own containers/volume), a fresh `docker compose up`
  (local `pgvector/pgvector:pg16`, not Supabase) auto-applies `schema.sql` to
  produce all 4 tables *and* the `vector` extension; `python scripts/index_repo.py`
  and `python scripts/ask.py` — the actual CLI scripts, not `python -c` shortcuts —
  both pointed at that local database via a shell-level `DATABASE_URL` override,
  successfully index a repo (verified: `resources` count > 0 *and* `edges` count >
  0, proving Day 2 and Day 4's work reproduces here too) and answer a question
  (verified: a `query_logs` row exists whose `stages_json` contains `vector`,
  `bm25`, `fusion`, and `final` keys, proving Day 6's pipeline actually ran, not
  just that some answer text came back) — end to end, from a genuinely empty
  database, using only `docker compose up` plus the two existing scripts.
  **Report the actual result. If any part of this fails, Day 7 is not complete** —
  see Step 3's closing paragraph for what to do instead of silently patching
  `schema.sql`/`docker-compose.yml` to force a pass. Do not assume success because
  the Supabase path has worked all along; those are different databases running the
  same `schema.sql`, and this is the first time the local one has been exercised.

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
- **The docker-compose check is genuinely unverified as of this plan being written**
  — this section itself is a prediction of what *should* happen given `schema.sql`
  and the codebase's own DB calls, not a report of a check already run. Section 8
  explicitly asks for the real result, whichever way it comes out.
- **If the docker-compose check fails**, the likely causes, roughly in order of
  probability: (a) `schema.sql`'s `CREATE EXTENSION IF NOT EXISTS vector` requires
  the `pgvector/pgvector:pg16` image specifically (already the image
  `docker-compose.yml` uses, so low risk); (b) port `5434`/`8080` already bound by
  the normal `ripple` project or something else — Step 3's precondition check
  (`lsof`) catches this *before* the isolated project attempts to start, but if it's
  something other than the normal project holding the port, that still needs
  separate resolution; (c) a real drift between `schema.sql` and whatever the live
  Supabase schema actually looks like today, if anything was ever changed by hand
  against Supabase directly rather than through `schema.sql`. (c) would be the most
  important finding of this entire cycle if it happens, since it would mean the two
  databases this project can run against have silently diverged.
- **The isolated Compose project (`ripple-day7-check`) still shares host ports with
  the normal `ripple` project**, since `docker-compose.yml`'s port mappings aren't
  parameterized and this plan doesn't add an override file to change that. Isolation
  here means separate containers/volumes, not separate ports — Step 3's precondition
  check and reversible `stop`/`start` around the normal project is how that's
  handled without modifying `docker-compose.yml`.
- **The `k <= 0` fix reopens a file every prior day explicitly marked "do not
  modify."** That restriction existed to keep each day's diff scoped; Day 7's whole
  purpose is closing exactly this kind of deliberately-deferred item, so reopening it
  now — for this one line only — is the plan working as intended, not a violation of
  the pattern.
