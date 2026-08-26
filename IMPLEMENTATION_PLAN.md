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
- `PgVectorStore.query()` (Day 3, frozen this cycle) has no guard against a
  non-positive `k` — it binds `k` straight into a raw SQL `LIMIT %s`, and a negative
  `LIMIT` value is a genuine PostgreSQL runtime error ("LIMIT must not be negative").
  This cycle's `pipeline.py` must never let a non-positive `vector_k` reach it (see
  section 5, Step 3, and section 10).

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
  `ask_module.pipeline`/`ask_module.db`/`ask_module.answer_question` instead. Also
  adds one new DB-dependent integration test (see 7, item 5 from this review).

Do not modify: `sql/schema.sql` (schema already correct, unused until now),
`docker-compose.yml`, `.env.example`, `requirements.txt`, `ripple/config.py`
(`RetrievalConfig` is already complete and correct), `ripple/ingest/*`,
`ripple/llm/embeddings.py`, `ripple/llm/prompts.py`, `ripple/llm/generate.py`,
`ripple/retrieval/vector_store.py`, `ripple/retrieval/pgvector_store.py` (its lack of
a `k<=0` guard is worked around from `pipeline.py`, not patched here — see Step 3),
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
    """SPEC.md 9.6, verbatim, plus one guard: k < 0 raises instead of risking
    division by zero (k + rank == 0, when rank is small enough) or silently
    nonsensical negative score contributions. Every k >= 0 behaves exactly as
    SPEC.md specifies -- this never changes output for a valid k.
    """
    if k < 0:
        raise ValueError(f"rrf's k must be non-negative, got {k}")

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
    Propagates rrf()'s ValueError unchanged for k < 0.
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

`rrf()` is reproduced exactly as SPEC.md gives it for every valid input — the `k < 0`
guard is new, added because negative `rrf_k` is a real, reachable misconfiguration
(section 10 explains why it raises instead of returning `{}`, unlike the count-style
`k` parameters elsewhere in this project). `fuse()`/`concat_dedup()` both tie-break by
`address` for determinism, matching every prior day's convention.

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
safe `dict`s built entirely by `pipeline.py` (see Step 3) — `db.py` stays decoupled
from `ripple.retrieval`'s types, consistent with `ResourceRowLike`/`EdgeRowLike`'s
existing Protocol-based decoupling. Reading `jsonb` columns back (section 7's test,
and the manual acceptance check) does **not** need any special unwrapping — `psycopg`
decodes `jsonb` into native Python `dict`/`list` automatically; only the write
direction needs the explicit wrapper. Verify this by testing the actual round trip
(section 7), not by assuming it from this description alone.

### Step 3 — `ripple/retrieval/pipeline.py`

