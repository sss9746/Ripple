# Implementation Plan — Day 12: Cross-Encoder Reranking

## 0. Process note for this cycle

**`SPEC.md` is read-only.** Nothing below proposes editing it; any tension between
SPEC's text and the current code is resolved in this plan and flagged, never patched
into `SPEC.md`.

**Only this file is modified in this planning cycle.** No application code, tests, or
other files change until you and/or Codex implement a step from section 6 below.

This is a **revision** of the prior Day 12 plan, correcting 13 findings from a second
review (Codex) that caught real defects in that draft: a false claim that existing
pipeline tests were already rerank-safe (they are not — `use_rerank` defaults to
`True`), a defaulted `embed_text` field that could silently mask a forgotten
production value, an unconditional `reranker=` keyword that would break three
existing monkeypatched test doubles, a report schema with no way to identify which
model produced row four, a smoke-test ordering that spent money before confirming,
overstated Day 11 factual claims, a self-contradiction about whether pytest can
trigger a model download, a `warm_up()` that didn't actually warm anything up, a
weak empty-input test, overstated claims about what the smoke test proves, and an
unverified model-size claim. Every section below was re-derived from a fresh reading
of the actual current code and tests (not from the prior plan's own claims about
them) specifically to catch exactly this class of error.

This plan replaces the Days 8–11 implementation plan, which is done (section 1 is a
short completed-baseline summary, not a plan to re-execute). It covers **Day 12
only** — cross-encoder reranking — matching SPEC section 11's own day boundary
("Day 12 — Reranking... Done when: row four exists"). Day 13 (graph expansion) and
everything after it stays out of scope.

**Collaboration routine, unchanged from every prior cycle:**
1. Explain each step in plain language before it happens.
2. You decide whether you implement it or Codex does.
3. Run the focused tests for that step before moving on.
4. Review the diff.
5. Run the complete suite.
6. Run the paid/local-compute work (smoke test, then full evaluation) only after
   your explicit confirmation — **two separate confirmations, not one** (section 9).
7. Commit the accepted Day 12 code and the accepted Day 12 report separately.

**Python interpreter portability, unchanged**: every command below uses
`.venv/bin/python`, never bare `python`/`python3`.

## 1. Completed baseline — Days 8–11 (for context, not re-execution)

- `data/benchmark.json`: 40 questions, categories exactly 15 lookup / 10 relational /
  8 blast_radius / 7 attribute.
- `ripple/evaluation/{dataset,metrics,runner}.py`, `scripts/run_eval.py`: implemented,
  tested, committed.
- Three real, accepted ablation rows exist in
  `data/eval_results/2026-08-29T17-09-51-035170Z.json`, with
  `data/eval_results/DAY_11_ANALYSIS.md` explaining the RRF-vs-concat anomaly. Both
  files are accepted and **must not be modified or replaced** by this cycle (section
  10) — Day 12 adds a new, separate timestamped report, never edits an existing one.
- Measured Day 11 numbers this plan's row four is compared against:

  | Configuration | Recall@5 | Recall@10 | MRR | Latency (ms) |
  |---|---:|---:|---:|---:|
  | Vector only | 0.746 | 0.821 | 0.696 | 2341.32 |
  | Vector + BM25 | 0.804 | 0.835 | 0.696 | 4831.00 |
  | Vector + BM25 + RRF | 0.702 | 0.821 | 0.658 | 4093.96 |

- **Corrected factual account of `DAY_11_ANALYSIS.md`'s named regressions** (the
  prior Day 12 plan overstated this as "all BM25 rank-1/2 hits pushed out of the
  final top 10" — that is not what the analysis actually measured for every
  question; here are the specific, measured facts, kept specific rather than
  generalized):
  - **`q020`** — `module.vpc_endpoints` was BM25 rank 1 and fell **outside** RRF's
    final top 10.
  - **`q038`** — `module.vpc_endpoints` was BM25 rank 2; RRF reduced this question's
    Recall@5 from `1.0` to `0.5` (a partial regression, not a total loss).
  - **`q037`** — `output.vpc_cidr_block` was BM25 rank 1 and
    `aws_security_group.rds` was BM25 rank 4; RRF produced only **partial** expected
    recall for this question, not a complete miss.
  - **`q039`** — `module.vpc` was **not** a BM25 top-10 result at all; the non-RRF
    merge (concat/dedup) placed it in the top 5, while RRF moved it below rank 5 —
    but it **remained within the top 10** (this is a Recall@5 regression, not a
    Recall@10 failure).
  - **`q014`/`q016`** — partial `blast_radius` demotions (not total losses).
  - **Not yet verified by this plan**: whether each of these addresses actually
    falls inside RRF's *fused* top-50 pool (`rerank_top_n`'s input) is not something
    `DAY_11_ANALYSIS.md` measured directly, and this plan does not assume it. Section
    9's smoke test explicitly checks this for `q020` before drawing any conclusion
    about whether reranking recovers it — candidate-pool inclusion is something to
    *verify*, not something already known.
- Full suite: **194 tests passing**, verified fresh this cycle (see section 8 for the
  exact command). This is Day 12's baseline; section 11 requires it to stay green
  plus every new Day 12 test.
- `relational` remains weak in all three rows (0.30–0.43 Recall@5) — expected and
  **out of scope for Day 12**; SPEC frames graph expansion (Day 13) as what that
  category needs, and this plan does not touch graph expansion.

## 2. Objective

Implement cross-encoder reranking (SPEC 9.7), wire it into the pipeline behind
`RetrievalConfig.use_rerank`, add the fourth ablation row (`"+ Cross-encoder
rerank"`, SPEC 10.3), and produce one real, accepted, investigated evaluation report
for that row — without touching graph expansion, query rewriting, Pinecone, RRF
tuning, or BM25 caching, and **without changing the behavior of a single existing
test** except where this plan explicitly says a test's expectations must change.

## 3. Relevant SPEC.md requirements, quoted

- **Section 6 (Architecture)**: reranking sits after RRF fusion and before graph
  expansion in the pipeline order — confirmed unaffected by this plan, since graph
  expansion isn't wired yet either way.
- **Section 9.6 (RRF)**: `"Sort descending, take the top 50 into reranking."` — this
  is where `rerank_top_n`'s default of 50 comes from, and it means the *fused*
  candidate list feeds reranking, not the post-`final_k` list.
- **Section 9.7 (Cross-encoder reranking)**, quoted in full:
  ```python
  from sentence_transformers import CrossEncoder
  model = CrossEncoder("BAAI/bge-reranker-base", max_length=512)
  scores = model.predict([(question, r.embed_text) for r in candidates])
  ```
  > Batch the predictions — one call with 50 pairs, not 50 calls. Use the original
  > question, not the rewritten queries. Take the top 8. Store every score in
  > `stages_json`.
  >
  > The reranker sees question and candidate together, so it can judge relevance
  > that a precomputed embedding cannot... First stage retrieval optimizes recall;
  > reranking optimizes precision within that candidate set.
  - **"Take the top 8" is section 9.7's own words, and it is in tension with SPEC
    10.3's `Recall@10` column** — the identical tension Days 8–11 already resolved
    for the first three rows. Section 5.5 applies that exact precedent to row four;
    `SPEC.md` and `ripple/config.py`'s default are not touched.
  - **`candidate.embed_text`, not `candidate.body`**, is what SPEC says the reranker
    receives. Section 4 confirms the current code does not carry `embed_text` this
    far, and section 5.2 fixes that with a **required** field, not a defaulted one.
- **Section 9.11 (RetrievalConfig)**: `use_rerank: bool = True`,
  `rerank_top_n: int = 50`, `final_k: int = 8` — already exactly present in
  `ripple/config.py` (section 4 confirms). **`use_rerank` defaulting to `True` is
  exactly what makes section 4/5.4's test-safety analysis necessary** — every
  existing test that constructs a `RetrievalConfig` without explicitly setting
  `use_rerank=False` would, once reranking is wired in, silently start exercising
  the rerank path.
- **Section 10.2 (Metrics/latency)**: `rerank` is one of the named per-stage latency
  fields (`rewrite, vector_query, hydrate, bm25, fusion, rerank, graph, total`) —
  `rerank_ms` joins the existing pattern. No changes needed to
  `ripple/evaluation/metrics.py`.
