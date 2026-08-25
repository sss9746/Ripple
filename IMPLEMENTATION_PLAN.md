# Implementation Plan — Day 5: BM25 Lexical Search

## 0. Process note for this cycle

Per explicit instruction: **`SPEC.md` is read-only for this cycle.** Nothing below
proposes editing it. Where SPEC.md's text is ambiguous or has a non-obvious side
effect, this plan flags it for review rather than silently "correcting" it in the
plan or the code.

This cycle is also structured for **collaborative, step-by-step implementation** —
section 5 is broken into small, independently-completable steps. Decide per step
whether you or Codex implements it.

## 0a. BLOCKING DECISION — resolve before Step 1

SPEC.md 9.5's `tokenize()`, reproduced literally, has a real, testable consequence:

```pycon
>>> tokenize("worker")
["worker", "worker"]
```

Any raw token with **no** `.`/`_`/`-` in it (a plain delimiter-free word — very common:
`"true"`, `"worker"` as a bare word, etc.) gets counted **twice**: `re.split` on a
string with no delimiter returns `[the_whole_string]` as its sole element, and the
`len(p) > 1` extension then re-adds that same string on top of the raw token the loop
already appended. Net effect: every delimiter-free word of length > 1 gets roughly 2x
the term frequency of a genuinely multi-part identifier, in BM25's term-frequency
component.

**This plan does not choose for you. Pick one before Step 1 begins:**

- **(A) Implement SPEC.md literally.** Keep the duplication, keep the characterization
  test that pins `tokenize("worker") == ["worker", "worker"]` (section 7). No spec
  change, no code deviation from section 9.5's literal text.
- **(B) Revise the tokenizer's behavior** so delimiter-free words aren't duplicated
  (e.g. `parts = [p for p in parts if len(p) > 1 and p != tok]`). This is a
  **spec-level decision**, not something this plan or Codex applies unilaterally —
  if you choose (B), it should happen as its own explicitly-approved, spec-only
  change (updating SPEC.md's section 9.5 deliberately, on the record, separately from
  this implementation cycle), not as a silent side effect of implementing Day 5.
  **`SPEC.md` is not edited by this plan or by Step 1 regardless of which option you
  pick** — if (B), that edit is a separate, explicit action you take (or ask for) on
  its own.

Step 1 below implements (A) as its working assumption, since that requires no prior
decision to start from — it's the literal spec. If you choose (B), Step 1's code and
its characterization test both need a one-line change; nothing else in this plan
depends on which option is chosen.

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
  split across modules.
- Section 9.11 (`RetrievalConfig`, already implemented): `use_bm25: bool = True`,
  `bm25_k: int = 30`. **Not wired to anything yet** — no code currently reads
  `RetrievalConfig`. Unchanged this cycle (section 9).