```python
import dataclasses
import time
from dataclasses import dataclass

from ripple.config import RetrievalConfig
from ripple.llm.embeddings import EmbeddingProvider, OpenAIEmbeddingProvider
from ripple.retrieval import fusion
from ripple.retrieval.bm25 import build_index
from ripple.retrieval.pgvector_store import PgVectorStore
from ripple.retrieval.vector_store import RetrievedBlock, VectorStore

SUPPORTED_VECTOR_BACKENDS = ("pgvector",)


@dataclass
class PipelineResult:
    blocks: list[RetrievedBlock]
    config_json: dict
    stages_json: dict[str, list[dict]]
    latency_json: dict[str, float]


def _serialize(blocks: list[RetrievedBlock]) -> list[dict]:
    return [
        {"id": block.id, "address": block.address, "score": block.score}
        for block in blocks
    ]


def _get_vector_store(config: RetrievalConfig) -> VectorStore:
    """Return the VectorStore for config.vector_backend, or reject an
    unsupported one explicitly. PineconeStore doesn't exist yet (Day 20) --
    silently falling back to PgVectorStore for an unrecognized backend would
    hide a real configuration mistake. Callers only see the VectorStore
    Protocol, so adding PineconeStore later is a one-branch change here, not
    a change to anything that calls this function.
    """
    if config.vector_backend == "pgvector":
        return PgVectorStore()
    raise ValueError(
        f"Unsupported vector_backend {config.vector_backend!r}; "
        f"only {SUPPORTED_VECTOR_BACKENDS} are implemented"
    )


def _build_config_json(config: RetrievalConfig) -> dict:
    """Requested vs. executed, explicitly separated. config.use_rerank,
    use_graph, and use_rewrite default to True, but pipeline.py runs none of
    those stages yet -- logging only dataclasses.asdict(config) would make
    the log claim they ran when they didn't. "requested" preserves the raw
    config as asked for; "executed" is grounded in what this function
    actually does, hardcoding False for every stage that doesn't exist yet
    regardless of what was requested.
    """
    fusion_will_run = config.use_vector and config.use_bm25
    return {
        "requested": dataclasses.asdict(config),
        "executed": {
            "vector": config.use_vector,
            "bm25": config.use_bm25,
            "fusion": fusion_will_run,
            "fusion_method": (
                "rrf"
                if fusion_will_run and config.use_rrf
                else "concat_dedup"
                if fusion_will_run
                else None
            ),
            "rerank": False,
            "graph": False,
            "rewrite": False,
        },
    }


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
        vector_store = _get_vector_store(config)  # raises before any API call
        start = time.perf_counter()

        if config.vector_k > 0:
            embedder = embedder or OpenAIEmbeddingProvider()
            [question_embedding] = embedder.embed([question])
            vector_results = vector_store.query(
                repo_id, question_embedding, config.vector_k
            )
        # vector_k <= 0: skip the query entirely -- config.vector_k is bound
        # into a raw SQL LIMIT downstream, and a negative LIMIT is a
        # PostgreSQL error, not a Python one. Treated as "zero vector
        # results requested," matching bm25/final_k's identical convention.

        latency_json["vector_query_ms"] = (time.perf_counter() - start) * 1000
        stages_json["vector"] = _serialize(vector_results)

    if config.use_bm25:
        start = time.perf_counter()
        # BM25Index.query already returns [] for k <= 0 (Day 5) -- no
        # separate guard needed here, unlike vector_k above.
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

    # config.final_k <= 0 means "zero final results," not Python's
    # candidates[:-1]-style negative-slice reinterpretation -- explicit
    # guard, not left to slice semantics.
    blocks = candidates[: config.final_k] if config.final_k > 0 else []
    stages_json["final"] = _serialize(blocks)
    latency_json["total_ms"] = (time.perf_counter() - total_start) * 1000

    return PipelineResult(
        blocks=blocks,
        config_json=_build_config_json(config),
        stages_json=stages_json,
        latency_json=latency_json,
    )
```

Key design points, several revised from an earlier draft after review:
- **`_get_vector_store` is called, and can raise, before any embedding API call or
  database query.** A misconfigured `vector_backend` fails immediately and cheaply,
  not after already having spent an OpenAI call.
- **`config_json` is a `{"requested": ..., "executed": ...}` structure, not a bare
  `dataclasses.asdict(config)`.** This is the direct fix for "the log must not falsely
  imply `use_rerank`/`use_graph`/`use_rewrite` ran" — `"executed"` hardcodes `False`
  for all three regardless of what was requested, while `"requested"` still shows the
  original config faithfully. See section 10 for why this shape, not a schema change.
- **`embedder` is constructed only when `config.use_vector` is `True` *and*
  `config.vector_k > 0`** — same "don't require an API key for a path that doesn't
  need one" discipline as Day 3's empty-repository fix, now also covering the
  "vector is on but asked for zero results" case.
- **Numeric boundaries are handled consistently: a non-positive *count* (`final_k`,
  `vector_k`, `bm25_k`) means "zero results for that stage," never an error; a
  non-positive `rrf_k` is not a count, has no sensible "zero results" reading (it's a
  smoothing constant inside a division), and raises `ValueError` instead of guessing.**
  This is why `rrf_k < 0` only ever surfaces when fusion actually runs (`use_vector`
  and `use_bm25` both `True`, and `use_rrf=True`) — an unused, invalid `rrf_k` sitting
  in a config that never reaches fusion is harmless, matching the same
  don't-validate-what-you-don't-use posture as `vector_backend`.
