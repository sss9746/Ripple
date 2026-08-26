# Implementation Plan — Day 6: Fusion and Observability

## 0. Process note for this cycle

Same as Day 5: **`SPEC.md` is read-only.** Nothing below proposes editing it. Where
SPEC.md's text is silent on something this cycle needs to decide (it is, on several
points — see section 10), this plan states the decision explicitly and flags it for
your review rather than guessing quietly.

**Read section 4 before starting.** This is the first cycle since Day 3 that touches
`scripts/ask.py`, and it's a near-total rewrite, not an addition — `ask()`'s internal
implementation changes completely, its CLI flag changes name, and `tests/test_ask.py`
needs an equally complete rewrite of its mocking strategy. Flagging this up front,
not discovering it mid-implementation.

## 1. Objective

Combine Day 3's vector search and Day 5's BM25 search into one retrieval signal via
Reciprocal Rank Fusion, orchestrate both (plus fusion) through a `RetrievalConfig`-
driven `pipeline.py`, and log every query's stages, scores, and latencies to
`query_logs` so any answer's provenance can be reconstructed after the fact. Wire this
into `scripts/ask.py`, replacing its current hardcoded, vector-only flow.

This is SPEC.md's Day 6 milestone, sitting on top of Day 1–5 (`ripple/config.py`,
`ripple/db.py`, `ripple/ingest/`, `ripple/llm/`, `ripple/retrieval/vector_store.py` +
`pgvector_store.py` + `bm25.py`, `ripple/retrieval/graph.py`, `scripts/index_repo.py`,
`scripts/ask.py`), all already implemented and verified (84 passing tests as of Day 5).

## 2. Relevant SPEC.md requirements

- Section 11, Day 6: "`fusion.py` per section 9.6. `pipeline.py` reading
  `RetrievalConfig`, running vector and BM25, fusing. Write `query_logs` rows with
  per-stage candidates, scores, and latencies. **Done when:** you can query the log
  table and reconstruct exactly why any block was returned."
- Section 9.6 (Reciprocal rank fusion), quoted verbatim:
  ```python
  def rrf(ranked_lists: list[list[int]], k: int = 60) -> dict[int, float]:
      scores = defaultdict(float)
      for lst in ranked_lists:
          for rank, doc_id in enumerate(lst, start=1):
              scores[doc_id] += 1.0 / (k + rank)
      return scores
  ```
  > `k` is configurable and defaults to 60. Every rewritten query's vector list and
  > BM25 list is a separate input list. Sort descending, take the top 50 into
  > reranking.
  >
  > RRF is used rather than a weighted score sum because cosine similarity and BM25
  > scores are on incomparable scales — normalizing them requires a tuning parameter
  > that RRF avoids. Be able to say this out loud.
  - **This cycle has no query rewriting (Day 15) and no reranking (Day 12).** So:
    exactly one vector list and one BM25 list are fused (not N pairs — see section
    10), and "take the top 50 into reranking" doesn't apply yet — this cycle
    truncates the fused list directly to `RetrievalConfig.final_k` (8 by default).
    Both simplifications are explicit scope decisions, not oversights (section 9).
- Section 9.11 (`RetrievalConfig`, already implemented, unchanged): `pipeline.py`
  reads it and skips stages accordingly. **"When `use_rrf` is false but both
  retrievers are on, concatenate and deduplicate by best rank instead of fusing."**
  This is the one other piece of fusion logic SPEC.md specifies, alongside `rrf()`
  itself — `fusion.py` needs both.
- Section 7 (schema, already exists, unused until now):
  ```sql
  CREATE TABLE query_logs (
      id             SERIAL PRIMARY KEY,
      repo_id        INTEGER REFERENCES repos(id) ON DELETE CASCADE,
      question       TEXT NOT NULL,
      config_json    JSONB NOT NULL,
      stages_json    JSONB NOT NULL,
      latency_json   JSONB NOT NULL,
      answer         TEXT,
      created_at     TIMESTAMPTZ DEFAULT now()
  );
  ```
  `repo_id` and `answer` are nullable in the schema; this plan always sets `repo_id`
  (every query is against a specific repo) but `answer` can legitimately be `NULL`
  (no candidates found, so nothing was ever generated).
