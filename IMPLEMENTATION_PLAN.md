# Implementation Plan — Day 5: BM25 Lexical Search

## 0. Process note for this cycle

Per explicit instruction: **`SPEC.md` is read-only for this cycle.** Nothing below
proposes editing it. Where SPEC.md's text is ambiguous or has a non-obvious side
effect, this plan flags it in section 10 for review rather than silently
"correcting" it in the plan or the code.

This cycle is also structured for **collaborative, step-by-step implementation** —
section 5 is broken into six small, independently-completable steps. Decide per step
whether you or Codex implements it; each step has a clear, small deliverable and can
be reviewed/tested on its own before moving to the next.

## 1. Objective

Add lexical (keyword) search alongside Day 3's vector search: a Terraform-aware
tokenizer that makes exact identifiers like `aws_security_group.worker` findable both
as a whole and by their parts, and an in-memory `rank_bm25.BM25Okapi` index built from
`resources.embed_text`, scoped to one repo at a time. This is a second, independent
retrieval signal — nothing fuses it with vector search yet (that's Day 6).

This is SPEC.md's Day 5 milestone, sitting on top of Day 1–4
(`ripple/config.py`, `ripple/db.py`, `ripple/ingest/`, `ripple/llm/`,
`ripple/retrieval/vector_store.py` + `pgvector_store.py`, `scripts/index_repo.py`,
`scripts/ask.py`), all already implemented and verified (63 passing tests as of Day 4,
commit `a53cad6`).

## 2. Relevant SPEC.md requirements

- Section 11, Day 5: "`bm25.py` with the tokenizer from section 9.5. Corpus built from
  `embed_text` at startup. **Done when:** querying an exact address like
  `aws_nat_gateway.this` returns that block at rank 1."
- Section 9.5 (BM25), quoted verbatim:
  > `rank_bm25.BM25Okapi` over an in-memory corpus, rebuilt at process start.
  >
  > **Tokenization is the whole game here.** The default whitespace split makes
  > `aws_security_group.worker` a single unmatched token. Instead, emit both the full
  > token and its parts:
  ```python
  def tokenize(text: str) -> list[str]:
      raw = re.findall(r'[A-Za-z0-9_.\-]+', text.lower())
      out = []
      for tok in raw:
          out.append(tok)
          parts = re.split(r'[._\-]', tok)
          out.extend(p for p in parts if len(p) > 1)
      return out
  ```
  > So `aws_security_group.worker` yields the full string plus `aws`, `security`,
  > `group`, `worker`. A query for either the exact address or a loose phrase now hits.
  >
  > Index over `embed_text`. Default limit 30 per rewritten query.
- Section 8 (repository layout): `retrieval/bm25.py` — "lexical search." One file, not
  split across modules — the tokenizer, the corpus builder, and the index all belong
  here.
- Section 9.11 (`RetrievalConfig`, already implemented in `ripple/config.py`):
  `use_bm25: bool = True`, `bm25_k: int = 30`. **Not wired to anything yet** — no code
  currently reads `RetrievalConfig` at all. This cycle does not change that (see
  section 9, non-goals) — `bm25_k` is not consulted; callers (tests, and later
  `pipeline.py`) pass `k` explicitly.
- Section 5 (Corpus): the target repo is `terraform-aws-vpc`. `examples/complete` (used
  for every prior day's manual corpus check) is the flat, concrete-reference example
  root. The **module root** (the repo's top-level `main.tf` etc.) is described as a
  harder, more heavily parameterized corpus to add "once the pipeline works on
  `examples/`" — see section 8's acceptance-criterion note below for why this matters
  for Day 5 specifically.

## 3. Current implementation gaps

- `ripple/retrieval/bm25.py` does not exist — no tokenizer, no lexical index.
- `ripple/db.py` has no read function that returns `embed_text` alongside the other
  fields needed to build a `RetrievedBlock` (id, address, file_path, start_line,
  end_line, body) — `fetch_resource_bodies` (Day 4) only returns `(id, address,
  body)`, not enough to construct a full result or to tokenize `embed_text`
  specifically (as opposed to `body`).
- **Dependency check (done as part of this planning pass, not left as an open
  question):** `rank_bm25` is already listed in `requirements.txt` (added Day 1,
  unused until now) and is already installed in this environment
  (`rank-bm25==0.2.2`, confirmed via `pip show rank_bm25`). **No `requirements.txt`
  change is needed or proposed.** If Codex's own environment doesn't have it
  installed, that's a `pip install -r requirements.txt` step (same as Day 3's `openai`/
  `pgvector` gap), not a file change.

## 4. Exact files Codex (or you) should create or modify

Create:
- `ripple/retrieval/bm25.py`
- `tests/test_bm25.py`

Modify:
- `ripple/db.py` — add one new read function, `fetch_bm25_documents(repo_id)`.

Do not modify: `sql/schema.sql`, `docker-compose.yml`, `.env.example`,
`requirements.txt`, `ripple/config.py`, `ripple/ingest/*`, `ripple/llm/*`,
`ripple/retrieval/vector_store.py`, `ripple/retrieval/pgvector_store.py`,
`ripple/retrieval/graph.py`, `scripts/index_repo.py`, `scripts/ask.py`, `SPEC.md`,
`AGENTS.md`, `CLAUDE.md`, `README.md`, and every existing test file (`test_config.py`,
`test_db.py` — aside from confirming it still passes unmodified —, `test_scanner.py`,
`test_parser.py`, `test_references.py`, `test_indexer.py`, `test_graph.py`,
`test_embeddings.py`, `test_generate.py`, `test_prompts.py`, `test_pgvector_store.py`,
`test_index_repo.py`, `test_ask.py`).

**`scripts/ask.py` is deliberately not touched this cycle** — see section 9.

## 5. Step-by-step implementation order (collaborative — assign each step as you go)

### Step 1 — `tokenize()` (pure function, no DB, no dependencies)

In `ripple/retrieval/bm25.py`:

```python
import re

TOKEN_RE = re.compile(r'[A-Za-z0-9_.\-]+')
SPLIT_RE = re.compile(r'[._\-]')


def tokenize(text: str) -> list[str]:
    """SPEC.md 9.5's tokenizer, verbatim: emit each raw token plus its
    underscore/period/hyphen-delimited parts (parts of length > 1 only), so
    an exact address and a loose keyword phrase both hit the same document.
    """
    raw = TOKEN_RE.findall(text.lower())
    out = []
    for tok in raw:
        out.append(tok)
        parts = SPLIT_RE.split(tok)
        out.extend(p for p in parts if len(p) > 1)
    return out
```

This is section 9.5's function reproduced exactly (compiled patterns instead of
inline `re.findall`/`re.split` calls — same behavior, avoids recompiling the regex on
every call, harmless deviation in form only). A good first step to do by hand — it's
self-contained, has no dependencies, and the test cases in Step 5 pin down every edge
case explicitly, so it's easy to verify in isolation.

**Read this before writing it:** casing is normalized once, via `.lower()`, applied to
the *whole* input text before tokenizing. This means **`tokenize()` must be applied to
the query string too, not just corpus documents** — if a caller tokenizes the corpus
but naively `.split()`s the query, case and punctuation handling will silently diverge
and matches will be missed. This isn't stated explicitly in SPEC.md's snippet; it's a
requirement this plan is adding because it's necessary for the tokenizer to do its job
at query time, not just index time (see Step 4).

### Step 2 — `db.fetch_bm25_documents(repo_id)`

In `ripple/db.py`, alongside `fetch_resource_bodies`:

```python
def fetch_bm25_documents(
    repo_id: int,
) -> list[tuple[int, str, str, int, int, str, str]]:
    """Return (id, address, file_path, start_line, end_line, body, embed_text)
    for every resource in repo_id — everything bm25.build_index needs to both
    tokenize (embed_text) and construct a RetrievedBlock (everything else).
    """
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT id, address, file_path, start_line, end_line, body, embed_text
                FROM resources
                WHERE repo_id = %s
                ORDER BY id
                """,
                (repo_id,),
            )
            return cursor.fetchall()
```

`ORDER BY id` is added purely for hygiene/determinism (matching Day 4's
`ORDER BY r.address` lesson in `graph.py`) — the final result ordering from
`BM25Index.query()` doesn't actually depend on fetch order (see Step 4's tie-break
rule), but there's no reason to leave it unspecified when it's free to add.