- **`stages_json["final"]` is always present** and always exactly matches
  `PipelineResult.blocks`, serialized — this is what lets the log show precisely which
  blocks were actually sent to `answer_question`, not just what each retrieval stage
  produced before truncation.
- **`latency_json` only gets keys for stages that actually ran** (`"total_ms"` is
  always present). No `"fusion_ms"` unless both retrievers ran; no
  `"vector_query_ms"` if `use_vector=False`.
- **Exactly two ranked lists are ever fused this cycle** (one vector, one BM25) — not
  N pairs per rewritten query, since there's no query rewriting yet (Day 15).
  `fusion.fuse`/`concat_dedup` both already accept an arbitrary-length list of ranked
  lists, so Day 15 extending this to N query variants' worth of vector+BM25 lists is
  an additive change to `pipeline.py`'s call site, not an interface change to
  `fusion.py`.
- **`use_rerank`, `use_graph`, `use_rewrite` (and `rerank_top_n`, `graph_seed_n`,
  `graph_max_added`) are not read at all this cycle.** Their stages don't exist yet
  (Day 12 for reranking, Day 13 for graph expansion wiring, Day 15 for rewriting).
  Setting them to anything has no effect on current behavior; `config_json["executed"]`
  reflects this honestly (see above).
- **Truncation to `final_k` happens directly on the fused/single-stage list.**
  SPEC.md's "take the top 50 into reranking" describes the *eventual* full pipeline
  (Day 12 onward); without a reranker, there's nothing between fusion and the final
  answer, so truncating straight to `final_k` (8, not 50) is correct for this cycle.

### Step 4 — Rewrite `scripts/ask.py`

