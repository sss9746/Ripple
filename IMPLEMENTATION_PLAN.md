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

**Read this section fully before running anything in it — it involves `docker
compose down -v`, and the whole point is to never let it touch the real database.**

The check must run against the **local** `pgvector/pgvector:pg16` container
`docker-compose.yml` defines, with `DATABASE_URL` overridden **at the shell level for
that command only** — never by editing the tracked `.env` file, and never by pointing
anything at the real Supabase instance. `psycopg`'s `load_dotenv()` (called in
`db.py`/`embeddings.py`/`generate.py`) does not override an already-set shell
environment variable, so a shell-level override is both sufficient and safe — no code
change is needed to support this.

```bash
# 1. Confirm before this line specifically — it removes the LOCAL container's
#    volume. It does not touch .env or the Supabase database in any way, since
#    docker-compose.yml only ever manages its own `db`/`adminer` containers, which
#    are entirely separate infrastructure from the DATABASE_URL this project has
#    actually been using.
docker compose down -v

# 2. Bring up a genuinely fresh local Postgres + pgvector.
docker compose up -d

# 3. Confirm the schema applied automatically (all 4 tables, vector extension).
docker exec -it $(docker compose ps -q db) \
  psql -U ripple -d ripple -c '\dt'

# 4. Register and index a small, free-to-embed-cheaply repo against the LOCAL
#    database only, via a one-shot shell-level DATABASE_URL override:
DATABASE_URL=postgresql://ripple:ripple@localhost:5434/ripple \
  python scripts/index_repo.py tests/fixtures/reference_repo --name day7-docker-check

# 5. Ask it a question, against the same local database:
DATABASE_URL=postgresql://ripple:ripple@localhost:5434/ripple \
  python -c "from scripts.ask import ask; print(ask(1, 'What does aws_vpc.main create?'))"
```

Step 4/5 make one small, real OpenAI embedding call and one real generation call —
the same category of "small, unavoidable cost" as every prior day's manual checks.
**Confirm before running Step 4 onward**, same convention as Days 3/5/6.

If step 3 shows fewer than 4 tables, or steps 4–5 fail, that is section 4's
reproducibility claim genuinely not holding — a real finding to report, not a
process error to paper over.

## 6. Interfaces, data structures, and error behavior

- `PgVectorStore.query(repo_id, embedding, k)` — now returns `[]` immediately for
  `k <= 0`, without constructing a `Vector` or making any database call. Behavior for
  `k > 0` is completely unchanged. This makes `PgVectorStore` and `BM25Index` (Day 5)
  agree exactly on non-positive-`k` semantics.
- No other public interface changes this cycle. `parse_file`, `extract_references`,
  `pipeline.run_pipeline`, `ask()` — all unchanged; only new test coverage is added
  around them.

## 7. Required tests

- `tests/test_pgvector_store.py` — add
  `test_query_returns_empty_list_for_nonpositive_k`, parametrized over `k=0` and
  `k=-1`, against a repo with real indexed rows (so a bug that let the query through
  would visibly return non-empty results, not coincidentally return `[]` because the
  repo was empty). Assert `PgVectorStore().query(repo_id, embedding, k) == []` for
  both values, and — for `k=-1` specifically — that no `psycopg` exception is raised
  (the direct regression test proving the negative-`LIMIT` PostgreSQL error is now
  avoided, not just coincidentally not hit).
- `tests/test_parser.py` — the adjacent-blocks test (Step 2), proving no off-by-one
  bleed between two blocks separated by nothing but a newline.
- `tests/test_references.py` — the chained-bracket-then-attribute test (Step 2).
- **Full-suite regression check**: `python -m pytest` must still show all
  pre-existing tests passing (119 before this cycle, 122 after) — this cycle adds
  tests and fixes one function; it does not change behavior anywhere else, so nothing
  else should move.

Run `python -m pytest` after implementation. All of this cycle's own tests are
offline (no `OPENAI_API_KEY`, no real network) except
`test_query_returns_empty_list_for_nonpositive_k`, which needs a reachable Postgres
(skip-if-unreachable, same convention as every prior day) but no OpenAI access
(embeddings for its indexed rows use the existing `_FakeEmbeddingProvider` pattern).

## 8. Acceptance criteria

- `python -m pytest` passes with no failures, full suite, 122 tests.
- `PgVectorStore().query(repo_id, embedding, k=0)` and `k=-1` both return `[]` with no
  exception, verified against a repo with real rows.
- **The literal Day 7 "Done when," run for the first time this project has ever run
  it — read section 5, Step 3's safety note before attempting:** a fresh
  `docker compose up` (local `pgvector/pgvector:pg16`, not Supabase) auto-applies
  `schema.sql` to produce all 4 tables; `scripts/index_repo.py` and then `ask()`,
  both pointed at that local database via a shell-level `DATABASE_URL` override,
  successfully index a repo and answer a question — end to end, from a genuinely
  empty database, using only `docker compose up` plus the two existing scripts.
  Report the actual result (pass or fail) rather than assuming it will work because
  the Supabase path has worked all along — those are different databases running the
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
  `docker-compose.yml` uses, so low risk); (b) port `5434` already bound by something
  else stale; (c) a real drift between `schema.sql` and whatever the live Supabase
  schema actually looks like today, if anything was ever changed by hand against
  Supabase directly rather than through `schema.sql`. (c) would be the most important
  finding of this entire cycle if it happens, since it would mean the two databases
  this project can run against have silently diverged.
- **The `k <= 0` fix reopens a file every prior day explicitly marked "do not
  modify."** That restriction existed to keep each day's diff scoped; Day 7's whole
  purpose is closing exactly this kind of deliberately-deferred item, so reopening it
  now — for this one line only — is the plan working as intended, not a violation of
  the pattern.