This is mechanical and pattern-matches `fetch_resource_bodies` exactly — a good step
to hand to Codex, or a quick one to do yourself if you want the rest of the day for
the more interesting parts.

### Step 3 — `BM25Document` and `build_index(repo_id)`

Still in `ripple/retrieval/bm25.py`:

```python
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from ripple import db


@dataclass
class BM25Document:
    id: int
    address: str
    file_path: str
    start_line: int
    end_line: int
    body: str


class BM25Index:
    """An in-memory BM25 index over one repo's resources, built from
    embed_text. Rebuilt fresh on every build_index() call — there is no
    cross-call caching yet (see section 9/10 for why that's fine for now).
    """

    def __init__(self, documents: list[BM25Document], model: BM25Okapi | None):
        self._documents = documents
        self._model = model

    # query() defined in Step 4


def build_index(repo_id: int) -> BM25Index:
    rows = db.fetch_bm25_documents(repo_id)

    documents = [
        BM25Document(
            id=row[0],
            address=row[1],
            file_path=row[2],
            start_line=row[3],
            end_line=row[4],
            body=row[5],
        )
        for row in rows
    ]

    if not documents:
        return BM25Index(documents=[], model=None)

    tokenized_corpus = [tokenize(row[6]) for row in rows]  # row[6] = embed_text
    model = BM25Okapi(tokenized_corpus)
    return BM25Index(documents, model)
```