- **Section 10.3 (Ablation table)**: row four's exact label is `"+ Cross-encoder
  rerank"`. This plan adds exactly that row, in that position.
- **Section 11, Day 12**, quoted: `"rerank.py per section 9.7; batch predictions.
  Wire into the pipeline behind use_rerank. Done when: row four exists. Note the
  latency cost; it will be the slowest stage."`
- **Section 12 (Risk register)**: `"Reranker is slow | Batch predictions. If still
  slow, reduce rerank_top_n to 30 and note it."` This plan keeps the SPEC default of
  50 unless the real run in section 9 shows it's needed — and if it is needed, the
  fallback is `rerank_top_n=30`, recorded as a deviation, **never** a different
  model (section 5.1/9 correct the prior draft's unverified size claims and make
  this explicit).
- **Section 4 (Stack)**: `sentence-transformers` is already a listed dependency —
  section 4 confirms it is already installed, not merely listed.

## 4. Current-state audit — what the real code does today, read fresh this cycle

Every claim below was verified by reading the file this cycle, not carried over from
the prior plan's own description of it.

**Dependency**: `requirements.txt` already lists `sentence-transformers`, and it is
already installed in `.venv` (`sentence-transformers==6.0.0`, `torch==2.13.0`,
confirmed by import). **No `requirements.txt` change and no new `pip install` step
are needed.** This machine has never downloaded the actual `BAAI/bge-reranker-base`
model weights (`~/.cache/huggingface/hub` does not exist yet).

**`ripple/retrieval/vector_store.py`** — `RetrievedBlock` today has exactly `id`,
`address`, `file_path`, `start_line`, `end_line`, `body`, `score`. **No
`embed_text` field.** None of its fields have defaults.

**`ripple/retrieval/pgvector_store.py`** — `PgVectorStore.query()`'s `SELECT` list
does not select `embed_text`, even though `resources.embed_text` has existed since
Day 1.

**`ripple/retrieval/bm25.py`** — `db.fetch_bm25_documents(repo_id)` already returns
`embed_text` as its 7th column and `build_index` already uses it correctly to build
the BM25 corpus (`tokenize(row[6])`) — but discards it immediately after tokenizing.
`BM25Document` has no `embed_text` field.

**`ripple/retrieval/fusion.py`** — needs no changes. `fuse()` builds its result via
`dataclasses.replace(blocks_by_id[document_id], score=...)`, which copies every
field it doesn't explicitly override; `concat_dedup()` returns original block
objects unchanged. A new `RetrievedBlock` field survives both automatically.

**`ripple/retrieval/pipeline.py`** — no rerank stage exists yet;
`use_rerank`/`rerank_top_n` are never read. `_build_config_json`'s `"executed"` dict
hardcodes `"rerank": False`. `run_pipeline`'s signature is `(repo_id, question,
config, embedder=None)` — no reranker injection point exists yet.

**`ripple/config.py`** — `RetrievalConfig` already matches SPEC 9.11 exactly,
**including `use_rerank: bool = True`** — no change needed here, and this default
is preserved (section 5.4/12 make this explicit: the *production* default stays
`True`; only *specific existing tests* are updated to opt out of it).

**`ripple/evaluation/runner.py`** — `ABLATION_CONFIGS` has exactly the three Day
8–11 rows. `run_benchmark` calls `pipeline.run_pipeline(repo_id, entry.question,
config)` — **exactly three positional arguments, no fourth argument of any kind.**
`ConfigResult` has no `reranker_json` field. `build_report` has no concept of
per-row reranker metadata.

**`scripts/run_eval.py`** — confirmed fully generic over `ABLATION_CONFIGS`'
length; no changes needed for a fourth row to be selectable and reportable.

### 4.1 Every `RetrievalConfig(...)` construction in `tests/test_pipeline.py` — audited line by line

There are 16 construction sites in this file (some inside `@pytest.mark.parametrize`,
covering more than one test run each). **Every existing test in this file relies on
exact assertions about `result.stages_json`/`result.latency_json`/`result.blocks`
that a live rerank stage would change**, or, in several cases, would additionally
attempt to **construct a real `CrossEncoderReranker` and call `_get_model()` against
non-empty candidates** — a real network download during `pytest`. This is the
concrete substance of finding 1: the prior draft's claim that these tests were
"already unaffected" was false. Below, "non-empty pool" means the rerank stage
would receive at least one candidate and therefore reach the real model construction
path if `use_rerank` isn't explicitly disabled; "empty pool" means the rerank stage
would receive zero candidates and short-circuit before touching the model (section
5.3) — but even those tests still need `use_rerank=False` explicitly, because
several of them assert an *exact* `set(result.stages_json)`/`set(result.latency_json)`
that a `"rerank"`/`"rerank_ms"` key would break regardless of whether the model was
ever touched.

| Test | Line | Candidates reaching rerank | Verdict |
|---|---|---|---|
| `test_run_pipeline_vector_only` | 112 | non-empty | **must add `use_rerank=False`** |
| `test_run_pipeline_bm25_only_without_openai_key` | 138 | non-empty | **must add `use_rerank=False`** |
| `test_run_pipeline_fuses_vector_and_bm25_results` | 165 | non-empty | **must add `use_rerank=False`** |
| `test_run_pipeline_concatenates_when_rrf_is_disabled` | 204 | non-empty | **must add `use_rerank=False`** |
| `test_run_pipeline_with_both_retrievers_disabled` | 223 | empty | **must add `use_rerank=False`** (exact-key-set assertions) |
| `test_config_json_separates_requested_and_executed_stages` | 235 | empty (already sets `use_rerank=True`) | **keep `use_rerank=True`, but inject a fake `Reranker` explicitly** (section 5.4/8) rather than relying on the empty-pool short-circuit as an implicit safety net |
| `test_config_json_records_executed_fusion_method` (×2 params) | 271 | empty | **must add `use_rerank=False`** |
| `test_unsupported_vector_backend_fails_before_external_calls` | 289 | never reached (raises earlier) | **must add `use_rerank=False`** for audit consistency, even though the `ValueError` fires before the rerank stage |
| `test_unused_unsupported_vector_backend_does_not_raise` | 306 | empty | **must add `use_rerank=False`** |
| `test_nonpositive_final_k_returns_no_final_blocks` (×2 params) | 330 | **non-empty** (bm25 returns 2 blocks; rerank runs *before* `final_k` truncation) | **must add `use_rerank=False`** |
| `test_nonpositive_vector_k_skips_embedding_and_query` (×2 params) | 352 | empty | **must add `use_rerank=False`** |
| `test_nonpositive_bm25_k_returns_no_bm25_results` (×2 params) | 375 | empty | **must add `use_rerank=False`** |
| `test_negative_rrf_k_raises_when_rrf_runs` | 395 | never reached (raises earlier) | **must add `use_rerank=False`** for audit consistency |
| `test_negative_rrf_k_is_ignored_when_rrf_is_disabled` | 409 | **non-empty** | **must add `use_rerank=False`** |
| `test_negative_rrf_k_is_ignored_with_only_one_retriever` (×2 params) | 431 | **non-empty** | **must add `use_rerank=False`** |
| `test_final_stage_matches_truncated_pipeline_blocks` | 459 | **non-empty** | **must add `use_rerank=False`** |

**Net result: 15 of the 16 sites get `use_rerank=False` added; the 1 remaining site
(`test_config_json_separates_requested_and_executed_stages`) keeps `use_rerank=True`
and gains an explicitly-injected fake reranker.** No site is left relying on
"empty candidates happen to make this safe" as an unstated assumption — every site's
`use_rerank` value is explicit after this change.

### 4.2 Every `RetrievedBlock(...)` construction — audited across the whole repo

```
ripple/retrieval/bm25.py:85            (production — must populate embed_text for real)
ripple/retrieval/pgvector_store.py:44  (production — must populate embed_text for real)
tests/test_ask.py:15                   (fixed-value helper, no params)
tests/test_fusion.py:12                (_block(block_id, address, score) helper)
tests/test_generate.py:39              (inline construction, one call site)
tests/test_runner.py:21                (retrieved_block(block_id, address) helper)
tests/test_prompts.py:14               (_block(*, id, address, file_path, start_line, end_line, body) helper)
tests/test_pipeline.py:17              (_block(block_id, address, score) helper)
```

All eight are keyword-argument constructions. **None currently pass `embed_text`,
because the field doesn't exist yet.** Since section 5.2 makes `embed_text` a
**required** field (no default), every one of these eight call sites needs an
explicit value once the field exists — section 5.2 specifies exactly what value each
gets and why. (`tests/test_rerank.py`, new in this cycle, is not in this list — it's
written from scratch already knowing `embed_text` is required.)

### 4.3 Existing tests whose *expectations*, not just their constructions, must change

- `tests/test_pipeline.py::test_config_json_separates_requested_and_executed_stages`
  currently asserts `result.config_json["executed"]["rerank"] is False` for a config
  that sets `use_rerank=True` — checking today's hardcoded stub. **Must change** to
  expect `True` (section 5.4).
- `tests/test_runner.py::test_ablation_configs_are_explicit_and_support_recall_at_10`
  hardcodes a 3-name list and asserts `use_rerank is False` for every row. **Must
  change** to 4 names and per-row expected `use_rerank` (section 5.6).
- `tests/test_runner.py::test_run_benchmark_scores_ranked_results_and_preserves_latency`,
  `test_run_benchmark_preserves_all_ten_results`,
  `test_run_benchmark_never_generates_an_answer` — each monkeypatches
  `runner.pipeline.run_pipeline` with a callable accepting **exactly** `(repo_id,
  question, config)`. All three use `runner.ABLATION_CONFIGS[0][1]`
  (`"Vector only"`, `use_rerank=False`). **These must keep working unmodified** —
  the corrected `run_benchmark` design (section 5.4) preserves the exact 3-argument
  call shape whenever `config.use_rerank` is `False`, specifically so these three
  fakes never receive an unexpected `reranker=` keyword. This is finding 3's core
  requirement, verified against the literal fakes in the file, not assumed.
- `tests/test_run_eval.py::test_main_runs_all_three_configs_when_config_is_omitted`
  asserts against `run_eval.ABLATION_CONFIGS` dynamically, so it passes unchanged
  with four rows — a purely cosmetic rename is proposed in section 6, not a
  functional requirement.

## 5. Design decisions

### 5.1 Dependency and model

Nothing to add to `requirements.txt` (section 4). The real first-run cost is the
model weights:

- **First run**: `CrossEncoder("BAAI/bge-reranker-base", max_length=512)` downloads
  the model from Hugging Face Hub the first time it's constructed on this machine,
  cached under `~/.cache/huggingface/hub` (the library's own default, **not** inside
  this repository — nothing to `.gitignore`, nothing to accidentally commit; don't
  set `HF_HOME`/`SENTENCE_TRANSFORMERS_HOME` to a repo-local path).
- **Size and runtime — stated honestly, not asserted as a known fact**: the model
  download is **expected to be substantial** (on the order of hundreds of MB, based
  on `bge-reranker-base`'s published architecture), and a CPU forward pass over up
  to 50 short (`max_length=512`) pairs per question is expected to take on the order
  of hundreds of milliseconds to a few seconds — but this plan does **not** assert a
  precise cache size or peak RSS figure, because neither has actually been measured
  on this machine yet. The **confirmed `prepare()` step in section 9** is where the
  real, observed download size, load time, and preparation time get recorded — that
  measurement, not a number written into this plan in advance, is what the accepted
  report's `reranker_json` (section 5.6) reflects.
- **CPU / Apple Silicon**: this machine is `arm64` macOS; `sentence-transformers`
  runs on CPU here (no CUDA, no MPS enabled by this plan).
- **If memory or runtime turns out to be unacceptable**: fall back to SPEC's own
  documented mitigation, `rerank_top_n=30` (section 3), and record that deviation
  explicitly in `DAY_12_ANALYSIS.md` (section 9). **The model itself is never
  silently swapped** for a smaller or different one — that would no longer be
  "BAAI/bge-reranker-base per SPEC 9.7."
- **Offline / CI / unit-test behavior — corrected, no contradiction (finding 7)**:
  **No `pytest` test path ever imports `sentence_transformers`, constructs a real
  `CrossEncoder`, or reaches the network.** This holds for the *entire* suite, not
  "everything except one path" — every `test_rerank.py` test injects a fake model
  object (section 5.3/8); every pipeline test that touches `use_rerank=True`
  (exactly one, after section 4.1's audit) injects a fake `Reranker` double, never a
  real `CrossEncoderReranker`. The only two places a real download/construction can
  ever happen are the manually-run, explicitly-confirmed smoke test and full
  evaluation in section 9 — **never** `pytest`. Section 8 restates this as the exact
  full-suite command's expected behavior, with no caveat about a "first real
  download during the suite" — there is no such path.
- **No external paid reranking API.** `sentence-transformers`'s local
  `BAAI/bge-reranker-base` is the only reranker this plan builds.

### 5.2 Candidate text — `RetrievedBlock.embed_text` is required, not defaulted

**Corrected from the prior draft.** `RetrievedBlock` becomes:

```python
@dataclass
class RetrievedBlock:
    id: int
    address: str
    file_path: str
    start_line: int
    end_line: int
    body: str
    embed_text: str    # required -- inserted here, before score
    score: float