- Section 10.2 (latency fields, for naming consistency): "rewrite, vector_query,
  hydrate, bm25, fusion, rerank, graph, total." This cycle populates `vector_query`,
  `bm25`, `fusion`, and `total` only — the rest don't exist yet (see section 9).
  `hydrate` is Pinecone-only and N/A regardless (this project uses `PgVectorStore`).

## 3. Current implementation gaps

- `ripple/retrieval/fusion.py` does not exist.
- `ripple/retrieval/pipeline.py` does not exist. Nothing currently reads
  `RetrievalConfig` at all — Day 1 built the dataclass, nothing has consulted it since.
- `ripple/db.py` has no `query_logs` write function.
- `query_logs` has never been written to (table exists since Day 1's schema, zero rows
  ever inserted).
- `scripts/ask.py` hardcodes a single-stage, vector-only flow
  (`OpenAIEmbeddingProvider` → `PgVectorStore.query` → `answer_question`, `--top-k`
  flag) with no config, no BM25, no fusion, no logging. This is intentional Day 3/5
  scope, not a bug — but it's what this cycle replaces.

## 4. Exact files Codex (or you) should create or modify

Create:
- `ripple/retrieval/fusion.py`
- `ripple/retrieval/pipeline.py`
- `tests/test_fusion.py`
- `tests/test_pipeline.py`

Modify:
- `ripple/db.py` — add `insert_query_log(...)`.
- `scripts/ask.py` — **rewrite**, not extend: `ask()`'s body changes completely (calls
  `pipeline.run_pipeline` instead of directly constructing an embedder/store),
  `--top-k` is replaced by `--final-k` (see 5, Step 4).
- `tests/test_ask.py` — **rewrite**, not extend: every existing test monkeypatches
  `ask_module.OpenAIEmbeddingProvider`/`ask_module.PgVectorStore` directly, neither of
  which `ask.py` imports anymore. All of them need to change to monkeypatch
  `ask_module.pipeline`/`ask_module.db`/`ask_module.answer_question` instead (see 7).

Do not modify: `sql/schema.sql` (schema already correct, unused until now),
`docker-compose.yml`, `.env.example`, `requirements.txt`, `ripple/config.py`
(`RetrievalConfig` is already complete and correct), `ripple/ingest/*`,
`ripple/llm/embeddings.py`, `ripple/llm/prompts.py`, `ripple/llm/generate.py`,
`ripple/retrieval/vector_store.py`, `ripple/retrieval/pgvector_store.py`,
`ripple/retrieval/bm25.py`, `ripple/retrieval/graph.py`, `scripts/index_repo.py`,
`SPEC.md`, `AGENTS.md`, `CLAUDE.md`, `README.md`, and every other existing test file
(`test_config.py`, `test_db.py` — aside from confirming it still passes, plus its own
addition —, `test_scanner.py`, `test_parser.py`, `test_references.py`,
`test_indexer.py`, `test_graph.py`, `test_embeddings.py`, `test_generate.py`,
`test_prompts.py`, `test_pgvector_store.py`, `test_index_repo.py`, `test_bm25.py`).

## 5. Step-by-step implementation order

### Step 1 — `ripple/retrieval/fusion.py`