**The empty-corpus short-circuit is deliberate, not incidental.** `BM25Okapi([])`'s
behavior on an empty corpus (division-by-zero in its average-document-length
calculation, or some other internal failure) is a `rank_bm25` implementation detail
this plan doesn't want to depend on either way — `build_index` never constructs a
`BM25Okapi` at all when there are zero documents, and `BM25Index.query()` (Step 4)
checks `self._model is None` first and returns `[]` immediately in that case.

### Step 4 — `BM25Index.query()`

```python
    def query(self, question: str, k: int) -> list[RetrievedBlock]:
        if self._model is None:
            return []

        query_tokens = tokenize(question)
        scores = self._model.get_scores(query_tokens)

        ranked_indexes = sorted(
            range(len(self._documents)),
            key=lambda i: (-scores[i], self._documents[i].address),
        )

        return [
            RetrievedBlock(
                id=self._documents[i].id,
                address=self._documents[i].address,
                file_path=self._documents[i].file_path,
                start_line=self._documents[i].start_line,
                end_line=self._documents[i].end_line,
                body=self._documents[i].body,
                score=float(scores[i]),
            )
            for i in ranked_indexes[:k]
        ]
```

(Add `from ripple.retrieval.vector_store import RetrievedBlock` to the imports at the
top of the file.)