```

All three of `body`, `embed_text`, `score` are non-defaulted, so this ordering is
valid (dataclass field-ordering only requires that defaulted fields come after
non-defaulted ones — there are no defaulted fields on this class at all, so any
order among the non-defaulted fields is legal). **A defaulted `embed_text: str =
""` was rejected**: it would let a production constructor forget to populate it and
silently rerank against empty text, discovered only by a reranker producing
meaningless scores against real data — exactly the class of quiet failure this
project's "never let a bug hide behind a plausible-looking default" posture (Day 6's
negative-slice fix, Day 8–11's edge-case tables) exists to prevent.

**Because the field is required, every one of section 4.2's eight construction
sites needs an explicit value.** None of this is optional or deferred:

- **`ripple/retrieval/pgvector_store.py`** — add `embed_text` to the `SELECT` list
  and the constructor call (section 7). Real value, from the real column.
- **`ripple/retrieval/bm25.py`** — add `embed_text: str` (also required, no
  default — unchanged decision from the prior draft, still correct) to
  `BM25Document`; populate it in `build_index` from the already-fetched 7th column;
  pass it into the `RetrievedBlock(...)` construction in `query()` (section 7).
- **`tests/test_fusion.py`** — `_block(block_id, address, score)` helper gains a
  fourth parameter `embed_text: str = "embed text"` (a fixed placeholder, distinct
  from the helper's existing fixed `body="resource body"`) — this file tests fusion
  ordering/scoring, never text content, so a stable, valid, non-empty, non-`body`
  placeholder is appropriate and requires no changes to any existing call site.
- **`tests/test_pipeline.py`** — same treatment: `_block(block_id, address, score,
  embed_text: str = "embed text")`. **Exception**: the new rerank-specific tests
  added to this file (section 8) call `_block(..., embed_text=...)` with an
  explicit value that **deliberately differs from `body`** on the specific
  candidates whose pairing needs to be verified (finding 2's explicit requirement),
  overriding the default for exactly those cases.
- **`tests/test_runner.py`** — `retrieved_block(block_id, address)` helper gains
  `embed_text: str | None = None`, defaulting inside the function body to
  `f"embed text for {address}"` (distinct from the existing `body=f"body for
  {address}"`) when not given — no existing call site needs to change.
- **`tests/test_prompts.py`** — `_block(*, id, address, file_path, start_line,
  end_line, body)` helper gains `embed_text: str | None = None`, defaulting inside
  the function body to `address` when not given (this file tests citation
  formatting, not text content — `address` is a valid, non-empty, deterministic
  placeholder). No existing call site needs to change.
- **`tests/test_generate.py`** — the one inline `RetrievedBlock(...)` construction
  gets an explicit `embed_text="aws_vpc.main"` (or any valid non-empty string —
  this file tests prompt/generation plumbing, not reranking).
- **`tests/test_ask.py`** — the one fixed-value `_block()` helper gets an explicit
  `embed_text="aws_vpc.main"` added to its single literal construction.