```python
import dataclasses
from collections import defaultdict

from ripple.retrieval.vector_store import RetrievedBlock


def rrf(ranked_lists: list[list[int]], k: int = 60) -> dict[int, float]:
    """SPEC.md 9.6, verbatim."""
    scores: dict[int, float] = defaultdict(float)
    for lst in ranked_lists:
        for rank, doc_id in enumerate(lst, start=1):
            scores[doc_id] += 1.0 / (k + rank)
    return scores


def fuse(ranked_lists: list[list[RetrievedBlock]], k: int = 60) -> list[RetrievedBlock]:
    """RRF over ranked RetrievedBlock lists, returning one list sorted by
    fused score descending. Each returned block's .score is overwritten with
    its fused RRF score — the original cosine/BM25 score is no longer
    meaningful once blocks from both signals are being compared directly.
    """
    id_lists = [[block.id for block in lst] for lst in ranked_lists]
    scores = rrf(id_lists, k=k)

    best_block: dict[int, RetrievedBlock] = {}
    for lst in ranked_lists:
        for block in lst:
            best_block.setdefault(block.id, block)

    ranked_ids = sorted(
        scores,
        key=lambda doc_id: (-scores[doc_id], best_block[doc_id].address),
    )

    return [
        dataclasses.replace(best_block[doc_id], score=scores[doc_id])
        for doc_id in ranked_ids
    ]


def concat_dedup(ranked_lists: list[list[RetrievedBlock]]) -> list[RetrievedBlock]:
    """SPEC.md 9.11's use_rrf=False fallback: concatenate and deduplicate by
    best rank. A block appearing in multiple lists keeps its best (lowest)
    rank and that occurrence's own (unmodified) score — there is no fused
    score when RRF isn't used.
    """
    best_rank: dict[int, int] = {}
    best_block: dict[int, RetrievedBlock] = {}

    for lst in ranked_lists:
        for rank, block in enumerate(lst, start=1):
            if block.id not in best_rank or rank < best_rank[block.id]:
                best_rank[block.id] = rank
                best_block[block.id] = block

    ranked_ids = sorted(
        best_rank,
        key=lambda doc_id: (best_rank[doc_id], best_block[doc_id].address),
    )
    return [best_block[doc_id] for doc_id in ranked_ids]
```