- Section 5 (Corpus): `examples/complete` (used for every prior day's manual check) is
  the flat, concrete-reference root. The module root is a harder, more heavily
  parameterized corpus meant to be added later — relevant to section 8's acceptance
  criteria below.

## 3. Current implementation gaps

- `ripple/retrieval/bm25.py` does not exist.
- `ripple/db.py` has no read function returning `embed_text` alongside the other
  fields needed for a `RetrievedBlock` — `fetch_resource_bodies` (Day 4) only returns
  `(id, address, body)`.
- **Dependency check (resolved, not left open):** `rank_bm25` is already listed in
  `requirements.txt` (Day 1, unused until now) and already installed in this
  environment (`rank-bm25==0.2.2`, confirmed via `pip show rank_bm25`; import verified
  directly: `from rank_bm25 import BM25Okapi` succeeds). **No `requirements.txt`
  change needed or proposed.**
- **Already-indexed data available for the manual acceptance check (confirmed by
  direct query, not assumed):** `repos.id = 13` (`name = "vpc-complete"`,
  `local_path = .../examples/complete`) already has 114 resources, all with non-`NULL`
  embeddings, including `aws_security_group.rds` (`resources.id = 328`). This means
  section 8's acceptance check needs **no new indexing and no OpenAI cost** — see
  section 8.

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

**Do not start this step until the section 0a decision is resolved.**

In `ripple/retrieval/bm25.py`, option (A) (literal spec) shown — see 0a for option (B):

```python
import re

TOKEN_RE = re.compile(r'[A-Za-z0-9_.\-]+')
SPLIT_RE = re.compile(r'[._\-]')


def tokenize(text: str) -> list[str]:
    """SPEC.md 9.5's tokenizer: emit each raw token plus its
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

(Compiled patterns instead of inline `re.findall`/`re.split` calls — same behavior,
avoids recompiling on every call, harmless deviation in form only.)

**Read this before writing it:** casing is normalized once, via `.lower()`, applied to
the *whole* input text before tokenizing. **`tokenize()` must be applied to the query
string too, not just corpus documents** — if a caller tokenizes the corpus but
naively `.split()`s the query, matches will silently be missed. Not stated explicitly
in SPEC.md's snippet; necessary for the tokenizer to do its job at query time too (see
Step 4).

### Step 2 — `db.fetch_bm25_documents(repo_id)`

In `ripple/db.py`, alongside `fetch_resource_bodies`:

```python
def fetch_bm25_documents(
    repo_id: int,
) -> list[tuple[int, str, str, int, int, str, str]]:
    """Return (id, address, file_path, start_line, end_line, body, embed_text)
    for every resource in repo_id.
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
`ORDER BY r.address` lesson in `graph.py`) — mechanical, pattern-matches
`fetch_resource_bodies` exactly.

### Step 3 — `BM25Document`, `BM25Index`, and `build_index(repo_id)`

Still in `ripple/retrieval/bm25.py`:

```python
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from ripple import db
from ripple.retrieval.vector_store import RetrievedBlock


@dataclass
class BM25Document:
    id: int
    address: str
    file_path: str
    start_line: int
    end_line: int
    body: str
    tokens: frozenset[str]
```

`tokens` is new relative to `RetrievedBlock`'s shape — it exists specifically to
support Step 4's overlap-based filtering (see section 6/10 for why score alone can't
be used for this). It's the `frozenset` of every token `embed_text` produced, computed
once at build time so query time never re-tokenizes documents.

```python
class BM25Index:
    """An in-memory BM25 index over one repo's resources, built from
    embed_text. Rebuilt fresh on every build_index() call — there is no
    cross-call caching yet (see section 9 for why that's fine for now).
    """

    def __init__(self, documents: list[BM25Document], model: BM25Okapi | None):
        self._documents = documents
        self._model = model

    # query() defined in Step 4


def build_index(repo_id: int) -> BM25Index:
    rows = db.fetch_bm25_documents(repo_id)

    if not rows:
        return BM25Index(documents=[], model=None)

    tokenized_corpus = [tokenize(row[6]) for row in rows]  # row[6] = embed_text

    documents = [
        BM25Document(
            id=row[0],
            address=row[1],
            file_path=row[2],
            start_line=row[3],
            end_line=row[4],
            body=row[5],
            tokens=frozenset(tokenized_corpus[i]),
        )
        for i, row in enumerate(rows)
    ]

    model = BM25Okapi(tokenized_corpus)
    return BM25Index(documents, model)
```

**The empty-corpus short-circuit is deliberate, not incidental.** `BM25Okapi([])`'s
behavior on an empty corpus is a `rank_bm25` implementation detail this plan doesn't
want to depend on (see section 10) — `build_index` never constructs a `BM25Okapi` at
all when there are zero rows.

### Step 4 — `BM25Index.query()`

```python
    def query(self, question: str, k: int) -> list[RetrievedBlock]:
        if self._model is None or k <= 0:
            return []

        query_tokens = tokenize(question)
        query_token_set = set(query_tokens)
        if not query_token_set:
            return []

        candidate_indexes = [
            i
            for i, document in enumerate(self._documents)
            if document.tokens & query_token_set
        ]
        if not candidate_indexes:
            return []

        scores = self._model.get_scores(query_tokens)

        ranked_indexes = sorted(
            candidate_indexes,
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

**Why this shape, explained (this replaces an earlier, unsafe draft that scored and
returned every document regardless of relevance):**

- **`k <= 0` is checked up front, alongside `self._model is None`** — both are "there
  is nothing to return" conditions, handled identically.
- **An empty query-token set short-circuits to `[]`.** `tokenize("???")` is `[]`;
  there is no lexical evidence to rank anything by, so nothing is returned. Returning
  an arbitrary alphabetically-first slice of the corpus here (what an earlier draft of
  this plan effectively did, by always ranking every document) would hand Day 6's RRF
  fusion candidates with zero actual relevance, silently polluting the fused result.
- **Candidates are selected by token *overlap* (`document.tokens & query_token_set`),
  not by score sign.** This is the important one: `BM25Okapi`'s IDF term,
  `log((N - n + 0.5) / (n + 0.5))`, goes **negative** whenever a term appears in more
  than half the documents in the corpus — completely realistic in small corpora
  (SPEC.md's own benchmark is 40 questions over a few hundred blocks; test fixtures
  are smaller still). A document that **genuinely shares a query term** can therefore
  receive a **negative or zero overall score**, while a document with **zero shared
  terms** always scores exactly `0.0`. Filtering by `score > 0` would incorrectly
  *exclude* real matches in exactly the corpora this project uses, while filtering by
  nothing (the earlier draft) would incorrectly *include* complete non-matches.
  Token-set overlap is the one criterion that correctly separates "has lexical
  evidence" from "has none," independent of how the IDF math happens to net out.
  Section 7 includes a constructed test proving a real, overlapping match can still
  carry a non-positive score and must still be returned.

`BM25Index` deliberately does **not** implement the `VectorStore` Protocol
(`upsert`/`delete_namespace`) — it has no persistent storage; it's rebuilt from the
database every time `build_index()` is called. Reusing `RetrievedBlock` as the return
type is intentional forward-compatibility with Day 6's fusion, which will want both
vector and BM25 results in the same shape.

### Step 5 — Tests (`tests/test_bm25.py`)

See section 7 for the full required list.

### Step 6 — Manual acceptance check (no new indexing, no OpenAI cost)

See section 8 — this now runs entirely against already-indexed data (`repo_id = 13`).
An *optional*, separate, explicitly-approved secondary check against the module root
also exists in section 8, clearly marked as costing real OpenAI usage and requiring
your go-ahead before it runs.

## 6. Interfaces, data structures, and error behavior

- `tokenize(text: str) -> list[str]` — pure, never raises, `""` in → `[]` out. Exact
  behavior contingent on the section 0a decision.
- `BM25Document` — mirrors `RetrievedBlock`'s fields (minus `score`), plus `tokens`
  (a `frozenset[str]`, the full tokenized form of that document's `embed_text`), which
  exists solely to support overlap-based candidate filtering at query time. Not part
  of `RetrievedBlock`'s own shape.
- `build_index(repo_id: int) -> BM25Index` — never raises for a repo with zero
  resources; returns a `BM25Index` whose `query()` always returns `[]`. Raises
  whatever `db.fetch_bm25_documents`/`rank_bm25.BM25Okapi` raise, uncaught, for any
  other failure.
- `BM25Index.query(question: str, k: int) -> list[RetrievedBlock]`:
  - Returns `[]` if the index has no documents, or `k <= 0`.
  - Returns `[]` if `question` tokenizes to no tokens (e.g. pure punctuation).
  - Returns `[]` if `question`'s tokens share **no** overlap with **any** indexed
    document's tokens — this is a real "no evidence" result, not an error.
  - Otherwise, returns up to `k` results, ranked by `(-score, address)`, restricted to
    documents with at least one overlapping token with the query. A returned
    document's `score` **may be zero or negative** — a low/negative score is not a
    reason for exclusion once a document has qualified via token overlap; only the
    overlap test decides inclusion.
- `db.fetch_bm25_documents(repo_id) -> list[tuple[int, str, str, int, int, str, str]]`
  — `(id, address, file_path, start_line, end_line, body, embed_text)`, ordered by
  `id`. Empty list for a repo with no resources; never raises for an unknown `repo_id`.

## 7. Required tests

`tests/test_bm25.py`, tokenizer section (pure, no DB, instant):
- `tokenize("aws_security_group.worker")` — the exact worked example from SPEC.md
  9.5: `== ["aws_security_group.worker", "aws", "security", "group", "worker"]`.
- Casing: `tokenize("AWS_Security_Group.Worker")` produces the identical output to the
  lowercase version above.
- Hyphens: `t3-micro` splits into `t3-micro`, `t3` (len 2, kept), `micro`.
- Short parts filtered: `a.b` (both parts length 1) contributes only the raw token
  `a.b` itself.
- Consecutive delimiters: `aws..vpc` → `["aws..vpc", "aws", "vpc"]`, no empty-string
  entries (the `len(p) > 1` filter naturally excludes them).
- Non-token characters (spaces, braces, quotes, `=`) separate raw tokens —
  `tokenize('name = "worker-sg"')` produces multiple independent raw tokens.
- **Tokenizer duplication — implement per the section 0a decision, whichever is
  chosen.** If (A): `tokenize("worker") == ["worker", "worker"]`, with a comment
  explaining why (SPEC.md's literal code, reproduced exactly). If (B): the analogous
  test asserting `tokenize("worker") == ["worker"]`, since that's the whole point of
  choosing (B).
- Multiple distinct Terraform tokens in one `embed_text`-shaped string — spot-check
  that both `"aws_vpc.main"` (full address) and `"main"` (a part) appear.

`tests/test_bm25.py`, `BM25Index` section (DB-dependent, skip-if-unreachable — same
convention as every prior day; reuse `tests/fixtures/reference_repo/` and
`tests/fixtures/sample_repo/` via `indexer.index_repo(..., embedder=
_FakeEmbeddingProvider())`, no new fixture needed):

- **Exact-address retrieval (the Day 5 acceptance criterion, formalized)**: index
  `reference_repo`, build a `BM25Index`, query `"aws_vpc.main"`; assert
  `results[0].address == "aws_vpc.main"`.

- **Repository isolation — corrected logic.** Index `reference_repo` under one
  throwaway repo and `sample_repo` under a second; build a `BM25Index` for each.
  Capture each repo's own known resource ids (e.g. via `db.fetch_bm25_documents`).
  Run the *same* query (anything with plausible overlap in both, e.g. `"aws"`) against
  both indexes and assert:
  ```python
  reference_result_ids = {block.id for block in reference_index.query("aws", k=50)}
  sample_result_ids = {block.id for block in sample_index.query("aws", k=50)}

  assert reference_result_ids <= reference_repo_ids
  assert reference_result_ids.isdisjoint(sample_repo_ids)

  assert sample_result_ids <= sample_repo_ids
  assert sample_result_ids.isdisjoint(reference_repo_ids)
  ```
  A `BM25Index` built from `reference_repo` can, by construction, only ever hold
  `reference_repo`'s own documents — so this subset/disjoint pair is what's actually
  checkable and meaningful (an earlier draft of this plan asserted the *opposite*,
  that no result should belong to the repo the index was built from, which is
  impossible by construction and was wrong — that assertion is removed).
  Then, using `aws_security_group.worker`, which exists as a **distinct row in both**
  fixtures (same address text, different `id`s — the real cross-contamination risk):
  query each index for `"aws_security_group.worker"` and assert `results[0].id`
  equals *that specific repo's own* row id for that address, never the other repo's
  id for the same address text.

- **Deterministic ordering**: call `.query()` twice with identical arguments against
  the same built index; assert identical results, in identical order.

- **Empty corpus**: `build_index` for a `repo_id` with zero resources returns a
  `BM25Index` whose `.query("anything", k=5)` is `[]`, without raising — the direct
  regression test for the `BM25Okapi([])` avoidance in Step 3.

- **`k <= 0`**: against a non-empty index, `.query("aws_vpc.main", k=0)` and
  `.query("aws_vpc.main", k=-1)` both return `[]`, even though the query itself would
  otherwise clearly match something.

- **Empty/punctuation query returns no evidence, not arbitrary results**:
  `.query("???", k=5)` against a non-empty index returns `[]`.

- **Unknown textual query returns no evidence**: a query composed entirely of words
  that appear nowhere in the corpus (e.g. `"zzz_nonexistent_zzz qqq_unmatched_qqq"`)
  returns `[]`.

- **A genuine match may carry a non-positive score, and must still be returned** —
  this is a constructed unit test, not DB-dependent, since it needs precise control
  over corpus content to force negative IDF deterministically:
  ```python
  def test_query_returns_overlapping_documents_with_nonpositive_scores() -> None:
      texts = ["common alpha", "common beta", "common gamma"]
      tokenized = [tokenize(t) for t in texts]
      documents = [
          BM25Document(
              id=i, address=f"addr.{i}", file_path="f.tf",
              start_line=1, end_line=1, body=t, tokens=frozenset(toks),
          )
          for i, (t, toks) in enumerate(zip(texts, tokenized))
      ]
      index = BM25Index(documents, BM25Okapi(tokenized))

      results = index.query("common", k=10)

      # "common" appears in all 3 documents, so BM25Okapi's IDF term for it
      # is negative (log((3 - 3 + 0.5) / (3 + 0.5)) < 0) -- every score here
      # is <= 0, yet all three genuinely share the query term and must be
      # returned, not filtered out.
      assert {r.address for r in results} == {"addr.0", "addr.1", "addr.2"}
      assert all(r.score <= 0 for r in results)
  ```

- **`k` truncation**: a corpus with more matching documents than `k` returns exactly
  `k`; fewer matching documents than `k` returns all of them (never pads with
  non-matching documents to reach `k`).

- **Regression check, not a new test**: `tests/test_pgvector_store.py` must continue
  to pass completely unmodified. Run the full suite, not just the new file.

Run `python -m pytest` after implementation; all tests must pass. DB-dependent tests
skip cleanly if Postgres isn't reachable. Nothing in this cycle needs
`OPENAI_API_KEY` except indirectly, through the existing `_FakeEmbeddingProvider`
pattern for indexing fixtures.

## 8. Acceptance criteria

- `python -m pytest` passes with no failures, including the full existing suite.
- The fixture-based exact-address test (section 7) passes.
- **Primary manual acceptance check — no new indexing, no OpenAI cost, run this
  first:** using the already-indexed `repos.id = 13` (`vpc-complete`,
  `examples/complete`, confirmed present with 114 embedded resources including
  `aws_security_group.rds` at `resources.id = 328` — see section 3):
  ```python
  from ripple.retrieval.bm25 import build_index
  results = build_index(13).query("aws_security_group.rds", k=5)
  assert results[0].address == "aws_security_group.rds"
  ```
  SPEC.md's Day 5 wording asks for an exact address "like `aws_nat_gateway.this`" —
  this exercises the identical exact-address behavior, on data that already exists,
  for zero additional cost.
- **Optional secondary check — reproduces SPEC's literal named example — requires
  your explicit go-ahead before running, since it costs real OpenAI usage:**
  `aws_nat_gateway.this` exists only in the **module root**
  (`.repos/terraform-aws-vpc/main.tf:1228`), not in `examples/complete`. Running this
  requires registering the module root as a new repo (`python scripts/index_repo.py
  .repos/terraform-aws-vpc --name vpc-module-root`), which embeds every block in it —
  a real, if small, API cost. **Do not run this without confirming first.** If run,
  query with the **exact dotted address**, not a space-separated phrase:
  ```python
  results = build_index(module_root_repo_id).query("aws_nat_gateway.this", k=5)
  assert results[0].address == "aws_nat_gateway.this"
  ```
  (An earlier draft of this plan incorrectly suggested querying with
  `"aws_nat_gateway this"` — a space, not a dot. That's a different, weaker test:
  it exercises loose keyword matching, not the exact-address behavior SPEC.md's
  wording is actually asking about. Fixed here.)

## 9. Explicit non-goals

- **`scripts/ask.py` is not touched.** It remains vector-only this cycle.
  `RetrievalConfig.use_bm25`/`use_vector` are not consulted by anything yet — that's
  `pipeline.py`'s job, Day 6.
- RRF or any fusion logic (`fusion.py`) — Day 6, explicitly. Nothing in this cycle
  reads or is read by anything Day 6 will build; `BM25Index.query()`'s "no evidence
  returns `[]`" contract (section 6) exists specifically so that whenever Day 6's RRF
  does arrive, it never has to guess whether an empty or low-score BM25 result means
  "no evidence" or "everything, unfiltered."
- Graph expansion wiring into anything — `graph.py` (Day 4) is untouched.
- Cross-encoder reranking, query rewriting — Days 12 and 15 respectively.
- **Caching a `BM25Index` across multiple calls within a long-running process.**
  SPEC.md 9.5 says "rebuilt at process start" — for every consumer that exists today
  (a one-shot test or CLI invocation), "at process start" and "rebuilt every call" are
  the same thing. This matters only once Day 17's FastAPI app exists as a
  long-running server — out of scope here.
- `PineconeStore`, the `RetrievalConfig`-driven pipeline itself, the FastAPI app — all
  still not built, unchanged from prior days' non-goals.
- **Modifying `SPEC.md`.** Any apparent issue in its text (section 0a) is flagged for
  your decision, never edited directly by this plan or by implementing it.

## 10. Risks, ambiguities, and things flagged for your review

- **Blocking, not just flagged: the tokenizer duplication decision (section 0a).**
  Repeated here because it's the one item that must be resolved before Step 1, not
  just noted for later.
- **Why score-based filtering was rejected, restated for visibility:** `BM25Okapi`'s
  IDF term can go negative for common terms in small corpora, so "score > 0" is not a
  safe proxy for "this document is relevant" — it would wrongly exclude genuine
  matches. Token-set overlap (section 5, Step 4) is the correct, safe criterion, and
  is what's implemented. See the constructed test in section 7 for a concrete,
  deterministic demonstration.
- **BM25 scores are unnormalized and not comparable to vector cosine scores.**
  Expected, and exactly why Day 6 uses RRF instead of a weighted score sum (SPEC.md
  9.6 says so explicitly) — noted so it's not mistaken for an oversight here.
- **`BM25Okapi([])`'s actual behavior was not empirically verified** against the
  installed `rank-bm25==0.2.2` — sidestepped entirely by never constructing one for an
  empty corpus (Step 3), rather than relying on an unverified library edge case.
- **Fixture reuse, not new fixtures.** Both `sample_repo` (Day 2) and `reference_repo`
  (Day 4) are reused for repo-isolation testing; neither fixture file is modified.
- **`get_scores()` vs `get_top_n()`**: `BM25Okapi.get_top_n()` returns documents, not
  indices or scores — not enough to build `RetrievedBlock`s or apply the deterministic
  tie-break. This plan uses `get_scores()` directly and ranks/tie-breaks itself.
- **Repo `id = 13`** used in section 8's primary acceptance check is pre-existing
  state in the shared development database (confirmed present, not created by this
  plan). If it's ever deleted or re-indexed differently, section 8's primary check
  should be re-pointed at whatever `repo_id` currently holds `examples/complete`,
  re-verified the same way (a direct `SELECT` before trusting an address exists), not
  assumed to still be `13`.