- **`tests/test_rerank.py`** (new) — every candidate constructed here sets `body`
  and `embed_text` to **deliberately different** strings, since this file's whole
  purpose is proving the reranker pairs `(question, embed_text)`, not
  `(question, body)` (section 8).

**Everything downstream of the two production constructors still needs no
changes**: `fusion.py` (section 4), and `pipeline.py`'s existing vector/bm25/fusion/
`final_k` logic, which only ever reads `.id`/`.address`/`.score` and never
constructs a `RetrievedBlock` from scratch.

### 5.3 Reranker design — `ripple/retrieval/rerank.py` (new file)

A narrow Protocol, matching the existing `VectorStore`/`EmbeddingProvider` style:

```python
class Reranker(Protocol):
    def rerank(
        self,
        question: str,
        candidates: list[RetrievedBlock],
    ) -> list[RetrievedBlock]:
        ...
```

No `top_n` parameter on the interface (unchanged reasoning from the prior draft,
section 3/9.6) — the pipeline decides pool size (section 5.4).

```python
class CrossEncoderReranker:
    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-base",
        max_length: int = 512,
        model: object | None = None,   # injection point for tests, section 8
    ) -> None:
        self._model_name = model_name
        self._max_length = max_length
        self._model = model
        self.prepare_ms: float | None = None   # set only by prepare(), section 5.6

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder   # imported here, not
                                                               # at module level
            self._model = CrossEncoder(
                self._model_name,
                max_length=self._max_length,
            )
        return self._model

    def rerank(
        self,
        question: str,
        candidates: list[RetrievedBlock],
    ) -> list[RetrievedBlock]:
        if not candidates:
            return []                  # _get_model() is never called on this path

        model = self._get_model()
        pairs = [(question, candidate.embed_text) for candidate in candidates]
        raw_scores = model.predict(pairs)   # one call; sentence-transformers
                                             # batches internally
        scores = [float(score) for score in raw_scores]  # normalizes numpy/tuple/
                                                           # list AND makes every
                                                           # score JSON-serializable

        if len(scores) != len(candidates):
            raise ValueError(
                f"reranker model returned {len(scores)} scores "
                f"for {len(candidates)} candidates"
            )

        reranked = [
            dataclasses.replace(candidate, score=score)
            for candidate, score in zip(candidates, scores)
        ]
        return sorted(
            reranked,
            key=lambda block: (-block.score, block.address),
        )
```

`prepare()` and `describe()` (provenance) are specified in section 5.6, since they
exist specifically to serve the report schema — keeping them together there avoids
splitting one cohesive design decision across two sections.

Resolved behaviors:
- **Empty candidate list** → `[]` immediately; `_get_model()` is never called.
  Section 9's corrected test proves this by making the *would-be* real-model path
  raise if reached at all (finding 9), not merely by observing an unused fake.
- **`rerank_top_n <= 0`** → resolved in `pipeline.py` (section 5.4): the pipeline
  hands `rerank()` an empty list, which is the case above.
- **Model returns the wrong number of scores** → `ValueError` naming both counts.
- **Non-list/numpy score outputs** → `float(score) for score in raw_scores`
  normalizes any iterable of numeric values and fixes a real JSON-serialization
  hazard (a bare `numpy.float64` is not JSON-serializable by the standard library).
- **Duplicate candidates** → not deduplicated here; trusts `fusion.py`'s upstream
  dedup-by-id guarantee. If given duplicates anyway, each is scored independently.