`rrf()` is reproduced exactly as SPEC.md gives it — plain `dict[int, float]` in, no
`RetrievedBlock` awareness, no sorting (the docstring says "sort descending" is the
caller's job, and `fuse()` is that caller). `fuse()`/`concat_dedup()` both tie-break by
`address` for determinism, matching every prior day's convention (Day 4's `graph.py`,
Day 5's `bm25.py`).

### Step 2 — `db.insert_query_log(...)`

```python
from psycopg.types.json import Jsonb

def insert_query_log(
    repo_id: int,
    question: str,
    config_json: dict,
    stages_json: dict,
    latency_json: dict,
    answer: str | None,
) -> int:
    with get_connection() as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO query_logs
                    (repo_id, question, config_json, stages_json, latency_json, answer)
                VALUES (%s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (
                    repo_id,
                    question,
                    Jsonb(config_json),
                    Jsonb(stages_json),
                    Jsonb(latency_json),
                    answer,
                ),
            )
            return cursor.fetchone()[0]
```

**This is the same lesson as Day 3's `pgvector.Vector` wrapping, applied to JSONB
instead of `vector`.** A bare Python `dict` does not automatically encode as `jsonb`
when bound as a query parameter — wrap every JSON-column value with
`psycopg.types.json.Jsonb(...)` explicitly, the same way `pgvector.Vector(...)`
wraps embeddings. `config_json`/`stages_json`/`latency_json` are plain, already-JSON-
safe `dict`s built by `pipeline.py` (see Step 3) — `db.py` stays decoupled from
`ripple.retrieval`'s types, consistent with `ResourceRowLike`/`EdgeRowLike`'s existing
Protocol-based decoupling. Reading `jsonb` columns back (section 7's test, and the
manual acceptance check) does **not** need any special unwrapping — `psycopg` decodes
`jsonb` into native Python `dict`/`list` automatically; only the write direction needs
the explicit wrapper. Verify this by testing the actual round trip (Step 5), not by
assuming it from this description alone — same caution Day 3's `Vector` bug taught.

### Step 3 — `ripple/retrieval/pipeline.py`

```python
import time
from dataclasses import dataclass

from ripple.config import RetrievalConfig
from ripple.llm.embeddings import EmbeddingProvider, OpenAIEmbeddingProvider
from ripple.retrieval import fusion
from ripple.retrieval.bm25 import build_index
from ripple.retrieval.pgvector_store import PgVectorStore
from ripple.retrieval.vector_store import RetrievedBlock


@dataclass
class PipelineResult:
    blocks: list[RetrievedBlock]
    stages_json: dict[str, list[dict]]
    latency_json: dict[str, float]


def _serialize(blocks: list[RetrievedBlock]) -> list[dict]:
    return [
        {"id": block.id, "address": block.address, "score": block.score}
        for block in blocks
    ]


def run_pipeline(
    repo_id: int,
    question: str,
    config: RetrievalConfig,
    embedder: EmbeddingProvider | None = None,
) -> PipelineResult:
    total_start = time.perf_counter()
    stages_json: dict[str, list[dict]] = {}
    latency_json: dict[str, float] = {}

    vector_results: list[RetrievedBlock] = []
    bm25_results: list[RetrievedBlock] = []

    if config.use_vector:
        start = time.perf_counter()
        embedder = embedder or OpenAIEmbeddingProvider()
        [question_embedding] = embedder.embed([question])
        vector_results = PgVectorStore().query(
            repo_id, question_embedding, config.vector_k
        )
        latency_json["vector_query_ms"] = (time.perf_counter() - start) * 1000
        stages_json["vector"] = _serialize(vector_results)

    if config.use_bm25:
        start = time.perf_counter()
        bm25_results = build_index(repo_id).query(question, config.bm25_k)
        latency_json["bm25_ms"] = (time.perf_counter() - start) * 1000
        stages_json["bm25"] = _serialize(bm25_results)

    if config.use_vector and config.use_bm25:
        start = time.perf_counter()
        if config.use_rrf:
            candidates = fusion.fuse([vector_results, bm25_results], k=config.rrf_k)
        else:
            candidates = fusion.concat_dedup([vector_results, bm25_results])
        latency_json["fusion_ms"] = (time.perf_counter() - start) * 1000
        stages_json["fusion"] = _serialize(candidates)
    elif config.use_vector:
        candidates = vector_results
    elif config.use_bm25:
        candidates = bm25_results
    else:
        candidates = []

    blocks = candidates[: config.final_k]
    latency_json["total_ms"] = (time.perf_counter() - total_start) * 1000

    return PipelineResult(
        blocks=blocks, stages_json=stages_json, latency_json=latency_json
    )
```

Key design points:
- **`embedder` is constructed only when `config.use_vector` is `True`** — same "don't
  require an API key for a path that doesn't need one" discipline as Day 3's
  empty-repository fix. `use_vector=False, use_bm25=True` must work with
  `OPENAI_API_KEY` unset.
- **`latency_json` only gets keys for stages that actually ran.** No `"fusion_ms"`
  unless both retrievers ran; no `"vector_query_ms"` if `use_vector=False`. This
  matters for Day 6's own "Done when": if a stage didn't run, the log shouldn't imply
  it did by having a `0.0` entry for it — see section 10.
- **Exactly two ranked lists are ever fused this cycle** (one vector, one BM25) — not
  N pairs per rewritten query, since there's no query rewriting yet (Day 15).
  `fusion.fuse`/`concat_dedup` both already accept an arbitrary-length list of ranked
  lists, so Day 15 extending this to N query variants' worth of vector+BM25 lists is
  an additive change to `pipeline.py`'s call site, not an interface change to
  `fusion.py`.
- **`use_rerank`, `use_graph`, `use_rewrite` (and `rerank_top_n`, `graph_seed_n`,
  `graph_max_added`) are not read at all this cycle.** Their stages don't exist yet
  (Days 12/8+13/15). Setting them to anything has no effect on current behavior — see
  section 9.
- **Truncation to `final_k` happens directly on the fused/single-stage list.**
  SPEC.md's "take the top 50 into reranking" describes the *eventual* full pipeline
  (Day 12 onward); without a reranker, there's nothing between fusion and the final
  answer, so truncating straight to `final_k` (8, not 50) is correct for this cycle.

### Step 4 — Rewrite `scripts/ask.py`

```python
import argparse
import dataclasses
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ripple import db
from ripple.config import RetrievalConfig
from ripple.llm.generate import answer_question
from ripple.retrieval import pipeline

NO_RESULTS_MESSAGE = (
    "No indexed resources found for this repo — nothing to answer from."
)


def ask(repo_id: int, question: str, config: RetrievalConfig | None = None) -> str:
    config = config or RetrievalConfig()

    result = pipeline.run_pipeline(repo_id, question, config)

    answer = answer_question(question, result.blocks) if result.blocks else None

    db.insert_query_log(
        repo_id=repo_id,
        question=question,
        config_json=dataclasses.asdict(config),
        stages_json=result.stages_json,
        latency_json=result.latency_json,
        answer=answer,
    )

    return answer if answer is not None else NO_RESULTS_MESSAGE


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Ask a question about an indexed Terraform repo"
    )
    parser.add_argument("repo_id", type=int, help="repos.id of the indexed repo to query")
    parser.add_argument("question", help="Natural-language question")
    parser.add_argument(
        "--final-k",
        type=int,
        default=None,
        help="Override RetrievalConfig.final_k (default: RetrievalConfig()'s own default, 8)",
    )
    args = parser.parse_args(argv)

    config = (
        RetrievalConfig(final_k=args.final_k)
        if args.final_k is not None
        else RetrievalConfig()
    )

    print(ask(args.repo_id, args.question, config))


if __name__ == "__main__":
    main()
```

**`--top-k` is renamed to `--final-k`, and `DEFAULT_TOP_K` is removed entirely** —
`RetrievalConfig()` already carries its own default (`final_k=8`), so there's no
reason for `ask.py` to maintain a second, parallel default. This is a deliberate,
visible CLI change, not a silent one — call it out if you're used to the old flag
name.

**Every call to `ask()` logs, including zero-result queries** (`answer=None` in that
case) — this is what makes Day 6's "Done when" possible at all: even a query that
found nothing is diagnostic information (which stages ran, what `k` they used, that
none of them produced candidates), and section 8's acceptance check depends on the
log being unconditional, not just written on success.

### Step 5 — Tests

See section 7 for the full list. Suggested order: `fusion.py`'s pure functions first
(no DB, no mocking), then `pipeline.py` with `PgVectorStore`/`build_index` mocked at
the module level (not hitting real vector/BM25 search — see section 7 for why), then
`db.insert_query_log`'s round trip, then `ask.py`'s full rewrite.

### Step 6 — Manual acceptance check

See section 8. The retrieval-only part costs one small, unavoidable embedding call
(embedding the question itself — trivial next to Day 3/5's indexing-cost concerns);
the full `ask()` call additionally costs one generation call and is called out
separately.

## 6. Interfaces, data structures, and error behavior

- `fusion.rrf(ranked_lists, k=60) -> dict[int, float]` — pure, SPEC.md 9.6 verbatim.
  Never raises; an empty `ranked_lists` (or all-empty inner lists) returns `{}`.
- `fusion.fuse(ranked_lists, k=60) -> list[RetrievedBlock]` — every returned block's
  `.score` is its fused RRF score, not its original cosine/BM25 score. Sorted
  descending by score, tie-broken by address. `[]` in, `[]` out for empty input lists.
- `fusion.concat_dedup(ranked_lists) -> list[RetrievedBlock]` — every returned block
  keeps its *original* score from whichever list gave it the best (lowest-numbered)
  rank; there is no fused score to assign when RRF isn't used. Sorted by best rank
  ascending, tie-broken by address.
- `pipeline.run_pipeline(repo_id, question, config, embedder=None) -> PipelineResult`
  — never raises for a config where `use_vector` and `use_bm25` are both `False`
  (returns an empty `PipelineResult`, no stages run, no error); never constructs an
  `EmbeddingProvider` unless `use_vector` is `True`. Propagates whatever
  `PgVectorStore.query`/`build_index`/the embedder raise, uncaught, for any real
  failure — same "let real failures propagate" posture as every prior day.
- `db.insert_query_log(...)` — always inserts a row and returns its `id`; `answer` may
  be `None`. Raises whatever `psycopg` raises for a genuinely invalid `repo_id`
  (foreign key violation) — not expected in normal use, since `ask()` always operates
  on a `repo_id` it was given by the caller.
- `scripts/ask.py`'s `ask(repo_id, question, config=None)` — always writes exactly one
  `query_logs` row per call, regardless of whether any candidates were found. Returns
  `NO_RESULTS_MESSAGE` (not an exception) when `result.blocks` is empty.

## 7. Required tests

`tests/test_fusion.py` (pure, no DB, no mocking):
- `rrf()`: a small, hand-computed example — e.g. `rrf([[1, 2, 3], [2, 1, 4]], k=60)` —
  verify every score against the formula by hand (doc `2` appears in both lists at
  ranks 2 and 1, so its score is `1/(60+2) + 1/(60+1)`, etc.).
- `rrf([], k=60) == {}`; `rrf([[], []], k=60) == {}`.
- `fuse()`: two small `RetrievedBlock` lists (constructed by hand, distinct `id`s and
  addresses) — assert the fused order matches RRF's formula, and that every returned
  block's `.score` equals its computed RRF score (not its original score).
- `fuse()` tie-break: two blocks landing at identical fused scores (construct this
  deliberately — e.g. two single-element lists, each containing a different block at
  rank 1, with the same `k`) sort by address.
- `concat_dedup()`: a block appearing in both lists at different ranks (e.g. rank 3 in
  list A, rank 1 in list B) ends up positioned by its *best* rank (1), and its
  `.score` is the score it had in list B specifically (the list that gave it that
  best rank), not list A's.
- `concat_dedup()` with a block appearing in only one list: keeps that list's rank and
  score unchanged.
- `concat_dedup()` never overwrites `.score` — a direct contrast test against `fuse()`
  using the identical input lists, asserting the two functions produce different
  `.score` values for the same block.

`tests/test_pipeline.py` — **mock `PgVectorStore` and `build_index` at the module
level** (`monkeypatch.setattr(pipeline, "PgVectorStore", ...)` /
`monkeypatch.setattr(pipeline, "build_index", ...)`), feeding canned
`RetrievedBlock` lists, rather than hitting a real database with real search. This is
deliberate: `pipeline.py`'s own tests should verify *orchestration* (which stages ran,
how they combine, what gets logged) — retrieval *quality* is already covered
thoroughly by Day 3's `test_pgvector_store.py` and Day 5's `test_bm25.py`. Re-testing
ranking quality here would also require carefully-constructed, distinguishable fake
embeddings (recall Day 5's fixtures all share one all-zero fake embedding — fine for
BM25, useless for meaningfully testing vector ranking) — sidestepped entirely by
mocking at this level.
- **Vector only** (`use_vector=True, use_bm25=False`): `build_index` is never called
  (assert via a monkeypatch that raises if invoked); `stages_json` has only a
  `"vector"` key; `latency_json` has `"vector_query_ms"` and `"total_ms"` only, no
  `"bm25_ms"`/`"fusion_ms"`.
- **BM25 only** (`use_vector=False, use_bm25=True`), **with `OPENAI_API_KEY` unset and
  no `embedder` passed**: succeeds without raising — this is the direct regression
  test for "never construct an `EmbeddingProvider` unless `use_vector` is `True`."
  `stages_json` has only a `"bm25"` key.
- **Both enabled, `use_rrf=True`**: mock both sources with distinguishable canned
  results (some overlapping `id`s, some not); assert `fusion.fuse` was actually
  applied — e.g. by checking the returned blocks' `.score` values match hand-computed
  RRF scores, not either mock's original scores. `stages_json` has `"vector"`,
  `"bm25"`, and `"fusion"` keys.
- **Both enabled, `use_rrf=False`**: same setup; assert the `concat_dedup` path was
  taken instead — returned blocks keep their original mock scores, ordered by best
  rank.
- **Both disabled** (`use_vector=False, use_bm25=False`): returns an empty
  `PipelineResult` (`blocks == []`), no error, no `EmbeddingProvider` constructed,
  `stages_json == {}`.
- **`final_k` truncation**: mock results larger than `config.final_k`; assert
  `result.blocks` is truncated, but `stages_json`'s per-stage lists are *not*
  truncated (the log should show everything each stage actually returned, not just
  what survived to the final answer).
- **`latency_json` always has `"total_ms"`**, even when both retrievers are disabled.

`tests/test_db.py` addition — `insert_query_log` round trip (DB-dependent,
skip-if-unreachable, same convention as every prior day):
- Insert a log row with a representative `config_json`/`stages_json`/`latency_json`
  (nested dicts/lists, not just flat key-value pairs) and a non-`None` answer; read it
  back via a raw `SELECT`; assert every field round-trips correctly, **specifically
  confirming the JSONB columns decode back into native Python `dict`/`list` objects
  without any extra unwrapping** — this is the direct proof that `Jsonb(...)` wrapping
  on the write side was necessary and sufficient (Step 2's Day-3-`Vector`-lesson
  callout).
- Insert a log row with `answer=None`; confirm it's stored and read back as `None`,
  not some other falsy value.
- Clean up the `repos` row afterward (cascades to `query_logs` via `ON DELETE
  CASCADE`).

`tests/test_ask.py` — **full rewrite**:
- `ask()`: monkeypatch `ask_module.pipeline.run_pipeline` to return a canned
  `PipelineResult` with non-empty `blocks`; monkeypatch `ask_module.answer_question`
  to a stub; monkeypatch `ask_module.db.insert_query_log` to a recording stub. Assert
  `ask()` returns the stub's answer, and that `insert_query_log` was called with the
  right `repo_id`/`question`/`config_json` (matches `dataclasses.asdict` of the config
  actually used)/`stages_json`/`latency_json`/`answer`.
- `ask()` with an empty-`blocks` canned `PipelineResult`: assert `answer_question` is
  never called, `insert_query_log` is called with `answer=None`, and `ask()` returns
  `NO_RESULTS_MESSAGE`.
- `ask()` with no `config` argument: assert `pipeline.run_pipeline` was called with a
  `RetrievalConfig()` carrying all-default values.
- `main()`: monkeypatch `ask_module.ask` to a stub; run `main(["3", "question"])` with
  no `--final-k`; assert `ask` was called with a plain `RetrievalConfig()`. Run again
  with `--final-k 5`; assert `ask` was called with a config whose `final_k == 5` and
  every other field still at its default.

Run `python -m pytest` after implementation; all tests must pass. DB-dependent tests
skip cleanly if Postgres isn't reachable. `test_pipeline.py` and the rewritten
`test_ask.py` need no `OPENAI_API_KEY` and no real database — everything external is
mocked at the module level.

## 8. Acceptance criteria

- `python -m pytest` passes with no failures, including the full existing suite.
- **Primary manual acceptance check — retrieval only, costs one small embedding call,
  no generation, using already-indexed `repo_id = 13`** (confirmed present since Day
  5, 114 embedded resources):
  ```python
  from ripple.config import RetrievalConfig
  from ripple.retrieval.pipeline import run_pipeline

  result = run_pipeline(13, "What creates the RDS security group?", RetrievalConfig())
  print(result.stages_json)
  print(result.latency_json)
  ```
  Confirm: `stages_json` has `"vector"`, `"bm25"`, and `"fusion"` keys, each a list of
  `{id, address, score}` dicts; `latency_json` has `vector_query_ms`, `bm25_ms`,
  `fusion_ms`, `total_ms`, all positive numbers; `result.blocks` (length ≤ 8) includes
  `aws_security_group.rds` or a clearly related block.
- **Full "Done when" reproduction — reconstruct why a block was returned, from the log
  table alone** — this is the literal Day 6 acceptance bar, and it requires an actual
  `ask()` call, which costs one generation call in addition to the embedding call
  above. **Confirm before running.** If approved:
  ```python
  from scripts.ask import ask
  answer = ask(13, "What creates the RDS security group?")
  ```
  then, separately, query the log table directly:
  ```sql
  SELECT question, config_json, stages_json, latency_json, answer
  FROM query_logs
  ORDER BY id DESC
  LIMIT 1;
  ```
  Confirm you can point at a specific block in the final answer and find it in
  `stages_json`'s `"fusion"` list (or `"vector"`/`"bm25"` if it came from only one
  signal), with a score explaining its rank — that reconstruction, from SQL alone, is
  exactly what "Done when" asks for.