Two design decisions worth understanding, not just copying:
- **The query itself goes through the same `tokenize()`** used for the corpus (Step
  1's warning). Using a different tokenization scheme for queries vs. documents is the
  single most common way to silently break a BM25 setup.
- **Deterministic tie-breaking**: sort key is `(-score, address)`, so equal-scoring
  documents (all-zero scores from an empty-token query, or genuine score ties) always
  come out in the same order — alphabetical by address — rather than whatever
  incidental order `rank_bm25`/Python happen to produce. This is what "stable result
  ordering" (your requirement 2) means concretely, and it's what Step 5's determinism
  test checks.

`BM25Index` deliberately does **not** implement the `VectorStore` Protocol
(`upsert`/`delete_namespace`) — it has no persistent storage to upsert into or delete;
it's rebuilt from the database every time `build_index()` is called. Reusing
`RetrievedBlock` as the return type is intentional forward-compatibility with Day 6:
when `fusion.py` combines ranked lists from vector and BM25 search, both sides
returning the same shape means the fusion logic doesn't need to branch on source.

### Step 5 — Tests (`tests/test_bm25.py`)

See section 7 for the full required list. Suggested split if dividing this step
further: tokenizer tests first (pure, instant, no setup), then the DB-dependent
`BM25Index` tests (need a reachable Postgres, same skip-if-unreachable convention as
every prior day).

### Step 6 — Manual acceptance check against the real corpus

See section 8. **Read this before running it** — the literal SPEC.md example address
(`aws_nat_gateway.this`) is not in the corpus every prior day's manual check has used.

## 6. Interfaces, data structures, and error behavior

- `tokenize(text: str) -> list[str]` — pure, never raises, `""` in → `[]` out.
- `BM25Document` — plain dataclass mirroring the fields `RetrievedBlock` needs, minus
  `score` (which only exists at query time) and `embed_text` (needed only transiently,
  for tokenizing at build time — not retained on the document, since nothing after
  indexing needs the raw embed_text again).
- `build_index(repo_id: int) -> BM25Index` — never raises for a repo with zero
  resources; returns a `BM25Index` whose `query()` always returns `[]`. Raises
  whatever `db.fetch_bm25_documents`/`rank_bm25.BM25Okapi` raise, uncaught, for any
  other failure (consistent with every prior day's "let real failures propagate"
  posture).
- `BM25Index.query(question: str, k: int) -> list[RetrievedBlock]` — returns at most
  `k` results, fewer if the corpus has fewer documents than `k`. Returns `[]` for an
  empty corpus (`self._model is None`) or `k <= 0`. An empty-token query (e.g.
  `question` is only punctuation) still runs — `get_scores([])` scores every document
  identically (typically `0.0`), and the `(-score, address)` tie-break still produces
  a deterministic (if not meaningful) ranking rather than an error.
- `db.fetch_bm25_documents(repo_id) -> list[tuple[int, str, str, int, int, str, str]]`
  — `(id, address, file_path, start_line, end_line, body, embed_text)`, ordered by
  `id`. Empty list for a repo with no resources; never raises for an unknown `repo_id`.

## 7. Required tests

`tests/test_bm25.py`, tokenizer section (pure, no DB, instant):
- `tokenize("aws_security_group.worker")` — the exact worked example from SPEC.md
  9.5: `== ["aws_security_group.worker", "aws", "security", "group", "worker"]`.
- Casing: `tokenize("AWS_Security_Group.Worker")` produces the identical output to the
  lowercase version above.
- Hyphens: a token like `t3-micro` (a real Terraform instance-type string) splits into
  `t3-micro`, `t3`(len 2, kept), `micro`.
- Short parts filtered: a token like `a.b` (both parts length 1) contributes only the
  raw token `a.b` itself — neither `a` nor `b` is added.
- Consecutive delimiters: a token like `aws..vpc` — `re.split` produces an empty
  string between the two dots, which the `len(p) > 1` filter naturally excludes, so
  the result is `["aws..vpc", "aws", "vpc"]` with no empty-string entries.
- Non-token characters (spaces, braces, quotes, `=`) act as separators between raw
  tokens — `tokenize('name = "worker-sg"')` produces multiple independent raw tokens,
  not one giant blob.
- **Duplicate-token behavior for delimiter-free words — pin this explicitly, it's
  non-obvious (see section 10):** `tokenize("worker")` (no `.`/`_`/`-` at all) is
  `["worker", "worker"]`, not `["worker"]`. `re.split` on a string with no delimiter
  returns the whole string as a single-element list, so the "parts" extension re-adds
  the same token the raw loop already appended. This is SPEC.md's literal code,
  reproduced exactly — this test documents the behavior rather than treating it as a
  bug to fix.
- Multiple distinct real Terraform tokens in one string (e.g. an `embed_text` header
  line `"aws_vpc.main\nFile: main.tf\nType: aws_vpc\n\n..."`) — spot-check that both
  `"aws_vpc.main"` (the full address) and `"main"` (a part) appear in the output.

`tests/test_bm25.py`, `BM25Index` section (DB-dependent, skip-if-unreachable — same
convention as every prior day; reuse `tests/fixtures/reference_repo/` via
`indexer.index_repo(..., embedder=_FakeEmbeddingProvider())`, no new fixture needed):
- **Exact-address retrieval (the Day 5 acceptance criterion, formalized)**: index
  `reference_repo`, build a `BM25Index` for that `repo_id`, query for
  `"aws_vpc.main"`; assert the top result (`results[0]`) has `address ==
  "aws_vpc.main"`.
- **Repository isolation**: index `reference_repo` under one throwaway repo and
  `tests/fixtures/sample_repo/` (Day 2's fixture) under a second throwaway repo;
  build a `BM25Index` for each. **Note before writing this test:**
  `aws_security_group.worker` and `data.aws_ami.ubuntu` exist in *both* fixtures
  (same address text, different rows/ids) — good evidence that isolation must be
  checked by row `id`, not by address text alone, but *not* usable as a
  "unique to one repo" example. Addresses actually unique to `reference_repo`:
  `aws_vpc.main`, `aws_subnet.public`, `aws_instance.node`, `var.cidr`. Addresses
  actually unique to `sample_repo`: `module.vpc`, `var.region`, `var.environment`.
  Query `reference_repo`'s index for `"module.vpc"` (a `sample_repo`-only address);
  assert no result's `id` belongs to `reference_repo`'s known id set. Then, using the
  address that exists in *both* fixtures (`aws_security_group.worker`), query each
  repo's index and assert the returned `id` matches that specific repo's own row for
  that address, not the other repo's row — this is the real isolation guarantee:
  same address text, different repos, never cross-contaminating.
- **Deterministic ordering**: call `.query()` twice with the same arguments against
  the same built index; assert identical results, in identical order.
- **Empty corpus**: `build_index` for a `repo_id` with zero resources (an
  unregistered/nonexistent id, or a real repo with `index_repo` run against an empty
  directory) returns a `BM25Index` whose `.query("anything", k=5)` is `[]`, without
  raising — this is the direct regression test for the `BM25Okapi([])` avoidance in
  Step 3.
- **Empty query**: query a non-empty index with a question that tokenizes to nothing
  (e.g. `"???"`); assert it returns up to `k` results (not an error, not necessarily
  meaningful) in the deterministic tie-break order (alphabetical by address).
- **`k` truncation**: a corpus with more documents than `k` returns exactly `k`
  results; a corpus with fewer than `k` documents returns all of them.
- **Regression check, not a new test**: `tests/test_pgvector_store.py` must continue
  to pass completely unmodified — nothing in this cycle touches `vector_store.py` or
  `pgvector_store.py`. Run the full suite, not just the new file, before calling this
  cycle done.

Run `python -m pytest` after implementation; all tests must pass. DB-dependent tests
skip cleanly if Postgres isn't reachable, same convention as every prior day. Nothing
in this cycle needs `OPENAI_API_KEY` except indirectly, through the *existing*
`_FakeEmbeddingProvider` pattern used to index fixtures without a real API call —
same as every prior day since Day 3.

## 8. Acceptance criteria

- `python -m pytest` passes with no failures, including the full existing suite (not
  just `test_bm25.py`).
- The fixture-based exact-address test (section 7) passes: querying `"aws_vpc.main"`
  against `reference_repo`'s `BM25Index` returns that block at rank 1.
- **Manual acceptance check against the real corpus, reproducing SPEC.md's literal
  example — read the caveat first:**
  - `aws_nat_gateway.this` (SPEC.md's own named example, and section 10.1's
    benchmark.json example) is **not present in `examples/complete`**, the corpus
    every prior day's manual check has used. It exists only in the **module root**:
    confirmed via direct file inspection at `.repos/terraform-aws-vpc/main.tf:1228`
    (`resource "aws_nat_gateway" "this" { ... }`). Section 5 describes the module root
    as the harder, more heavily parameterized corpus meant to be added "once the
    pipeline works on `examples/`" — this is the first day where reproducing SPEC's
    own named example requires that second corpus.
  - To actually run this check: register the module root as a second `repos` entry
    (e.g. `python scripts/index_repo.py .repos/terraform-aws-vpc --name
    vpc-module-root`), then `bm25.build_index(that_repo_id).query("aws_nat_gateway
    this", k=5)` (or via a quick throwaway script/REPL — no new CLI is being built
    this cycle, see section 9) and confirm `results[0].address ==
    "aws_nat_gateway.this"`.
  - **This will cost real, if small, OpenAI usage** (indexing the module root means
    embedding every block in it) — same category of manual, non-automated,
    real-API-cost step as Day 3's acceptance check. Confirm before running it, same as
    before.
  - If this is skipped for time/cost, the fixture-based automated test above is the
    fallback evidence that exact-address retrieval works — it exercises the identical
    code path against a smaller, free, deterministic corpus.

## 9. Explicit non-goals

- **`scripts/ask.py` is not touched.** It remains vector-only this cycle.
  `RetrievalConfig.use_bm25`/`use_vector` are not consulted by anything yet — wiring
  BM25 as a second path (and reading `RetrievalConfig` at all) is `pipeline.py`'s job,
  Day 6. Adding BM25 into `ask.py` directly now would just be replaced/refactored away
  next cycle for no benefit.
- RRF or any fusion logic (`fusion.py`) — Day 6, explicitly.
- Graph expansion wiring into anything — unrelated to this cycle; `graph.py` (Day 4)
  is untouched.
- Cross-encoder reranking, query rewriting — Days 12 and 15 respectively.
- **Caching a `BM25Index` across multiple calls within a long-running process.**
  SPEC.md 9.5 says "rebuilt at process start" — for every consumer that exists today
  (a one-shot test, or eventually a one-shot CLI invocation), "at process start" and
  "rebuilt every call" are the same thing, since each invocation *is* a fresh process.
  This distinction only becomes real once Day 17's FastAPI app exists as a
  long-running server handling multiple requests per process — that's when
  build-once-reuse-across-requests caching would actually matter, and it's out of
  scope here.
- `PineconeStore`, the `RetrievalConfig`-driven pipeline itself, the FastAPI app — all
  still not built, unchanged from prior days' non-goals.
- Modifying `SPEC.md`. Any apparent issue in its text is flagged in section 10, not
  corrected.

## 10. Risks, ambiguities, and things flagged for your review

- **Flagged for review, not fixed: SPEC.md's `tokenize()` double-counts delimiter-free
  words.** For any raw token with no `.`/`_`/`-` in it (a plain single word — very
  common, e.g. `"true"`, `"worker"` as a standalone word in a description string),
  `re.split` on a string with no delimiter returns `[the_whole_string]`, and the
  `len(p) > 1` extension re-adds that same string a second time. Net effect: every
  delimiter-free word of length > 1 gets **double term frequency** relative to how a
  reader skimming the function might expect ("parts" implies something in addition
  to, not a repeat of, the raw token). This is implemented exactly as SPEC.md
  specifies (per this cycle's read-only policy) and pinned by an explicit test (section
  7), but it's worth you knowing about since it does mean single-word terms are
  systematically weighted about 2x relative to genuinely-multi-part identifiers in
  BM25's term-frequency component. If this is unwanted, the fix would be
  `parts = [p for p in parts if len(p) > 1 and p != tok]` or similar — not applied
  here, since that would be silently changing spec-specified behavior rather than
  flagging it.
- **BM25 scores are unnormalized and not comparable to vector cosine scores.** This is
  expected and is exactly why Day 6 uses RRF instead of a weighted score sum (SPEC.md
  9.6 says so explicitly) — noted here only so it's not mistaken for an oversight in
  this cycle's `RetrievedBlock.score` field.
- **`BM25Okapi([])`'s actual behavior was not empirically verified against the
  installed `rank-bm25==0.2.2`** — this plan avoids the question entirely by never
  constructing a `BM25Okapi` for an empty corpus (Step 3), rather than relying on
  (and needing to test) whatever that edge case actually does in the library. If a
  future change needs to know that behavior for some other reason, it should be
  checked directly rather than assumed from this plan.
- **The `reference_repo` fixture is reused, not rebuilt.** Both fixtures already used
  by Day 2/4 tests (`sample_repo`, `reference_repo`) are reused for repo-isolation
  testing rather than creating a third fixture — there's no cross-references or line-
  range concern for BM25 the way there was for Day 4's edge extraction, so reuse
  carries no risk of breaking those fixtures' existing hardcoded assertions (this plan
  never modifies fixture files, only reads already-indexed data from them).
- **`get_scores()` vs `get_top_n()`**: `rank_bm25.BM25Okapi` offers a `get_top_n()`
  convenience method, but it returns *documents*, not indices or scores, which isn't
  enough to build `RetrievedBlock`s (need `score`) or to apply the deterministic
  tie-break (need to compare against other documents' addresses). This plan uses
  `get_scores()` directly and does ranking/tie-breaking itself — a deliberate choice,
  not an oversight, worth knowing if you see `get_top_n()` mentioned in `rank_bm25`'s
  own docs and wonder why it isn't used here.