```python
import argparse
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
        config_json=result.config_json,
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

`ask()` no longer builds `config_json` itself — it uses `result.config_json` exactly
as `pipeline.run_pipeline` built it (Step 3), so the requested-vs-executed distinction
is guaranteed to reach the log correctly without `ask.py` needing to know anything
about it.

**Correction from an earlier draft — read carefully:** "every call to `ask()` logs"
is only true for calls that **complete without raising**. If `pipeline.run_pipeline`
raises (an unsupported `vector_backend`, an invalid `rrf_k` reaching fusion, a real
database or OpenAI failure) or `answer_question` raises (a real OpenAI generation
failure), the exception propagates to the caller **before** `db.insert_query_log` is
ever reached — no row is written for that attempt. The **zero-candidates case is not
a failure**: `result.blocks == []` doesn't raise anything, `answer` is set to `None`,
and the log row is still written — that's genuinely unconditional. But a genuine
error is not logged at all. **Logging failed `ask()` attempts is explicitly out of
scope this cycle** — see section 9. This isn't a gap being silently accepted; it's a
real limitation on section 8's "reconstruct why any block was returned" promise,
which only ever applies to *completed* queries.

### Step 5 — Tests

See section 7 for the full list. Suggested order: `fusion.py`'s pure functions first
(no DB, no mocking), then `pipeline.py` with `PgVectorStore`/`build_index` mocked at
the module level (not hitting real vector/BM25 search — see section 7 for why), then
`db.insert_query_log`'s round trip, then `ask.py`'s rewritten unit tests, then the new
end-to-end integration test (7, item 5).

### Step 6 — Manual acceptance check

See section 8. The retrieval-only part costs one small, unavoidable embedding call
(embedding the question itself — trivial next to Day 3/5's indexing-cost concerns);
the full `ask()` call additionally costs one generation call and is called out
separately.

## 6. Interfaces, data structures, and error behavior

- `fusion.rrf(ranked_lists, k=60) -> dict[int, float]` — pure, SPEC.md 9.6 verbatim
  for `k >= 0`. Raises `ValueError` for `k < 0`. An empty `ranked_lists` (or all-empty
  inner lists) returns `{}`.
- `fusion.fuse(ranked_lists, k=60) -> list[RetrievedBlock]` — every returned block's
  `.score` is its fused RRF score, not its original cosine/BM25 score. Sorted
  descending by score, tie-broken by address. `[]` in, `[]` out for empty input lists.
  Raises `ValueError` for `k < 0` (propagated from `rrf()`).
- `fusion.concat_dedup(ranked_lists) -> list[RetrievedBlock]` — every returned block
  keeps its *original* score from whichever list gave it the best (lowest-numbered)
  rank; there is no fused score to assign when RRF isn't used. Sorted by best rank
  ascending, tie-broken by address. Never raises for any input shape.
- `pipeline.run_pipeline(repo_id, question, config, embedder=None) -> PipelineResult`:
  - Never raises for a config where `use_vector` and `use_bm25` are both `False`
    (returns an empty `PipelineResult`: `blocks == []`, `stages_json == {"final": []}`).
  - Never constructs an `EmbeddingProvider` unless `use_vector` is `True` *and*
    `config.vector_k > 0`.
  - Raises `ValueError` immediately if `use_vector` is `True` and `config.vector_backend`
    is not `"pgvector"` — before any embedding call.
  - Raises `ValueError` if fusion actually runs (`use_vector`, `use_bm25`, and
    `use_rrf` all `True`) and `config.rrf_k < 0`. Does **not** raise for an invalid
    `rrf_k` that fusion never reaches (either retriever off, or `use_rrf=False`).
  - Treats `config.vector_k <= 0`, `config.bm25_k <= 0`, and `config.final_k <= 0`
    identically: that stage/the final result contributes zero candidates, never an
    error.
  - Propagates whatever `PgVectorStore.query`/`build_index`/the embedder raise,
    uncaught, for any other real failure.
- `pipeline.PipelineResult.config_json` — `{"requested": dict, "executed": dict}`.
  `"requested"` is `dataclasses.asdict(config)` unmodified. `"executed"` has boolean
  keys `vector`/`bm25`/`fusion`/`rerank`/`graph`/`rewrite` (the last three always
  `False` this cycle) plus `fusion_method` (`"rrf"`, `"concat_dedup"`, or `None`).
- `pipeline.PipelineResult.stages_json` — keys present only for stages that ran
  (`"vector"`, `"bm25"`, `"fusion"` conditionally), plus `"final"`, which is **always**
  present and always equals `_serialize(PipelineResult.blocks)` exactly.
- `db.insert_query_log(...)` — always inserts a row and returns its `id`; `answer` may
  be `None`. Raises whatever `psycopg` raises for a genuinely invalid `repo_id`
  (foreign key violation) — not expected in normal use, since `ask()` always operates
  on a `repo_id` it was given by the caller.
- `scripts/ask.py`'s `ask(repo_id, question, config=None)` — writes exactly one
  `query_logs` row **for every call that completes without raising**, including the
  zero-candidates case (`answer=None`). Does **not** log a call where
  `pipeline.run_pipeline` or `answer_question` raises — the exception propagates
  uncaught and no row is written for that attempt (see Step 4 and section 9). Returns
  `NO_RESULTS_MESSAGE` (not an exception) when `result.blocks` is empty.

## 7. Required tests

`tests/test_fusion.py` (pure, no DB, no mocking):
- `rrf()`: a small, hand-computed example — e.g. `rrf([[1, 2, 3], [2, 1, 4]], k=60)` —
  verify every score against the formula by hand (doc `2` appears in both lists at
  ranks 2 and 1, so its score is `1/(60+2) + 1/(60+1)`, etc.).
- `rrf([], k=60) == {}`; `rrf([[], []], k=60) == {}`.
- `rrf(..., k=-1)` raises `ValueError`.
- `fuse()`: two small `RetrievedBlock` lists (constructed by hand, distinct `id`s and
  addresses) — assert the fused order matches RRF's formula, and that every returned
  block's `.score` equals its computed RRF score (not its original score).
- `fuse()` tie-break: two blocks landing at identical fused scores (construct this
  deliberately — e.g. two single-element lists, each containing a different block at
  rank 1, with the same `k`) sort by address.
- `fuse(..., k=-1)` raises `ValueError` (propagated from `rrf()`).
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

Stage orchestration:
- **Vector only** (`use_vector=True, use_bm25=False`): `build_index` is never called
  (assert via a monkeypatch that raises if invoked); `stages_json` has `"vector"` and
  `"final"` keys only; `latency_json` has `"vector_query_ms"` and `"total_ms"` only.
- **BM25 only** (`use_vector=False, use_bm25=True`), **with `OPENAI_API_KEY` unset and
  no `embedder` passed**: succeeds without raising — direct regression test for
  "never construct an `EmbeddingProvider` unless `use_vector` is `True`."
- **Both enabled, `use_rrf=True`**: mock both sources with distinguishable canned
  results (some overlapping `id`s, some not); assert `fusion.fuse` was actually
  applied (returned blocks' `.score` values match hand-computed RRF scores, not
  either mock's original scores). `stages_json` has `"vector"`, `"bm25"`, `"fusion"`,
  and `"final"` keys.
- **Both enabled, `use_rrf=False`**: same setup; assert the `concat_dedup` path was
  taken instead — returned blocks keep their original mock scores, ordered by best
  rank.
- **Both disabled**: returns an empty `PipelineResult` (`blocks == []`,
  `stages_json == {"final": []}`), no error, no `EmbeddingProvider` constructed.

Configuration honesty (finding 1):
- **`config_json["requested"]` vs `config_json["executed"]`**: build a config with
  `use_rerank=True` (the actual default), `use_vector=True`, `use_bm25=False`; assert
  `config_json["requested"]["use_rerank"] is True` (unmodified) **and**
  `config_json["executed"]["rerank"] is False`, `["vector"] is True`,
  `["bm25"] is False`, `["fusion"] is False`, `["fusion_method"] is None`.
- **`fusion_method` reflects which fusion path actually ran**: both retrievers on +
  `use_rrf=True` → `"rrf"`; both on + `use_rrf=False` → `"concat_dedup"`; only one
  retriever on → `None`.

`vector_backend` handling (finding 2):
- `RetrievalConfig(vector_backend="pinecone", use_vector=True)`: `run_pipeline` raises
  `ValueError` before constructing any embedder or calling `PgVectorStore`
  (monkeypatch `PgVectorStore`/the embedder to raise if invoked, proving neither was
  reached).
- `RetrievalConfig(vector_backend="pinecone", use_vector=False)`: does **not** raise —
  an unsupported backend sitting unused in a config that never touches vector search
  is harmless, matching `rrf_k`'s identical "unused, unvalidated" posture.

Numeric boundaries (finding 3):
- `final_k=0`: `result.blocks == []`.
- `final_k=-1`: `result.blocks == []` — **the direct regression test for the
  `candidates[:-1]` negative-slice bug** (construct a candidate list where
  `candidates[:-1]` would visibly be non-empty and wrong, to prove the fix, not just
  that an empty-candidates case happens to also produce `[]`).
- `vector_k=0` and `vector_k=-1` (`use_vector=True`): mock `PgVectorStore.query` to
  raise if called at all; assert `run_pipeline` doesn't raise and
  `stages_json["vector"] == []` — proves a bad `vector_k` never reaches the real
  `LIMIT` clause.
- `bm25_k=0` and `bm25_k=-1` (`use_bm25=True`): mock `build_index` to return a fake
  index whose `.query(question, k)` faithfully reproduces Day 5's `k <= 0 -> []` rule;
  assert `stages_json["bm25"] == []` and no exception.
- `rrf_k=-1`, both retrievers on, `use_rrf=True`: `run_pipeline` raises `ValueError`.
- `rrf_k=-1`, both retrievers on, `use_rrf=False`: does **not** raise (`concat_dedup`
  never calls `rrf()`).
- `rrf_k=-1`, only one retriever on (either): does **not** raise (fusion never
  attempted regardless of `use_rrf`).

Final-stage observability (finding 4):
- **`stages_json["final"]` matches `PipelineResult.blocks` exactly**: for a case with
  more candidates than `final_k`, assert `stages_json["final"]` equals
  `_serialize(result.blocks)` (same `id`s, same order, same length — truncated
  identically), while `stages_json["vector"]`/`"bm25"`/`"fusion"` remain
  *un*truncated (the log shows what each stage actually returned, separately from
  what survived to the final answer).
- `"final"` is present even when both retrievers are disabled (`stages_json["final"]
  == []`).

`tests/test_db.py` addition — `insert_query_log` round trip (DB-dependent,
skip-if-unreachable, same convention as every prior day):
- Insert a log row with a representative `config_json` (including the
  `{"requested": ..., "executed": ...}` shape) and `stages_json` (including a
  `"final"` key) — nested dicts/lists, not just flat key-value pairs — and a
  non-`None` answer; read it back via a raw `SELECT`; assert every field round-trips
  correctly, **specifically confirming the JSONB columns decode back into native
  Python `dict`/`list` objects without any extra unwrapping** — the direct proof that
  `Jsonb(...)` wrapping on the write side was necessary and sufficient.
- Insert a log row with `answer=None`; confirm it's stored and read back as `None`.
- Clean up the `repos` row afterward (cascades to `query_logs` via `ON DELETE
  CASCADE`).

`tests/test_ask.py` — **full rewrite** of the existing unit tests, **plus one new
integration test**:
- `ask()`: monkeypatch `ask_module.pipeline.run_pipeline` to return a canned
  `PipelineResult` (with a real `config_json`/`stages_json`/`latency_json` shape) with
  non-empty `blocks`; monkeypatch `ask_module.answer_question` to a stub; monkeypatch
  `ask_module.db.insert_query_log` to a recording stub. Assert `ask()` returns the
  stub's answer, and that `insert_query_log` was called with
  `config_json=result.config_json`/`stages_json=result.stages_json`/
  `latency_json=result.latency_json` — i.e. passed through unmodified, not
  reconstructed by `ask.py` itself.
- `ask()` with an empty-`blocks` canned `PipelineResult`: assert `answer_question` is
  never called, `insert_query_log` is called with `answer=None`, and `ask()` returns
  `NO_RESULTS_MESSAGE`.
- `ask()` with no `config` argument: assert `pipeline.run_pipeline` was called with a
  `RetrievalConfig()` carrying all-default values.
- `main()`: monkeypatch `ask_module.ask` to a stub; run `main(["3", "question"])` with
  no `--final-k`; assert `ask` was called with a plain `RetrievalConfig()`. Run again
  with `--final-k 5`; assert `ask` was called with a config whose `final_k == 5` and
  every other field still at its default.
- **New, required (finding 5) — `test_ask_writes_a_fully_reconstructable_query_log`,
  DB-dependent, skip-if-unreachable, makes no OpenAI calls:**
  1. Register a throwaway `repos` row.
  2. Monkeypatch `ask_module.pipeline.run_pipeline` to return a canned
     `PipelineResult` with a realistic `config_json` (`requested`/`executed`),
     `stages_json` (including `"final"`), and `latency_json`.
  3. Monkeypatch `ask_module.answer_question` to return a canned answer string
     (no real generation call).
  4. Call `ask_module.ask(repo_id, "a real question")` for real — this exercises the
     actual `db.insert_query_log` call against a real database, not a mock of it.
  5. Query `query_logs` directly (`SELECT question, config_json, stages_json,
     latency_json, answer FROM query_logs WHERE repo_id = %s`) and assert every
     column matches the canned data exactly, including that `stages_json` contains
     the `"final"` key.
  6. Clean up by deleting the `repos` row (cascades to `query_logs`).
  This is the literal `ask() -> pipeline result -> db.insert_query_log -> readable
  query_logs row` chain the review asked for, proven end to end without touching
  OpenAI.

Run `python -m pytest` after implementation; all tests must pass. DB-dependent tests
skip cleanly if Postgres isn't reachable. Everything in `test_fusion.py` and
`test_pipeline.py`, and all but the one new integration test in `test_ask.py`, need no
`OPENAI_API_KEY` and no real database.

## 8. Acceptance criteria

- `python -m pytest` passes with no failures, including the full existing suite.
- **Primary manual acceptance check — retrieval only, costs one small embedding call,
  no generation, using already-indexed `repo_id = 13`** (confirmed present since Day
  5, 114 embedded resources):
  ```python
  from ripple.config import RetrievalConfig
  from ripple.retrieval.pipeline import run_pipeline

  result = run_pipeline(13, "What creates the RDS security group?", RetrievalConfig())
  print(result.config_json)
  print(result.stages_json)
  print(result.latency_json)
  ```
  Confirm: `config_json["executed"]` shows `vector`/`bm25`/`fusion` all `True` and
  `rerank`/`graph`/`rewrite` all `False`, even though `config_json["requested"]`
  shows the default `True` values for the latter three; `stages_json` has `"vector"`,
  `"bm25"`, `"fusion"`, and `"final"` keys, each a list of `{id, address, score}`
  dicts, with `"final"` matching `result.blocks` exactly; `latency_json` has
  `vector_query_ms`, `bm25_ms`, `fusion_ms`, `total_ms`, all positive numbers;
  `result.blocks` (length ≤ 8) includes `aws_security_group.rds` or a clearly related
  block.
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
  `stages_json`'s `"final"` list (and trace it further back into `"fusion"`,
  `"vector"`, and/or `"bm25"`), with scores at each stage explaining its rank — that
  reconstruction, from SQL alone, is exactly what "Done when" asks for. This check
  only demonstrates the *successful* path — see Step 4/section 9 for why a failed
  `ask()` call would leave no row to reconstruct anything from.

## 9. Explicit non-goals

- **`use_rerank`, `use_graph`, `use_rewrite` are not read by `pipeline.py` this
  cycle.** Their stages don't exist yet: reranking is Day 12, graph-expansion pipeline
  wiring is **Day 13** (Day 8 is benchmark-question construction — unrelated, see the
  correction below), rewriting is Day 15. Setting these flags to `False` has no
  observable effect on current behavior — there's nothing to skip.
  `config_json["executed"]` reflects this honestly regardless of what was requested
  (section 5, Step 3).
- **`rerank_top_n`, `graph_seed_n`, `graph_max_added` are likewise unread.**
- Query rewriting itself, and the "N vector lists + N BM25 lists, one pair per
  rewritten query" fusion shape it implies — Day 15. This cycle always fuses exactly
  one vector list and one BM25 list.
- Cross-encoder reranking, and the "top 50 into reranking" truncation point SPEC.md
  describes — Day 12. This cycle truncates directly to `final_k` after fusion.
- **Graph expansion wiring into the pipeline or the prompt — Day 13.** (Corrected: an
  earlier draft of this plan said "Day 8+13." Day 8 is "Benchmark, first half" — 20
  labeled questions and `dataset.py`'s validator, per SPEC.md section 11 — entirely
  unrelated to graph or pipeline wiring. Day 13 is the only day that wires graph
  expansion into retrieval.) `graph.py` (Day 4) is untouched this cycle.
- `PineconeStore` — still not built; rejected explicitly if requested via
  `vector_backend` (section 5, Step 3) rather than silently ignored. `hydrate_ms`
  stays absent from `latency_json` regardless (N/A for `PgVectorStore`).
- **Logging failed `ask()` calls.** If `pipeline.run_pipeline` or `answer_question`
  raises, no `query_logs` row is written for that attempt — the exception propagates
  to the caller uncaught, same posture as every other failure in this project. This
  is a real, honestly-stated scope boundary, not an oversight: "every `ask()` call is
  logged" (section 8) is true only for calls that complete without raising, including
  the zero-candidates case (which completes cleanly with `answer=None`). Adding
  failure logging would mean wrapping `run_pipeline`/`answer_question` in
  `try`/`except` and deciding what partial `config_json`/`stages_json` to persist for
  a run that didn't finish — a real design question this plan defers rather than
  answers under review pressure.
- Patching `PgVectorStore` itself to guard against `k <= 0` — the guard lives in
  `pipeline.py` instead (section 5, Step 3), leaving Day 3's frozen file untouched.
- The FastAPI app (Day 17) — `scripts/ask.py` remains a CLI.
- Modifying `SPEC.md` or `sql/schema.sql` — the `query_logs` table is already correct
  as specified since Day 1; nothing here needs a schema change. The
  requested-vs-executed distinction (finding 1) is expressed entirely within the
  existing `config_json` `JSONB` column's structure, not a new column.

## 10. Risks, ambiguities, and things flagged for your review

- **`config_json`/`stages_json`/`latency_json` field-naming and shape are this plan's
  own design, not SPEC.md's.** SPEC.md says *what* must be logged ("per-stage
  candidates, scores, and latencies," "which stages were on") but not the exact JSON
  shape, and says nothing at all about a requested-vs-executed split — that's this
  plan's answer to finding 1, not a spec requirement. Reasonable and directly
  supports section 8's "Done when," but worth your explicit awareness since nothing
  forces this exact shape. Changing it later is a `pipeline.py`-only change; nothing
  else depends on the JSON's internal structure.
- **Why `rrf_k < 0` raises but `final_k`/`vector_k`/`bm25_k` <= 0 don't.** All four
  are `RetrievalConfig` integers, which invites treating them uniformly, but they
  mean different things: `final_k`/`vector_k`/`bm25_k` are *counts* ("how many
  results do you want"), and zero or negative sensibly means "none" — this matches
  `BM25Index.query`'s existing `k <= 0 -> []` precedent from Day 5 exactly. `rrf_k` is
  not a count; it's a smoothing constant inside `1/(k+rank)`, and a negative value
  doesn't have a sensible "give zero results" reading — it would either divide by
  zero or silently corrupt the fused ranking for some documents but not others,
  depending on their rank. Raising is the honest choice there; silently returning
  `[]` would hide a real configuration bug behind indistinguishable "not found"
  behavior. This is a single coherent policy (validate what "zero" can honestly mean;
  raise where it can't), not two unrelated rules.
- **Only `id`/`address`/`score` are logged per candidate, not full `body`/
  `file_path`/line numbers.** Deliberate: that data already lives in `resources`,
  addressable by `id`; duplicating full block bodies into every `query_logs` row would
  bloat the table for no diagnostic benefit `id` doesn't already provide. If deeper
  log rows are wanted later, that's an additive change to `_serialize()`.
- **Embedding time is folded into `vector_query_ms`, not logged separately.**
  SPEC.md's field list has no distinct "embed the question" key — reasonable to read
  this as intentionally bundled into "vector_query" from the pipeline's point of view.
- **`--top-k` → `--final-k` is a breaking CLI rename**, not an additive change. Anyone
  with a memorized `--top-k` invocation needs to know it changed.
- **`RetrievalConfig`'s declared defaults (`use_rerank=True`, `use_graph=True`,
  `use_rewrite=True`) are still, after this revision, not honored by `pipeline.py`.**
  What changed is that the *log* no longer lies about it (`config_json["executed"]`
  is always accurate) — the underlying gap between what the dataclass declares and
  what actually runs is unchanged, and closes incrementally as each stage's day
  arrives. Anyone reading only `RetrievalConfig`'s field defaults, without reading
  `config_json["executed"]` or `pipeline.py`'s source, could still reasonably but
  incorrectly assume reranking/graph/rewriting are active — the log now corrects
  that assumption after the fact, but doesn't prevent someone from making it in
  advance of running a query.
- **Floating-point score equality for tie-breaks.** `fuse()`'s RRF scores are sums of
  `1/(k+rank)` terms — exact ties are more likely here than with raw cosine/BM25
  scores (e.g. two blocks each appearing in exactly one list, both at rank 1, with
  identical `k`, produce identical fused scores). The `address` tie-break handles this
  deterministically; called out because Day 4/5 both needed the same fix once
  reviewed, and it's better to have it right from the first draft this time.
- **`PgVectorStore.query`'s own missing `k <= 0` guard is worked around, not fixed at
  its source.** `pipeline.py` never lets a non-positive `vector_k` reach it, so the
  underlying gap in Day 3's code is currently harmless — but any *other* future
  caller of `PgVectorStore.query` directly (bypassing `pipeline.py`) would still hit
  the same negative-`LIMIT` PostgreSQL error this cycle works around. Worth fixing at
  the source eventually; not done here to keep this cycle's file list unchanged from
  the original Day 6 scope.