## 9. Explicit non-goals

- **`use_rerank`, `use_graph`, `use_rewrite` are not read by `pipeline.py` this
  cycle.** Their stages (`rerank.py`, graph-expansion wiring, `rewrite.py`) don't
  exist yet (Days 12, 8+13, 15). Setting these flags to `False` has no observable
  effect on current behavior — there's nothing to skip. This is a real, temporary gap
  between what `RetrievalConfig` *declares* and what `pipeline.py` *honors*; it closes
  incrementally as each stage's day arrives.
- **`rerank_top_n`, `graph_seed_n`, `graph_max_added` are likewise unread.**
- Query rewriting itself, and the "N vector lists + N BM25 lists, one pair per
  rewritten query" fusion shape it implies — Day 15. This cycle always fuses exactly
  one vector list and one BM25 list.
- Cross-encoder reranking, and the "top 50 into reranking" truncation point SPEC.md
  describes — Day 12. This cycle truncates directly to `final_k` after fusion.
- Graph expansion wiring into the pipeline or the prompt — Day 8 (first pipeline
  wiring of graph context) and Day 13 (the dedicated graph-expansion day with real
  `graph_seed_n`/`graph_max_added` limits). `graph.py` (Day 4) is untouched.
- `PineconeStore` — still not built; `hydrate_ms` stays absent from `latency_json`
  regardless (N/A for `PgVectorStore`, which is the only backend this project runs).