- **Deterministic ties** → `(-score, address)`, matching `fusion.py`'s convention.
- **Original question, not rewritten queries** — automatically true today (query
  rewriting isn't implemented); flagged for Day 15 to preserve deliberately.

### 5.4 Pipeline integration

`run_pipeline` gains one new optional parameter, appended last
(`repo_id, question, config, embedder=None, reranker: Reranker | None = None`) —
purely additive.

```python
if config.use_rerank:
    rerank_start = time.perf_counter()

    if config.rerank_top_n > 0:
        pool = candidates[: config.rerank_top_n]
    else:
        pool = []                      # same negative-slice guard as final_k

    reranker = reranker or CrossEncoderReranker()
    candidates = reranker.rerank(question, pool)

    latency_json["rerank_ms"] = (time.perf_counter() - rerank_start) * 1000
    stages_json["rerank"] = _serialize(candidates)

if config.final_k > 0:
    blocks = candidates[: config.final_k]
else:
    blocks = []
```

Unchanged from the prior draft: this wires reranking between fusion/candidate
assembly and `final_k` truncation, so a candidate ranked below the raw fused
top-`final_k` but inside `rerank_top_n` can still reach the final result — the
direct mechanism `DAY_11_ANALYSIS.md`'s follow-up asks for, verified by a dedicated
test (section 8), not asserted in prose.

`_build_config_json`'s `"executed"` dict: `"rerank": False` becomes
`"rerank": config.use_rerank`, matching the existing config-driven convention for
`"vector"`/`"bm25"`/`"fusion"`. An enabled reranker with a genuinely empty fused
pool still shows `executed.rerank: true` and `stages_json["rerank"]: []` —
consistent with how `use_vector=True` with `vector_k=0` already behaves today.

**When `use_rerank=False`**: the new block is skipped entirely — no construction, no
import of `sentence_transformers`, no new keys. Every pre-existing
`test_pipeline.py` assertion of an exact `set(...)` already acts as a regression
guard for this.

**`run_benchmark`'s call shape — corrected (finding 3)**: the prior draft's
pseudocode called `pipeline.run_pipeline(..., reranker=reranker)`
**unconditionally**, including when `reranker` is `None`. Section 4.3 shows this
would pass an unexpected `reranker=` keyword to three existing monkeypatched fakes
that accept only `(repo_id, question, config)`, breaking `TypeError` on every one of
them. **Corrected design**:

```python
def run_benchmark(repo_id, entries, config, config_name) -> ConfigResult:
    reranker: CrossEncoderReranker | None = None
    reranker_json: dict | None = None

    if config.use_rerank:
        reranker = CrossEncoderReranker()
        reranker.prepare()                     # section 5.6 -- real dummy predict
        reranker_json = reranker.describe()
        print(
            f"[{config_name}] reranker prepared in "
            f"{reranker.prepare_ms:.0f}ms (one-time; excluded from every "
            "question's rerank_ms)"
        )

    per_question = []
    for entry in entries:
        if config.use_rerank:
            pipeline_result = pipeline.run_pipeline(
                repo_id, entry.question, config, reranker=reranker,
            )
        else:
            pipeline_result = pipeline.run_pipeline(
                repo_id, entry.question, config,
            )
        ...  # unchanged from here down

    return ConfigResult(
        config_name=config_name,
        config=config,
        per_question=per_question,
        aggregate=aggregate(per_question),
        by_category=aggregate_by_category(per_question),
        reranker_json=reranker_json,           # section 5.6
    )
```

This is not merely "safe" for the disabled path — it is **identical** to the call
`run_benchmark` makes today (three positional arguments, nothing else), for every
config with `use_rerank=False`. Section 8 adds a test asserting this call shape is
preserved exactly, and a second test asserting the enabled path passes the **same**
reranker object (identity, not just equality) to every question in one run,
reusing it 40 times rather than constructing 40 real models — the reasoning for
which is unchanged from the prior draft (avoiding self-inflicted repeated disk I/O
that has nothing to do with the retrieval cost `rerank_ms` measures, with no
cross-config fairness concern since only row four uses reranking).

`pipeline.py`'s own default (`reranker or CrossEncoderReranker()`) still applies
correctly for a single-question caller like `scripts/ask.py`, where there is no
repeated-question cost to amortize.

**Not wired**: graph expansion, query rewriting.

### 5.5 `final_k` and Recall@10 — same resolution as Days 8–11, applied again

Unchanged from the prior draft: `rerank.py` has no `final_k`/`top_n` concept to
hardcode `8` into (SPEC 9.7's "take the top 8" is never encoded there);
`pipeline.py`'s existing `final_k` handling is untouched; the fourth
`ABLATION_CONFIGS` entry sets `final_k=10` explicitly, an evaluation-only override,
identical in kind to the first three rows. `ripple/config.py`'s production default
of `8` and `scripts/ask.py`'s behavior are both unchanged.

### 5.6 Fourth ablation configuration and reranker provenance

```python
ABLATION_CONFIGS: list[tuple[str, RetrievalConfig]] = [
    ("Vector only", RetrievalConfig(..., final_k=10)),          # unchanged
    ("Vector + BM25", RetrievalConfig(..., final_k=10)),        # unchanged
    ("Vector + BM25 + RRF", RetrievalConfig(..., final_k=10)),  # unchanged
    ("+ Cross-encoder rerank", RetrievalConfig(
        use_vector=True, use_bm25=True, use_rrf=True,
        use_rerank=True,
        use_graph=False, use_rewrite=False,
        final_k=10,
    )),
]
```

Unchanged reasoning from the prior draft: this is "Vector + BM25 + RRF" plus
`use_rerank=True` — strict ablation, one variable added per row, per SPEC 10.3's
row-four label exactly. RRF's own underperformance (Day 11) is future-work
material (section 10), not a reason to substitute a different fourth row.

**Report provenance — new this revision (finding 4)**: the prior draft's report
schema had no way to tell *which model, at what settings, prepared how* produced
row four. `ConfigResult` (`ripple/evaluation/runner.py`) gains one new trailing,
defaulted field:

```python
@dataclass
class ConfigResult:
    config_name: str
    config: RetrievalConfig
    per_question: list[QuestionResult]
    aggregate: AggregateMetrics
    by_category: list[CategoryMetrics]
    reranker_json: dict | None = None   # None for the first three rows
```

`CrossEncoderReranker` gains the two methods that produce this metadata:

```python
def prepare(self) -> None:
    """Load the model and run one dummy prediction outside any per-question
    timing -- not counted as a benchmark question. Idempotent: a second call
    on the same instance does nothing (checked via self.prepare_ms is not None)."""
    if self.prepare_ms is not None:
        return
    start = time.perf_counter()
    model = self._get_model()
    model.predict([("prepare", "prepare")])   # one dummy pair; forces the model's
                                               # first real forward pass now, so
                                               # rerank_ms on question 1 reflects
                                               # steady-state inference, not
                                               # one-time initialization
    self.prepare_ms = (time.perf_counter() - start) * 1000

def _resolved_model_revision(self) -> str:
    """Best-effort local snapshot revision -- never a full filesystem path
    (which could otherwise leak the local username/home directory into a
    committed report). Falls back to the literal string "unavailable"."""
    try:
        ...  # inspect the loaded model's cached snapshot directory name;
             # return only the short revision identifier, nothing path-shaped
    except Exception:
        return "unavailable"

def describe(self) -> dict:
    import sentence_transformers
    return {
        "model_name": self._model_name,
        "max_length": self._max_length,
        "sentence_transformers_version": sentence_transformers.__version__,
        "model_revision": self._resolved_model_revision(),
        "prepare_ms": self.prepare_ms,
        "enabled": True,
    }
```

**Corrected terminology (finding 8)**: the prior draft's `warm_up()` only called
`_get_model()` — it loaded the model but never ran a forward pass, so the *first*
timed question could still silently absorb one-time inference-graph initialization
inside its `rerank_ms`. `prepare()` replaces it: it loads the model **and** runs one
dummy `model.predict()` call before any question is timed, so `rerank_ms` on every
real question — including the first — reflects steady-state per-question inference.
`prepare()` is never called by `pytest` with a real model (section 8); `run_benchmark`
(section 5.4) calls it exactly once per config run, and `pipeline.py`'s own
lazy-construction default for a single-question caller never calls it at all
(a single question has no "first question absorbs the cost" problem to correct for).

**Where `reranker_json` lands in the report**: `build_report` already does
`"results": [asdict(result) for result in results]` — since `reranker_json` is now
a field on `ConfigResult`, it appears automatically in each result's serialized
form, `None` for the first three rows and the full `describe()` dict for row four.
No `build_report` logic change is needed beyond what `dataclasses.asdict` already
does automatically — confirmed by reading `build_report`'s current implementation
(section 4), not assumed.

**Secrets check, extended (finding 4's explicit ask)**: `describe()` reads only
`self._model_name`, `self._max_length`, `sentence_transformers.__version__`,
`self.prepare_ms`, and a best-effort **short revision string** (never a full path,
per `_resolved_model_revision`'s own docstring above) — it never touches
`os.environ`, `.env`, or any credential. Section 8's existing secrets test
(assembled from fake data, asserting no injected fake secret value appears in the
serialized report) is extended to also build a report containing a populated
`reranker_json` and re-run the same assertion.

**`schema_version` — a concrete decision, not left open (finding 4's explicit
ask)**: the report's shape has genuinely changed (every `results[]` entry now
carries a `reranker_json` key, present — as `null` — even on rows that don't use
one). `build_report` bumps `"schema_version"` from `1` to **`2`** for every report
generated after this change, whether or not the run actually used reranking (the
*shape* changed, not just row four's content). **The two already-accepted
`schema_version: 1` files are not touched, not migrated, and remain fully valid to
read as-is** — `schema_version` is a property of when a report was generated, and
nothing in this plan writes a migration path from version 1 to 2, because none is
needed: no code reads an old report back in and re-derives a new one from it.

## 6. Exact file scope

**Create:**
- `ripple/retrieval/rerank.py` — `Reranker` protocol, `CrossEncoderReranker`
  (section 5.3/5.6).
- `tests/test_rerank.py` — unit tests for `CrossEncoderReranker` (section 8).

**Modify:**
- `ripple/retrieval/vector_store.py` — `RetrievedBlock.embed_text: str` inserted
  before `score`, **required** (section 5.2).
- `ripple/retrieval/pgvector_store.py` — `SELECT` `embed_text`; pass it into
  `RetrievedBlock(...)` (section 5.2/7).
- `ripple/retrieval/bm25.py` — add `BM25Document.embed_text: str` (required);
  populate it in `build_index`; pass it into `RetrievedBlock(...)` in
  `BM25Index.query()` (section 5.2/7).
- `ripple/retrieval/pipeline.py` — new `reranker` parameter on `run_pipeline`;
  rerank stage wired between fusion and `final_k` truncation; `executed.rerank`
  becomes `config.use_rerank` (section 5.4). Import `CrossEncoderReranker`.
- `ripple/evaluation/runner.py` — fourth `ABLATION_CONFIGS` row (section 5.6);
  `ConfigResult` gains `reranker_json: dict | None = None`; `run_benchmark`
  conditionally constructs/prepares one reranker and preserves the exact
  three-argument `run_pipeline` call when `use_rerank=False` (section 5.4); new
  `import time` (not currently imported in this file).
- `tests/test_pipeline.py` — **audit-driven changes only, nothing incidental**:
  add `use_rerank=False` to the 15 sites in section 4.1's table; inject a fake
  `Reranker` into `test_config_json_separates_requested_and_executed_stages` and
  update its expected `executed["rerank"]` to `True`; add `embed_text` parameter to
  the `_block` helper (section 5.2); add the new rerank-stage tests (section 8).
- `tests/test_runner.py` — add `embed_text` parameter to `retrieved_block` (section
  5.2); update `test_ablation_configs_are_explicit_and_support_recall_at_10` for
  four rows (section 4.3); add the reranker-reuse and disabled-call-shape tests
  (section 8); **do not** change
  `test_run_benchmark_scores_ranked_results_and_preserves_latency`,
  `test_run_benchmark_preserves_all_ten_results`, or
  `test_run_benchmark_never_generates_an_answer` (section 4.3 — they must keep
  passing exactly as written).
- `tests/test_fusion.py` — add `embed_text` parameter to `_block` (section 5.2).
- `tests/test_ask.py` — add `embed_text="aws_vpc.main"` to the one construction
  (section 5.2).
- `tests/test_prompts.py` — add `embed_text` parameter to `_block` (section 5.2).
- `tests/test_generate.py` — add `embed_text="aws_vpc.main"` to the one
  construction (section 5.2).
- `tests/test_pgvector_store.py` — add assertions that `embed_text` round-trips
  through a real query, distinct from `body` (section 8).
- `tests/test_bm25.py` — `_build_test_index` helper gains an `embed_text` value per
  entry; add assertions that `embed_text` round-trips through `build_index`/
  `query()` (section 8).
- `tests/test_run_eval.py` — rename
  `test_main_runs_all_three_configs_when_config_is_omitted` to
  `test_main_runs_all_configured_rows_when_config_is_omitted` (cosmetic only,
  section 4.3).

**Explicitly not modified, by choice**: `scripts/run_eval.py`. Finding 12 asks
whether a change is needed there "if you choose to automate [pre-confirmation cost]
disclosure rather than relying on manual commentary" — **this plan chooses manual
commentary** (section 9's smoke-test procedure is a set of commands and explicit,
conversational confirmations, not new code), so no change to this file is proposed.
If you'd rather have `scripts/run_eval.py` itself print reranker-specific cost
information before its existing `confirm_cost` prompt, that's a small, reasonable
addition — flagged here as an open option, not assumed.

**Do not modify**: `SPEC.md`, `sql/schema.sql`, `docker-compose.yml`,
`.env`/`.env.example`, `requirements.txt`, `ripple/config.py`,
`ripple/retrieval/fusion.py`, `ripple/retrieval/graph.py`, `ripple/ingest/*`,
`ripple/llm/*`, `ripple/evaluation/dataset.py`, `ripple/evaluation/metrics.py`,
`scripts/run_eval.py` (see above), `scripts/index_repo.py`, `scripts/ask.py`,
`AGENTS.md`, `CLAUDE.md`, `README.md`, `data/benchmark.json`, either existing file
under `data/eval_results/`, `data/eval_results/DAY_11_ANALYSIS.md`,
`tests/test_dataset.py`, `tests/test_metrics.py`, `tests/test_db.py`, and every
other Days 1–7 test file not named above.

## 7. Interfaces — the parts not already fully written in section 5

```python
# ripple/retrieval/pgvector_store.py -- query(), the only changed lines
"""
SELECT id, address, file_path, start_line, end_line, body, embed_text,
       1 - (embedding <=> %s) AS score
FROM resources
WHERE repo_id = %s AND embedding IS NOT NULL
ORDER BY embedding <=> %s
LIMIT %s
"""
# RetrievedBlock(..., body=row[5], embed_text=row[6], score=row[7])
```

```python
# ripple/retrieval/bm25.py -- the changed lines
@dataclass
class BM25Document:
    id: int
    address: str
    file_path: str
    start_line: int
    end_line: int
    body: str
    embed_text: str        # new, required -- internal dataclass, 2 call sites
    tokens: frozenset[str]

# build_index(): row[6] is embed_text (already fetched, previously discarded)
documents = [
    BM25Document(
        id=row[0], address=row[1], file_path=row[2],
        start_line=row[3], end_line=row[4], body=row[5],
        embed_text=row[6],
        tokens=frozenset(tokenized_corpus[index]),
    )
    for index, row in enumerate(rows)
]
# BM25Index.query()'s RetrievedBlock(...) construction adds
# embed_text=self._documents[index].embed_text
```

## 8. Tests — exact files and assertions

**`tests/test_rerank.py`** (new, entirely offline — a fake `model` object with a
`.predict(pairs) -> ...` method is injected via `CrossEncoderReranker(model=...)`;
no real `sentence_transformers` import anywhere in this file):
- One `.predict()` call handles all pairs; assert exactly one call, with
  `len(pairs) == len(candidates)`.
- Pairs are `(question, candidate.embed_text)`, **not** `(question, candidate.body)`
  — construct candidates whose `body` and `embed_text` are deliberately different
  strings (finding 2) so this assertion actually discriminates between the two.
- Scores attach correctly; result sorted descending by score.
- Deterministic ties: equal fake scores sort by `address`.
- **Empty candidate list — strengthened (finding 9)**: construct a
  `CrossEncoderReranker`, then replace its `_get_model` with a function that raises
  `AssertionError("model must not be constructed for empty input")` if called at
  all (via `monkeypatch.setattr(reranker, "_get_model", ...)` or an equivalent
  instance-level override). Assert `reranker.rerank(question, []) == []` — this
  proves the empty-input path **cannot reach** model construction, not merely that
  an already-injected fake model's `.predict` happened not to be called (the prior
  draft's version of this test).
- Wrong score count: fake model returns too few/many scores; assert `ValueError`
  naming both counts.
- Numpy-like output: fake model returns a `numpy.ndarray`; assert every resulting
  `RetrievedBlock.score` is a plain Python `float` (not `numpy.float64`), and the
  serialized result is `json.dumps`-able.
- Duplicate candidate `id`s: both survive, both scored independently.
- **`prepare()`**: fake model's `.predict` records calls; assert `prepare()` calls
  it exactly once with a single dummy pair, sets `prepare_ms` to a non-negative
  float, and a second `prepare()` call does not call `.predict` again (idempotent).
- **`describe()`**: assert the returned dict has exactly the keys
  `model_name`/`max_length`/`sentence_transformers_version`/`model_revision`/
  `prepare_ms`/`enabled`; `prepare_ms` is `None` before `prepare()` is called and a
  float after; `model_revision` falls back to `"unavailable"` for a fake model that
  doesn't expose whatever real attribute a genuine `CrossEncoder` would.
- Merely `import ripple.retrieval.rerank` does not import `sentence_transformers`.

**`tests/test_pgvector_store.py`**: assert `results[i].embed_text` equals the
existing `_row()` fixture's `embed_text` value (already distinct from `body`) — a
real, discriminating check.

**`tests/test_bm25.py`**: `_build_test_index` entries carry an `embed_text` distinct
from `body`; a new test against the real indexed reference fixture asserts
`bm25_index.query(...)[0].embed_text` matches the real database column value.

**`tests/test_pipeline.py`**:
- All 15 sites from section 4.1's table get `use_rerank=False` — **no behavioral
  change to any of these tests' assertions**, only their `RetrievalConfig(...)`
  construction.
- `test_config_json_separates_requested_and_executed_stages`: inject a fake
  `Reranker` (e.g., one whose `.rerank` returns `[]` unconditionally, matching this
  test's empty-candidates setup) via the new `reranker=` parameter; update the
  expected `executed["rerank"]` to `True`.
- New: disabled toggle never constructs or calls a reranker — inject a `Reranker`
  double whose `.rerank` raises if called at all; run with `use_rerank=False` and
  non-empty vector/BM25 results; assert no error (proves it's never invoked).
- New: enabled toggle receives only `rerank_top_n` candidates — fused results
  exceeding `rerank_top_n`; a recording fake `Reranker`; assert the pool length
  passed to `.rerank` equals `config.rerank_top_n`.
- New: reranking happens before `final_k` — construct a fused order where the
  fake reranker's top-scored candidate is outside the raw fused top-`final_k` but
  inside `rerank_top_n`; assert it appears in `result.blocks`.
- New: `stages_json["rerank"]`/`rerank_ms` correctness, present only when
  `use_rerank=True`.
- New: `executed.rerank` reflects the config in the zero-candidate-but-enabled
  case (`use_rerank=True`, empty vector/BM25 results): `executed.rerank is True`,
  `stages_json["rerank"] == []`.
- New: `use_rerank=True` with `use_bm25=False` (vector-only-plus-rerank) —
  reranking runs regardless of which retrievers produced the candidates.
- New: `rerank_top_n` in `[0, -1]`, parametrized — the fake reranker's spy records
  it was called with `[]`, no crash.

**`tests/test_runner.py`**:
- Update `test_ablation_configs_are_explicit_and_support_recall_at_10` for four
  rows, per-row `use_rerank` expectations.
- New: **disabled call-shape preservation (finding 3)** — a config with
  `use_rerank=False`, `pipeline.run_pipeline` monkeypatched to a function accepting
  **exactly** `(repo_id, question, config)` (mirroring the three existing fakes in
  this file exactly); assert `run_benchmark` succeeds and never raises `TypeError`
  about an unexpected keyword.
- New: **enabled reuse** — a config with `use_rerank=True`, `pipeline.run_pipeline`
  monkeypatched to a stub recording the `reranker` keyword argument across all N
  questions; assert every call received the **same object** (`is`-identity).
  `runner.CrossEncoderReranker` monkeypatched to a fake class with a constructor
  call counter; assert exactly one construction for N questions, zero for a
  `use_rerank=False` config.
- New: the `prepare()`-timing print happens exactly once per `run_benchmark` call
  when `use_rerank=True`, not at all when `False` (`capsys`).
- New: `ConfigResult.reranker_json` is `None` for a `use_rerank=False` run and
  equals the fake reranker's `describe()` output for a `use_rerank=True` run.
- **Confirmed unchanged**: `test_run_benchmark_scores_ranked_results_and_preserves_
  latency`, `test_run_benchmark_preserves_all_ten_results`,
  `test_run_benchmark_never_generates_an_answer` — not edited (section 4.3);
  running them is itself part of proving the disabled call shape didn't regress.

**`ripple/evaluation/runner.py`'s `build_report`/report-schema tests** (new, likely
in `tests/test_runner.py` alongside the existing `build_report` tests from the
Days 8–11 cycle):
- A report built from a `ConfigResult` with a populated `reranker_json` includes it
  verbatim under that result's entry.
- A report built from a `ConfigResult` with `reranker_json=None` serializes that
  key as `null`, not an absent key (so a reader can distinguish "this row didn't
  use reranking" from "this report predates reranker_json entirely").
- `schema_version` is `2` for any report `build_report` produces after this change.
- Extended secrets test (section 5.6): a fake secret in the environment does not
  appear anywhere in a report that includes a populated `reranker_json`.

**Focused test commands** (offline; run after each step):
```bash
.venv/bin/python -m pytest -q tests/test_rerank.py
.venv/bin/python -m pytest -q tests/test_pipeline.py
.venv/bin/python -m pytest -q tests/test_runner.py
.venv/bin/python -m pytest -q tests/test_pgvector_store.py tests/test_bm25.py  # DB-dependent, skip-if-unreachable
```

**Full suite** (corrected expectation — finding 7): this command **never**
downloads or constructs a real model, at any point, for any reason. There is no
"first real download during the suite" caveat — every path that could reach the
real model is either a fake injection (unit tests) or a config with
`use_rerank=False` (everything else):
```bash
.venv/bin/python -m pytest -q
```
Expected: **194 (Days 1–11 baseline) + every new Day 12 test above, all passing,
zero regressions**, with the full run completing in roughly the same wall-clock
time as today's suite (no multi-second model load anywhere inside it).

## 9. Real Day 12 evaluation — corrected ordering and cost accounting

**Step 0 — first explicit confirmation, before any spending of any kind**: before
running anything in Step 1, get your explicit go-ahead in conversation — this is a
manual, human-run procedure, not a scripted prompt, so the checkpoint is
conversational: *"about to run a retrieval-only smoke test that makes one real
OpenAI embedding request and downloads/prepares the real reranker model
(hundreds of MB, one time) — proceed?"* Nothing below runs before that confirmation.
This directly corrects the prior draft, which ran `scripts/ask.py` (spending money
and triggering a model load) before any confirmation existed in the plan at all.

**Step 1 — retrieval-only smoke test, not `scripts/ask.py`**: the prior draft used
`scripts/ask.py`, which calls `answer_question` (an unnecessary paid generation
request) and `db.insert_query_log` (a write this smoke test has no reason to make),
and only shows a prose answer — not the actual candidate ranks this feature needs to
demonstrate. Corrected: call `pipeline.run_pipeline` directly, print exactly what's
needed to judge reranking's effect, and make **zero** generation calls and **zero**
log writes:

```bash
.venv/bin/python -c "
from ripple.config import RetrievalConfig
from ripple.retrieval import pipeline

REPO_ID = ...  # resolved per section 3.1's convention, never hardcoded
QUESTION = 'Which blocks contain at least one private_dns_enabled = true setting?'  # q020

result = pipeline.run_pipeline(REPO_ID, QUESTION, RetrievalConfig(final_k=10))

print('--- fused candidates (pre-rerank), in fusion order ---')
for row in result.stages_json['fusion']:
    print(row['id'], row['address'], row['score'])

print('--- reranked candidates, in rerank order ---')
for row in result.stages_json['rerank']:
    print(row['id'], row['address'], row['score'])

print('--- final addresses ---')
print([b.address for b in result.blocks])

print('--- rerank_ms ---')
print(result.latency_json['rerank_ms'])
"
```
This makes **approximately one paid OpenAI embedding request** (the question),
**zero OpenAI generation requests**, no `answer_question` call, and no query-log
write — exactly the accounting finding 5 requires. Before interpreting the result:
**check the "fused candidates" list for `module.vpc_endpoints`** (q020's expected
answer) — confirm it's actually present in that printed pool before drawing any
conclusion about whether reranking recovered it (section 1 already corrected the
assumption that this was already known; this is where it actually gets checked).

**Step 2 — second explicit confirmation, before the full 40-question run**:
separate from Step 0. `scripts/run_eval.py`'s existing `confirm_cost` gate
(unchanged code) already enforces a `y`/`--yes` prompt; state plainly beforehand:
- **~40 paid OpenAI embedding requests** (one per question, uncached, unchanged
  Days 8–11 reasoning).
- **Zero OpenAI generation requests** — evaluation never calls `answer_question`.
- **Up to ~2,000 local cross-encoder pairs** (40 × up to 50; likely somewhat fewer
  wherever a question's fused pool has under 50 unique candidates) — local CPU
  compute, not a paid API cost.
- **One `prepare()` call** (section 5.6) before the timed loop; its observed
  duration is recorded in `reranker_json.prepare_ms` and printed, excluded from
  every question's `rerank_ms`.

**Step 3 — run only the fourth configuration**:
```bash
.venv/bin/python scripts/run_eval.py --repo-id <resolved-repo-id> \
  --config "+ Cross-encoder rerank"
```
Produces one new timestamped JSON report (`schema_version: 2`, section 5.6),
containing one `ConfigResult` with a populated `reranker_json`.

**Step 4 — inspect before accepting**:
- Compare aggregate Recall@5/Recall@10/MRR/`mean_latency_ms` against the Day 11
  table (section 1).
- **Specifically re-check `q020`, `q037`, `q038`, `q039`, `q014`, `q016`** against
  section 1's *corrected*, specific per-question facts (not the prior draft's
  overgeneralized "all BM25 rank-1/2, all lost" claim) — for each, compare the new
  row's `recall_at_5`/`recall_at_10` to the accepted Day 11 "Vector + BM25 + RRF"
  row's value for that same question.
- **What the smoke test and unit tests each actually prove, stated precisely
  (finding 10)**: `test_rerank.py`'s fake-model tests prove that exactly one
  `.predict()` call receives all of a given call's candidate pairs, and that the
  code correctly builds/sorts/attaches scores — a **structural** guarantee, backed
  by code review of `rerank()`'s single `model.predict(pairs)` call site (section
  5.3), that this holds for real usage too. The retrieval-only smoke test (Step 1)
  proves the **real** downloaded model loads successfully and produces usable
  scores against real data — it does not, and cannot, independently reconfirm the
  call count from outside the process. Both together are the actual evidence
  behind "one batched call per question, using the real model" — no single
  step proves the whole claim alone.
- **Investigate anything surprising before accepting** — identical standard to Day
  11. If a named regression question does *not* improve, that is a real, reportable
  finding (write it down), not a reason to adjust the benchmark or config.

**Step 5 — accept and commit, or fix and re-run**: same deliberate review-then-stage
workflow as Days 8–11 (`git add` only the one accepted file). Write
`DAY_12_ANALYSIS.md` alongside it, including the observed model download/load/
`prepare_ms` timing (section 5.1/5.6 — this is where the honest, *measured* numbers
that section 5.1 declined to guess in advance actually get recorded) and the
per-question comparisons above. **Never hand-edit any measured metric.**

## 10. Scope and process

- `SPEC.md` stays read-only.
- The two existing accepted JSON reports and `DAY_11_ANALYSIS.md` are not modified.
- **Not implemented this cycle**: graph expansion (Day 13), query rewriting (Day
  15), Pinecone, RRF weighting/tuning, BM25 index caching. RRF-tuning ideas remain
  future work only.
- No credentials are ever exposed, printed, logged, or committed.
- Model cache files are never committed (section 5.1).
- `.venv/bin/python` is used in every command; no bare `python`/`python3`.
- `repo_id` is never hardcoded in application code.
- `scripts/run_eval.py` is not modified (section 6 explains the choice and names
  the alternative as an open option, not a requirement).

## 11. Acceptance criteria

Day 12 is complete only when all of the following hold:
- Reranking works behind `use_rerank`, with the exact semantics in section 5.3/5.4.
- Disabled (`use_rerank=False`) behavior is provably unchanged: every pre-existing
  test in `test_pipeline.py` and the three named `run_benchmark` tests in
  `test_runner.py` (section 4.3) pass **unmodified**, plus the new explicit
  never-called and call-shape-preservation tests pass.
- **No `pytest` invocation, at any point, downloads, constructs, loads, or runs the
  real `BAAI/bge-reranker-base` model** — verified structurally (every reachable
  path either injects a fake or has `use_rerank=False`) and empirically (the full
  suite's wall-clock time doesn't include a multi-second model load).
- Real model inference runs in one batched `.predict()` call per question, per the
  combination of code-structure review, `test_rerank.py`'s call-count assertions,
  and the smoke test's confirmation that the real model produces usable output
  (section 9, step 4 — no single one of these alone is claimed as sufficient proof).
- `rerank_ms`, every reranked candidate's score, and `reranker_json` (model name,
  `max_length`, installed `sentence-transformers` version, best-effort model
  revision, `prepare_ms`, `enabled`) are recorded in the report for row four, and
  `reranker_json` is `null` for the first three rows.
- `.venv/bin/python -m pytest -q` is fully green: 194 baseline + every new Day 12
  test.
- The fourth real ablation row exists in one committed, `schema_version: 2` JSON
  report, with full provenance.
- The result has been inspected (section 9, step 4) using the corrected,
  question-specific comparisons in section 1 — not the prior draft's overstated
  generalization.
- The accepted report and its `DAY_12_ANALYSIS.md` are committed together, in a
  commit separate from the implementation commit(s).

## 12. Needs sign-off

**One item, newly surfaced by this revision, genuinely open**: section 6 names an
alternative to the "manual commentary" choice for pre-confirmation cost disclosure
(having `scripts/run_eval.py` itself print reranker-specific cost information
before its existing prompt). This plan does not implement that alternative, but it
is a real, undecided choice between two reasonable designs, not something SPEC.md
or the existing code settles either way — flag before implementation if you'd
rather have it automated.

Otherwise unchanged from the prior draft: every other decision in section 5 was
resolvable directly from SPEC.md's text or an existing codebase convention. Two
judgment calls remain open to your override if you disagree, though each has
explicit reasoning behind it: the `embed_text` required-vs-defaulted choice
(section 5.2 — this revision already corrected the prior draft's default per
finding 2, so this is now settled in the direction finding 2 required, not still
open), and `run_benchmark` constructing/reusing one reranker rather than relying on
`pipeline.py`'s own default (section 5.4). Restated so this section does not claim
"no ambiguity" while quietly leaving either of those two unresolved: **the
`embed_text` question is resolved by this revision, not ambiguous; the
`scripts/run_eval.py` cost-disclosure question above is the one genuinely open
item.**

## 13. Audit — corrections verified against the actual repository

- **Every `RetrievalConfig(...)` in `tests/test_pipeline.py`** — enumerated and
  classified in section 4.1 (16 sites, 15 need `use_rerank=False`, 1 keeps
  `use_rerank=True` with an injected fake).
- **Every `RetrievedBlock(...)` constructor** — enumerated in section 4.2 across
  all 8 files (2 production, 6 test), each given an explicit resolution in section
  5.2.
- **No `pytest` path reaches `CrossEncoderReranker._get_model()` without a fake** —
  confirmed by the section 4.1 audit (every non-rerank test disabled) plus the
  section 8 test list (every rerank-specific test injects a fake model/reranker).
- **Disabled runner calls retain the old three-argument `run_pipeline` shape** —
  confirmed against the three literal fakes in `tests/test_runner.py` (section
  4.3), which are left unmodified specifically because the corrected `run_benchmark`
  (section 5.4) never passes them a `reranker=` keyword.
- **Row-four JSON identifies the model and preparation time** — `reranker_json`
  (section 5.6), verified present in `build_report`'s existing `asdict()`-based
  serialization without further code changes.
- **Confirmation occurs before both smoke-test spending and full-evaluation
  spending** — two distinct steps (section 9, steps 0 and 2), not one.
- **The smoke test makes zero generation calls** — it calls `pipeline.run_pipeline`
  directly, never `scripts/ask.py`/`answer_question` (section 9, step 1).
- **Stale statements removed**: the prior draft's "all named regressions were BM25
  rank-1/2 hits fully lost from the top 10" is replaced with the specific,
  per-question facts in section 1; the prior draft's "full suite may trigger the
  first real model download" is replaced with an unconditional "never" in sections
  5.1/8/11; `warm_up()` is replaced with `prepare()` throughout; the unverified
  "~500MB on disk, comparable RSS" claim is replaced with "expected to be
  substantial, actually measured during the confirmed `prepare()` step" (section
  5.1).
- **No section claims "no ambiguity" while an alternative sits unresolved** —
  section 12 names the one genuinely open item (the `scripts/run_eval.py`
  cost-disclosure alternative) explicitly, rather than describing the plan as
  fully closed.

## 14. Summary

1. **Exact changes made to `IMPLEMENTATION_PLAN.md`**: `RetrievedBlock.embed_text`
   changed from defaulted to required (section 5.2); `run_benchmark`'s
   `run_pipeline` call made conditional on `config.use_rerank` to preserve the
   exact three-argument shape existing tests depend on (section 5.4); every
   `RetrievalConfig(...)`/`RetrievedBlock(...)` construction site in the test suite
   individually audited and resolved (sections 4.1/4.2); a `reranker_json`
   provenance field added to `ConfigResult`/the report schema with `schema_version`
   bumped to `2` (section 5.6); the smoke test replaced with a retrieval-only,
   two-confirmation procedure using `pipeline.run_pipeline` directly instead of
   `scripts/ask.py` (section 9); Day 11's factual claims corrected to the specific,
   measured per-question facts (section 1); the pytest/model-download
   contradiction removed (sections 5.1/8/11); `warm_up()` replaced with a real
   `prepare()` that performs one dummy prediction (section 5.6); the empty-input
   test strengthened to prove `_get_model()` is unreachable, not just unused
   (section 8); model-size claims softened to "expected, to be measured" (section
   5.1); file scope, test scope, and the audit section all updated to match.
2. **Corrected file scope**: section 6 — 2 new files, 15 modified files (up from 10
   in the prior draft, reflecting the newly-identified `embed_text` fixture updates
   in `test_ask.py`/`test_prompts.py`/`test_generate.py`/`test_fusion.py`), and an
   explicit decision **not** to modify `scripts/run_eval.py`, with the alternative
   named as an open option (section 12).
3. **Corrected test scope**: section 8 — a full audit table for every existing
   `RetrievalConfig`/`RetrievedBlock` construction, three `run_benchmark` tests
   explicitly preserved unmodified as a compatibility guarantee, and new tests for
   `prepare()`/`describe()`, the strengthened empty-input case, and report-schema
   provenance.
4. **Corrected smoke/evaluation cost and confirmation order**: two explicit
   confirmations (before the smoke test, and again before the full run), a
   retrieval-only smoke command with ~1 embedding request and 0 generation
   requests, printing fused ranks, reranked ranks/scores, final addresses, and
   `rerank_ms` (section 9).
5. **Report schema/provenance additions**: `ConfigResult.reranker_json: dict | None`
   (model name, `max_length`, installed `sentence-transformers` version, best-effort
   model revision or `"unavailable"`, `prepare_ms`, `enabled`), `null` for the first
   three rows; `schema_version` bumped to `2` for newly generated reports; existing
   `schema_version: 1` reports untouched (section 5.6).
6. **Remaining decision needing sign-off**: whether `scripts/run_eval.py` should
   itself print reranker-specific cost information before its existing
   confirmation prompt, versus relying on this plan's manual smoke-test commentary
   (section 12) — the only genuinely open item.