- The FastAPI app (Day 17) — `scripts/ask.py` remains a CLI.
- Modifying `SPEC.md` or `sql/schema.sql` — the `query_logs` table is already correct
  as specified since Day 1; nothing here needs a schema change.

## 10. Risks, ambiguities, and things flagged for your review

- **`config_json`/`stages_json`/`latency_json` field-naming and shape are this plan's
  own design, not SPEC.md's.** SPEC.md says *what* must be logged ("per-stage
  candidates, scores, and latencies," "which stages were on") but not the exact JSON
  shape. This plan's choices — `stages_json` keyed by stage name
  (`"vector"`/`"bm25"`/`"fusion"`) each holding a list of `{id, address, score}`
  dicts; `latency_json` keyed `"<stage>_ms"`; `config_json` as a flat
  `dataclasses.asdict(config)` — are reasonable and directly support section 8's
  "Done when," but they're a plan-level design decision worth your explicit awareness
  since nothing forces this exact shape. Changing it later is a `pipeline.py`-only
  change; nothing else depends on the JSON's internal structure.
- **Only `id`/`address`/`score` are logged per candidate, not full `body`/
  `file_path`/line numbers.** Deliberate: that data already lives in `resources`,
  addressable by `id`; duplicating full block bodies into every `query_logs` row would
  bloat the table for no diagnostic benefit `id` doesn't already provide (join back to
  `resources` when you need the body). If deeper log rows are wanted later, that's an
  additive change to `_serialize()`.
- **Embedding time is folded into `vector_query_ms`, not logged separately.**
  SPEC.md's field list has no distinct "embed the question" key — reasonable to read
  this as intentionally bundled into "vector_query" from the pipeline's point of view.
  Revisit if this granularity ever matters (e.g. to separately track embedding-API
  latency vs. the Postgres query itself).
- **`--top-k` → `--final-k` is a breaking CLI rename**, not an additive change. Anyone
  with a memorized `--top-k` invocation needs to know it changed. Flagged here rather
  than silently done, per this project's own established pattern for user-visible
  behavior changes.
- **`RetrievalConfig`'s declared defaults (`use_rerank=True`, `use_graph=True`,
  `use_rewrite=True`) are currently meaningless** — `pipeline.py` ignores them
  entirely this cycle (see section 9). A caller reading only `RetrievalConfig`'s
  defaults, without reading `pipeline.py`'s source, could reasonably but incorrectly
  assume reranking/graph/rewriting are active. This resolves itself day by day as each
  stage is wired in; not something to fix now, just something to be aware isn't fixed
  yet.
- **Floating-point score equality for tie-breaks.** `fuse()`'s RRF scores are sums of
  `1/(k+rank)` terms — exact ties are more likely here than with raw cosine/BM25
  scores (e.g. two blocks each appearing in exactly one list, both at rank 1, with
  identical `k`, produce identical fused scores). The `address` tie-break handles this
  deterministically; called out because Day 4/5 both needed the same fix once
  reviewed, and it's better to have it right from the first draft this time.
