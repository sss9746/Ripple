# Implementation Plan — Day 12: Cross-Encoder Reranking

## 0. Process note for this cycle

**`SPEC.md` is read-only.** Nothing below proposes editing it; any tension between
SPEC's text and the current code is resolved in this plan and flagged, never patched
into `SPEC.md`.

**Only this file is modified in this planning cycle.** No application code, tests, or
other files change until you and/or Codex implement a step from section 6 below.

This plan replaces the Days 8–11 implementation plan, which is done (section 1 is a
short completed-baseline summary, not a plan to re-execute). It covers **Day 12
only** — cross-encoder reranking — matching SPEC section 11's own day boundary
("Day 12 — Reranking... Done when: row four exists"). Day 13 (graph expansion) and
everything after it stays out of scope, same as it stayed out of scope for Days 8–11.

**Collaboration routine, unchanged from every prior cycle:**
1. Explain each step in plain language before it happens.
2. You decide whether you implement it or Codex does.
3. Run the focused tests for that step before moving on.
4. Review the diff.
5. Run the complete suite.
6. Run the paid/local-compute evaluation only after your explicit confirmation.
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
  9) — Day 12 adds a new, separate timestamped report, never edits an existing one.
- Measured Day 11 numbers this plan's row four is compared against:

  | Configuration | Recall@5 | Recall@10 | MRR | Latency (ms) |
  |---|---:|---:|---:|---:|
  | Vector only | 0.746 | 0.821 | 0.696 | 2341.32 |
  | Vector + BM25 | 0.804 | 0.835 | 0.696 | 4831.00 |
  | Vector + BM25 + RRF | 0.702 | 0.821 | 0.658 | 4093.96 |

- Known, investigated finding from `DAY_11_ANALYSIS.md` that this plan must respect,
  not silently fix: equal-weight RRF (`k=60`) suppressed several strong single-source
  matches — named regressions are `q020`, `q037`, `q038`, `q039` (all `attribute`,
  all BM25 rank-1/2 hits that RRF pushed out of the final top 10), plus partial
  demotions on `q014`/`q016` (`blast_radius`). The stated next step in that analysis
  is exactly Day 12's job: *"Add cross-encoder reranking over a larger candidate pool
  so exact BM25 matches are not discarded solely because vector search ranks them
  poorly."*
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
tuning, or BM25 caching.

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
    for the first three rows (`RetrievalConfig.final_k` defaults to 8;
    `ABLATION_CONFIGS` overrides it to `final_k=10` for evaluation only). Section 5.5
    below applies that exact precedent to row four; `SPEC.md` and
    `ripple/config.py`'s default are not touched.
  - **`candidate.embed_text`, not `candidate.body`**, is what SPEC says the reranker
    receives. Section 4 confirms the current code does not carry `embed_text` this
    far, and section 5.2 fixes that.
- **Section 9.11 (RetrievalConfig)**: `use_rerank: bool = True`,
  `rerank_top_n: int = 50`, `final_k: int = 8` — already exactly present in
  `ripple/config.py` (section 4 confirms). No `ripple/config.py` change is proposed;
  Day 12 only makes the pipeline actually *read* `use_rerank`/`rerank_top_n`, which
  it currently doesn't (section 4).
- **Section 10.2 (Metrics/latency)**: `rerank` is one of the named per-stage latency
  fields (`rewrite, vector_query, hydrate, bm25, fusion, rerank, graph, total`) —
  `rerank_ms` joins the existing `vector_query_ms`/`bm25_ms`/`fusion_ms` pattern
  Days 8–11 already built the latency-preservation machinery for (no changes needed
  to `ripple/evaluation/metrics.py` — section 4 confirms why).
- **Section 10.3 (Ablation table)**: row four's exact label is `"+ Cross-encoder
  rerank"`. This plan adds exactly that row, in that position, to
  `ABLATION_CONFIGS`.
- **Section 11, Day 12**, quoted: `"rerank.py per section 9.7; batch predictions.
  Wire into the pipeline behind use_rerank. Done when: row four exists. Note the
  latency cost; it will be the slowest stage."`
- **Section 12 (Risk register)**: `"Reranker is slow | Batch predictions. If still
  slow, reduce rerank_top_n to 30 and note it."` — this plan keeps the SPEC default
  of 50 and only reduces it if the real run in section 7 turns out to need it,
  exactly as the risk register anticipates; it is not pre-emptively lowered.
- **Section 4 (Stack)**: `sentence-transformers cross-encoder reranking
  (BAAI/bge-reranker-base)` is already a listed dependency — section 4 below confirms
  it is already installed, not merely listed.

## 4. Current-state audit — what the real code does today, read fresh this cycle

This section exists because guessing from SPEC pseudocode alone would produce a plan
that doesn't compile against the actual codebase. Every claim below was verified by
reading the file, not assumed.

**Dependency**: `requirements.txt` already lists `sentence-transformers`, and it is
already installed in `.venv` (`sentence-transformers==6.0.0`, `torch==2.13.0`,
confirmed by import). **No `requirements.txt` change and no new `pip install` step
are needed.** This machine has never downloaded the actual `BAAI/bge-reranker-base`
model weights (`~/.cache/huggingface/hub` does not exist yet) — the first real
`CrossEncoder(...)` construction, whenever it happens, will download them (a few
hundred MB) before any tests or the real run can use it. Section 5.1 covers this.

**`ripple/retrieval/vector_store.py`** — `RetrievedBlock` today has exactly `id`,
`address`, `file_path`, `start_line`, `end_line`, `body`, `score`. **It has no
`embed_text` field.** This is the mismatch SPEC 9.7's `r.embed_text` literally
depends on — reranking cannot be built against the current `RetrievedBlock` without
first fixing this.

**`ripple/retrieval/pgvector_store.py`** — `PgVectorStore.query()`'s `SELECT` list is
`id, address, file_path, start_line, end_line, body, 1 - (embedding <=> %s) AS
score` — **`embed_text` is not selected**, even though the `resources` table has
always had that column (schema section 7, present since Day 1). Fixing this is a
one-line `SELECT` addition plus one new constructor argument.

**`ripple/retrieval/bm25.py`** — `db.fetch_bm25_documents(repo_id)` (unchanged,
`ripple/db.py`) already returns `embed_text` as its 7th column and `build_index`
already uses it (correctly) to build the BM25 corpus (`tokenize(row[6])`) — but the
value is discarded immediately after tokenizing. `BM25Document` has no `embed_text`
field, and `BM25Index.query()`'s `RetrievedBlock` construction doesn't pass one.
BM25 candidates lose `embed_text` even though the database row already had it in
hand.

**`ripple/retrieval/fusion.py`** — **needs no changes at all.** `fuse()` builds its
result via `dataclasses.replace(blocks_by_id[document_id], score=...)`, which copies
every field it doesn't explicitly override — a new `embed_text` field on
`RetrievedBlock` survives fusion automatically. `concat_dedup()` returns original
block objects unchanged. Confirmed by reading both functions this cycle.

**`ripple/retrieval/pipeline.py`** — `run_pipeline` builds `candidates` from
vector/BM25/fusion, then does `blocks = candidates[:config.final_k]` (guarded
against `final_k <= 0`, matching Day 6's negative-slice lesson) and returns. **There
is no rerank stage at all** — `use_rerank`/`rerank_top_n` are never read.
`_build_config_json`'s `"executed"` dict **hardcodes** `"rerank": False, "graph":
False, "rewrite": False` regardless of the requested config. `run_pipeline`'s
signature is `(repo_id, question, config, embedder=None)` — no reranker injection
point exists yet.

**`ripple/config.py`** — `RetrievalConfig` already has `use_rerank: bool = True`,
`rerank_top_n: int = 50`, `final_k: int = 8`, matching SPEC 9.11 exactly. **No
change needed here.**

**`ripple/evaluation/runner.py`** — `ABLATION_CONFIGS` has exactly the three Day
8–11 rows, each with `use_rerank=False` and `final_k=10` explicit. `run_benchmark`
calls `pipeline.run_pipeline(repo_id, entry.question, config)` with no `embedder`
override and (currently) nothing else — matching the established "let the pipeline
build its own default" pattern. `build_report`/`_indexed_corpus_fingerprint`/
`_corpus_git_revision` are generic over `ConfigResult` and need no changes for a
fourth row.

**`scripts/run_eval.py`** — confirmed **fully generic** over `ABLATION_CONFIGS`'
length: `CONFIG_NAMES` is computed from it at import time (so a fourth name becomes
a valid `--config` choice automatically), `select_configs`/`render_markdown_table`/
`main` all iterate whatever `ABLATION_CONFIGS` (or the single selected entry)
contains. **No changes needed to this file for the fourth row to work.**

**Existing tests that construct `RetrievedBlock`/`BM25Document` directly** — 8 files
call `RetrievedBlock(...)`, all with keyword arguments, none passing `embed_text`
(it doesn't exist yet): `ripple/retrieval/{bm25,pgvector_store}.py` (production,
must change), `tests/{test_fusion,test_pipeline,test_runner,test_ask,test_prompts,
test_generate}.py` (fixtures, unrelated to reranking). One file constructs
`BM25Document(...)` directly: `tests/test_bm25.py`'s `_build_test_index` helper.
Section 5.2 explains exactly which of these must change and which must not.

**Existing tests that will need their *expectations* updated, not just tolerate a
new field** (found by reading, not assumed):
- `tests/test_pipeline.py::test_config_json_separates_requested_and_executed_stages`
  currently asserts `result.config_json["executed"] == {..., "rerank": False, ...}`
  for a config that sets `use_rerank=True` — this assertion is checking today's
  hardcoded stub and **will be wrong** once `executed.rerank` reflects the real
  config (section 5.4). Must be updated, not left alone.
- `tests/test_runner.py::test_ablation_configs_are_explicit_and_support_recall_at_10`
  hardcodes the three-name list and asserts `config.use_rerank is False` for *every*
  row — both assertions **must change** to account for row four
  (`use_rerank=True` only on the new row).
- `tests/test_run_eval.py::test_main_runs_all_three_configs_when_config_is_omitted`
  asserts against `run_eval.ABLATION_CONFIGS` dynamically (`config_names == [name
  for name, _config in run_eval.ABLATION_CONFIGS]`), so it **passes unchanged**
  with four rows — only its name is now slightly stale (it will exercise four
  configs, not three). A rename is proposed in section 6 as a one-line cosmetic
  fix, not a functional requirement.

## 5. Design decisions

### 5.1 Dependency and model

Nothing to add to `requirements.txt` — `sentence-transformers` is already listed and
already installed (section 4). The one real first-run cost is the model weights
themselves:

- **First run**: `CrossEncoder("BAAI/bge-reranker-base", max_length=512)` downloads
  the model from Hugging Face Hub the first time it's constructed on this machine —
  a few hundred MB, cached under `~/.cache/huggingface/hub` (the library's own
  default, **not** inside this repository — nothing to `.gitignore`, since nothing
  lands in the repo; don't set `HF_HOME`/`SENTENCE_TRANSFORMERS_HOME` to a
  repo-local path, which would change that). Subsequent constructions on the same
  machine reuse the cache and skip the download.
- **CPU / Apple Silicon**: this machine is `arm64` macOS. `sentence-transformers`
  runs `bge-reranker-base` on CPU here (no CUDA); PyTorch's MPS backend is not
  required and this plan does not enable it — a CPU forward pass over up to 50
  short (`max_length=512`) pairs per question is expected to take on the order of a
  few hundred milliseconds to a few seconds, which is exactly why SPEC's own risk
  register calls reranking "the slowest stage" and offers `rerank_top_n=30` as a
  fallback (section 3) — not invoked unless the real run in section 7 shows it's
  needed.
- **Disk/memory**: the cached model is on the order of ~500MB on disk; loaded into
  memory it adds a comparable amount of RSS for the lifetime of the process that
  loaded it. This is a one-time-per-process cost, not a per-question cost (section
  5.4 explains how `run_benchmark` avoids paying it 40 times).
- **Offline / CI / unit-test behavior**: every unit test that exercises
  `CrossEncoderReranker`'s own logic injects a fake model object (section 5.3) — it
  never constructs a real `sentence_transformers.CrossEncoder`, so it never touches
  the network and never requires the download to have already happened. Merely
  *importing* `ripple.retrieval.rerank` (or anything that imports it, including
  `pipeline.py` and therefore most of the test suite) does **not** import
  `sentence_transformers` either — that import is deferred to inside the method
  that actually needs a real model (section 5.3) — so collecting/running the ~200
  tests that never touch reranking stays exactly as fast as it is today, with zero
  risk of an accidental download during a CI run that never asked for one.
- **No external paid reranking API.** `sentence-transformers`'s local
  `BAAI/bge-reranker-base` is the only reranker this plan builds, matching SPEC 9.7
  and hard constraint 1 ("no LangChain... orchestration frameworks," but
  `sentence-transformers` itself is explicitly named as fine).

### 5.2 Candidate text — closing the `embed_text` gap

**`RetrievedBlock` gets a new trailing field, `embed_text: str = ""`, added *after*
`score`** (dataclass field-ordering requires this — `score` has no default today, so
a new defaulted field must come after it, not before). The default is deliberately
**not** SPEC's `body` — an empty string is a visibly-wrong placeholder, never
silently mistaken for a real chunk, in the rare fixture that doesn't set it, whereas
defaulting to `body` would quietly reintroduce exactly the body-vs-embed_text
substitution the request says not to make. Two real constructors change to populate
it for real; nothing else needs to:

- **`PgVectorStore.query()`** — add `embed_text` to the `SELECT` list (`SELECT id,
  address, file_path, start_line, end_line, body, embed_text, 1 - (embedding <=> %s)
  AS score ...`) and to the `RetrievedBlock(...)` construction. One query, one
  constructor call, both in the same file.
- **`BM25Index.query()`** (`ripple/retrieval/bm25.py`) — add `embed_text: str` to
  `BM25Document` (placed after `body`, no default — this dataclass is internal-only,
  constructed in exactly two places, so there is no blast-radius reason to default
  it), populate it from `db.fetch_bm25_documents`'s already-fetched 7th column in
  `build_index` (the value is already in hand, currently thrown away right after
  tokenizing), and pass `documents[index].embed_text` into the `RetrievedBlock(...)`
  construction in `query()`.

**Everything downstream of these two constructors needs no changes**: `fusion.py`
(section 4), `pipeline.py`'s existing vector/bm25/fusion/final_k logic (it only ever
reads `.id`/`.address`/`.score`, never constructs new blocks from scratch), and the
6 test files that construct `RetrievedBlock` for unrelated purposes (fusion
mechanics, pipeline mechanics unrelated to text content, prompt formatting, citation
generation, benchmark scoring) — they all use keyword arguments and never reference
`embed_text`, so the new defaulted field is invisible to them. This is deliberately
**not** "silently substituting body" — it's giving fixtures that don't test text
content a harmless placeholder, while the two paths that actually populate real
`RetrievedBlock`s from the database now carry the real, distinct `embed_text` value
end to end, which is what SPEC's `r.embed_text` requires and what section 8's new
tests directly prove (not just assume).

### 5.3 Reranker design — `ripple/retrieval/rerank.py` (new file)

A narrow Protocol, matching the existing `VectorStore`/`EmbeddingProvider` style
exactly:

```python
class Reranker(Protocol):
    def rerank(
        self,
        question: str,
        candidates: list[RetrievedBlock],
    ) -> list[RetrievedBlock]:
        ...
```

Deliberately **no `top_n` parameter on the interface** — SPEC 9.6/9.7 frame "take
the top 50 into reranking" as a *pipeline*-level candidate-pool decision, exactly
like `final_k`'s truncation already is. `rerank()` reranks and returns *every*
candidate it's given, sorted; the pipeline decides how many candidates it's given
(section 5.4) and how many of the reranked result it keeps (`final_k`, unchanged).
This mirrors `fusion.fuse()`/`concat_dedup()`, which likewise take no "how many to
keep" parameter.

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
        self._model = model            # None until first real use, or pre-supplied

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder   # imported here, not
                                                               # at module level --
                                                               # section 5.1
            self._model = CrossEncoder(
                self._model_name,
                max_length=self._max_length,
            )
        return self._model

    def warm_up(self) -> None:
        """Force model construction/download now, outside any timed region."""
        self._get_model()

    def rerank(
        self,
        question: str,
        candidates: list[RetrievedBlock],
    ) -> list[RetrievedBlock]:
        if not candidates:
            return []                  # no model construction, no predict call

        model = self._get_model()
        pairs = [(question, candidate.embed_text) for candidate in candidates]
        raw_scores = model.predict(pairs)   # one call, sentence-transformers
                                             # batches internally
        scores = [float(score) for score in raw_scores]  # normalizes numpy/tuple/
                                                           # list AND makes every
                                                           # score JSON-serializable
                                                           # (a bare numpy.float64
                                                           # would crash
                                                           # json.dump in
                                                           # write_report otherwise)

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

Resolved behaviors, each one explicit rather than left to accident:
- **Empty candidate list** → `[]`, immediately, before `_get_model()` is ever
  called — no construction, no download, no predict call. Tested directly
  (section 8).
- **`rerank_top_n <= 0`** → resolved in `pipeline.py`, not here (section 5.4):
  `rerank.py` has no `top_n` concept to misapply a negative-slice to. A
  non-positive `rerank_top_n` simply means the pipeline hands `rerank()` an empty
  list, which is the empty-list case above.
- **Model returns the wrong number of scores** → `ValueError` naming both counts,
  raised before any sorting/attachment happens on mismatched data.
- **Non-list/numpy score outputs** → `float(score) for score in raw_scores`
  iterates and coerces regardless of whether `raw_scores` is a `numpy.ndarray`,
  `list`, or `tuple` (all three are iterable and each element supports `float()`);
  this also fixes the JSON-serialization hazard above.
- **Duplicate candidates** → not deduplicated by `rerank()` itself; it trusts
  `fusion.py`'s existing dedup-by-id guarantee upstream. If a caller ever passes
  duplicate `id`s anyway, each is scored and returned independently — a documented,
  simple choice, not a silent bug, since the real pipeline never produces
  duplicates at this point.
- **Deterministic ties** → `(-score, address)` sort key, identical in shape to
  `fusion.fuse()`'s and `fusion.concat_dedup()`'s existing tie-break convention.
- **Original question, not rewritten queries** — automatically true today, since
  query rewriting (Day 15) isn't implemented; `pipeline.py` passes the same
  `question` variable it always has. Flagged here so Day 15 remembers to keep
  reranking pointed at the original question specifically, per SPEC 9.7's own
  instruction, rather than assuming "whatever `pipeline.py` currently calls
  `question`" stays correct by accident once rewriting exists.

### 5.4 Pipeline integration

`run_pipeline` gains one new optional parameter, appended after the existing ones
(`repo_id, question, config, embedder=None, reranker: Reranker | None = None`) —
purely additive, every existing call site keeps working unchanged.

Wiring, inserted between the existing candidate-assembly block and the existing
`final_k` truncation (nothing above or below this new block changes):

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

This directly satisfies the request's core correctness requirement: reranking sees
up to `rerank_top_n` (default 50) **fused** candidates, before `final_k` (10, for
evaluation) truncates — so a candidate ranked, say, 15th by RRF but scored highest
by the cross-encoder can still reach the final top 10. This is exactly what
`DAY_11_ANALYSIS.md` asks for on `q020`/`q037`/`q038`/`q039` (all currently lost
between RRF's fused order and the final top 10) and is proven by a dedicated test
(section 8), not just asserted in prose.

**Interaction with the existing 30+30 candidate limits**: `vector_k=30` and
`bm25_k=30` unchanged means the fused pool is at most 60 unique candidates (fewer
with overlap). `rerank_top_n=50` (SPEC's own default) can therefore still trim a
handful of the weakest fused candidates before reranking ever sees them — this
matches SPEC 9.6's own "top 50" language exactly and is not a bug; it only becomes
a concern for a candidate ranked below 50th, which none of the named regression
questions are.

**`_build_config_json`'s `"executed"` dict**: `"rerank": False` (hardcoded) becomes
`"rerank": config.use_rerank` — the same config-driven pattern already used for
`"vector"`/`"bm25"`/`"fusion"` (`fusion_will_run = config.use_vector and
config.use_bm25`, computed from config alone, not from whether either retriever
actually returned candidates at runtime). **Resolution of "is an enabled reranker
with zero input considered executed": yes** — consistent with the existing
convention, `executed.rerank` reflects the *config*, not the runtime candidate
count. A `use_rerank=True` config with a genuinely empty fused pool still shows
`executed.rerank: true` and `stages_json["rerank"]: []`, exactly mirroring how
`use_vector=True` with `vector_k=0` already shows `executed` (implicitly, via
`config_json["requested"]`) as vector-enabled with an empty `stages_json["vector"]`
today. `"graph"` and `"rewrite"` stay hardcoded `False` — untouched, out of scope.

**When `use_rerank=False`** (all three existing `ABLATION_CONFIGS` rows): the new
`if config.use_rerank:` block is skipped entirely — no `CrossEncoderReranker`
construction, no import of `sentence_transformers`, no `rerank_ms` key, no
`stages_json["rerank"]` key. Existing behavior is unchanged **because no existing
code path is touched**, not merely "expected to be" — and every existing
`test_pipeline.py` test that asserts an exact `set(result.latency_json)`/
`set(result.stages_json)` (there are several) already acts as a regression guard:
if this new code ever leaked a `rerank_ms`/`rerank` key when disabled, those
existing assertions would fail immediately without any new test being needed.

**Reranker reuse across a 40-question evaluation run** — a real engineering
decision, not incidental: if `run_benchmark` simply let `run_pipeline`'s default
(`reranker or CrossEncoderReranker()`) apply on every call, a **fresh
`CrossEncoderReranker` would be constructed for all 40 questions**, and each one's
first `.rerank()` call would re-trigger `_get_model()` — reloading a several-hundred-
MB model from disk 40 times. Unlike the Days 8–11 embedding-cache decision, sharing
one loaded model across all 40 questions of **one config's run** creates no
cross-config fairness problem (only row four uses `use_rerank=True` at all, so there
is no "which config ran first got the real cost" question) — it is simply avoiding
self-inflicted, meaningless disk I/O that has nothing to do with the actual
retrieval cost SPEC's `rerank_ms` is meant to measure. **Resolution**:
`run_benchmark` (section 5.6) constructs one `CrossEncoderReranker` when
`config.use_rerank` is true, calls `.warm_up()` once (timed and printed separately,
section 5.1/7), and passes that same instance into every `pipeline.run_pipeline(...,
reranker=reranker)` call for that config. `pipeline.py`'s own default (build one
lazily if not injected) stays exactly right for a single-question caller like
`scripts/ask.py`, where there is no repeated-question cost to amortize.

**Not wired**: graph expansion, query rewriting — both stay exactly as
unimplemented/unwired as they were before this cycle (section 4 confirms neither is
touched by this change).

### 5.5 `final_k` and Recall@10 — same resolution as Days 8–11, applied again

SPEC 9.7 says "take the top 8" (matching `RetrievalConfig.final_k`'s production
default); SPEC 10.3 requires a `Recall@10` column. `rerank.py` itself has no
`top_n`/`final_k` concept at all (section 5.3) — nothing to hardcode `8` into.
`pipeline.py` already respects `config.final_k` for every row (unchanged this
cycle); Day 12 does not change that. The fourth `ABLATION_CONFIGS` entry (section
5.6) sets `final_k=10` explicitly, identically to the first three rows, for the
identical reason recorded in the current plan's history: `PipelineResult.blocks` is
capped at `final_k`, so scoring `Recall@10` against a `final_k=8` result would
silently degrade to `Recall@8`. This is, again, an **evaluation-only** override —
`ripple/config.py`'s production default of `8` (SPEC 9.7's own number) is not
changed, and neither is `scripts/ask.py`'s behavior.

### 5.6 Fourth ablation configuration

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

The name is SPEC 10.3's row-four label, character for character. It is built as
**"Vector + BM25 + RRF" plus `use_rerank=True`**, not as reranking over the
non-RRF concat/dedup row — this is what "strict ablation" means: each row adds
exactly one variable on top of the previous row, so any measured change is
attributable to that one variable. `DAY_11_ANALYSIS.md` already showed RRF
underperforming plain concat/dedup on this benchmark; that finding does not change
*which* row SPEC's table asks for next — it's a reason to note as future work (a
"reranking on top of the non-RRF row" experiment, or a smaller RRF `k`, or weighted
RRF — section 9), not a reason to substitute a different fourth row. The first three
rows and their names are unchanged.

## 6. Exact file scope

**Create:**
- `ripple/retrieval/rerank.py` — `Reranker` protocol, `CrossEncoderReranker`
  (section 5.3).
- `tests/test_rerank.py` — unit tests for `CrossEncoderReranker` (section 8).

**Modify:**
- `ripple/retrieval/vector_store.py` — add `RetrievedBlock.embed_text: str = ""`
  (section 5.2).
- `ripple/retrieval/pgvector_store.py` — `SELECT` `embed_text`; pass it into
  `RetrievedBlock(...)` (section 5.2).
- `ripple/retrieval/bm25.py` — add `BM25Document.embed_text: str` (no default);
  populate it in `build_index`; pass it into `RetrievedBlock(...)` in
  `BM25Index.query()` (section 5.2).
- `ripple/retrieval/pipeline.py` — new `reranker` parameter on `run_pipeline`;
  rerank stage wired between fusion/candidate-assembly and `final_k` truncation;
  `_build_config_json`'s `executed.rerank` becomes `config.use_rerank` (section
  5.4). Import `CrossEncoderReranker` from the new `rerank` module.
- `ripple/evaluation/runner.py` — add the fourth `ABLATION_CONFIGS` row (section
  5.6); `run_benchmark` constructs and warms up one `CrossEncoderReranker` when
  `config.use_rerank` is true and passes it to every `pipeline.run_pipeline(...,
  reranker=...)` call for that config (section 5.4); new `import time` (not
  currently imported in this file).
- `tests/test_pgvector_store.py` — add assertions that `embed_text` round-trips
  through a real query, distinct from `body` (section 8); no fixture changes needed
  (`_row()` already sets a distinct `embed_text`).
- `tests/test_bm25.py` — `_build_test_index` helper gains an `embed_text` argument/
  field (its one direct `BM25Document(...)` construction, section 4); add
  assertions that `embed_text` round-trips through `build_index`/`query()`.
- `tests/test_pipeline.py` — update
  `test_config_json_separates_requested_and_executed_stages`'s expected
  `executed.rerank` value (section 4); add the new rerank-stage tests (section 8).
- `tests/test_runner.py` — update
  `test_ablation_configs_are_explicit_and_support_recall_at_10` for four rows
  (section 4); add the reranker-reuse test (section 8).
- `tests/test_run_eval.py` — rename
  `test_main_runs_all_three_configs_when_config_is_omitted` to
  `test_main_runs_all_configured_rows_when_config_is_omitted` (its assertions
  already pass unchanged against four rows, section 4 — cosmetic only, done in the
  same commit as the rest of Day 12 for consistency rather than left stale).

**Do not modify**: `SPEC.md`, `sql/schema.sql`, `docker-compose.yml`,
`.env`/`.env.example`, `requirements.txt` (already correct, section 4),
`ripple/config.py` (already correct, section 4), `ripple/retrieval/fusion.py`
(needs no change, section 4), `ripple/retrieval/graph.py`, `ripple/ingest/*`,
`ripple/llm/*`, `ripple/evaluation/dataset.py`, `ripple/evaluation/metrics.py`,
`scripts/run_eval.py` (already fully generic, section 4), `scripts/index_repo.py`,
`scripts/ask.py`, `AGENTS.md`, `CLAUDE.md`, `README.md`, `data/benchmark.json`,
either existing file under `data/eval_results/` (the two accepted Day 10/11 JSON
reports), `data/eval_results/DAY_11_ANALYSIS.md`, and every other existing test
file (`test_ask.py`, `test_prompts.py`, `test_generate.py`, `test_fusion.py`,
`test_dataset.py`, `test_metrics.py`, `test_db.py`, and the rest of the Days 1–7
test suite).

## 7. Interfaces — the parts not already fully written in section 5

```python
# ripple/retrieval/pgvector_store.py — query(), the only changed lines
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
# ripple/retrieval/bm25.py — the changed lines
@dataclass
class BM25Document:
    id: int
    address: str
    file_path: str
    start_line: int
    end_line: int
    body: str
    embed_text: str        # new, no default -- internal dataclass, 2 call sites
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

```python
# ripple/evaluation/runner.py — run_benchmark, the changed shape
def run_benchmark(repo_id, entries, config, config_name) -> ConfigResult:
    reranker: CrossEncoderReranker | None = None
    if config.use_rerank:
        reranker = CrossEncoderReranker()
        warm_up_start = time.perf_counter()
        reranker.warm_up()
        warm_up_ms = (time.perf_counter() - warm_up_start) * 1000
        print(
            f"[{config_name}] reranker model ready in {warm_up_ms:.0f}ms "
            "(one-time; excluded from every question's rerank_ms)"
        )

    per_question = []
    for entry in entries:
        pipeline_result = pipeline.run_pipeline(
            repo_id, entry.question, config, reranker=reranker,
        )
        ...  # unchanged from here down
```

## 8. Tests — exact files and assertions

**`tests/test_rerank.py`** (new, entirely offline — a fake `model` object with a
`.predict(pairs) -> ...` method is injected via `CrossEncoderReranker(model=...)`;
no real `sentence_transformers` import, no network, no download):
- One `.predict()` call handles all pairs: fake model records calls, assert exactly
  one call, with `len(pairs) == len(candidates)`.
- Pairs are `(question, candidate.embed_text)` — assert the fake model's recorded
  pairs use `embed_text`, not `body` (construct a candidate whose `body` and
  `embed_text` differ, to make this a real, discriminating assertion).
- Scores attach correctly and result is sorted descending by score.
- Deterministic ties: two candidates with equal fake scores sort by `address`.
- Empty candidate list: `rerank(question, [])  == []`; assert the fake model's
  `.predict` was **never called** (proves no construction/prediction happens).
- Wrong score count: fake model returns too few/many scores; assert `ValueError`
  naming both counts.
- Numpy-like output: fake model returns a `numpy.ndarray` (or a plain object
  wrapping floats without being a `list`); assert it's handled — every resulting
  `RetrievedBlock.score` is a plain Python `float` (`isinstance(score, float)` and
  `type(score) is float`, not `numpy.float64`), and the result is JSON-serializable
  (`json.dumps` on the serialized form succeeds).
- Duplicate candidate `id`s: both survive, both scored independently, no crash.
- `warm_up()` triggers model construction exactly once, and a second `warm_up()` (or
  a subsequent `rerank()` call) does not reconstruct it — assert via a fake
  "constructor" call counter injected through a subclass or a monkeypatched
  `sentence_transformers.CrossEncoder`-shaped factory, whichever reads more simply
  once written; either way, only `_get_model()`'s lazy path is exercised, never a
  real network call.
- Merely `import ripple.retrieval.rerank` does not import `sentence_transformers`
  (assert `"sentence_transformers" not in sys.modules` immediately after a fresh
  import in a subprocess, or rely on the module-level absence of that import being
  visible in a code read — whichever the implementer finds more robust; the point
  being tested is "importing this module has no heavy/network side effect").

**`tests/test_pgvector_store.py`** (additions to the existing DB-dependent test,
skip-if-unreachable, same convention as every prior day):
- After the existing `store.upsert([...])`/`store.query(...)` calls, assert
  `results[i].embed_text` equals the `_row()` fixture's `embed_text` value for that
  address — and that it differs from `results[i].body` (the fixture already makes
  `embed_text=address` while `body` is a full resource block string, so this is a
  real, discriminating check, not a tautology).

**`tests/test_bm25.py`** (additions):
- `_build_test_index` accepts an `embed_text` alongside each `(address, body)` entry
  (default to a value distinct from `body` in the helper's own test data, so a
  regression that swapped the two fields would be caught); `test_query_ranks_exact_
  address_first`-style existing tests keep working unchanged since `tokenize()`
  still operates on whatever text is passed for indexing.
- New test: `build_index(repo_id)` against the real, indexed reference fixture
  (reusing the existing `bm25_test_repositories` fixture) — assert
  `bm25_index.query(...)[0].embed_text` matches the real `embed_text` column value
  for that resource (fetched independently via `db.fetch_bm25_documents` in the
  test itself, not assumed).

**`tests/test_pipeline.py`** (additions and one update):
- **Update** `test_config_json_separates_requested_and_executed_stages`: change the
  expected `executed["rerank"]` from `False` to `True` for a config with
  `use_rerank=True` (and add a companion case/assert for `use_rerank=False` staying
  `False`, if not already implied by another existing test).
- Disabled toggle (`use_rerank=False`, the default across all pre-existing tests in
  this file): assert no `rerank_ms`/`"rerank"` key appears — already implicitly
  covered by every existing exact-`set(...)` assertion (section 5.4); add one
  test that explicitly injects a reranker double that raises if called at all, runs
  the pipeline with `use_rerank=False`, and asserts it's never touched (the direct,
  unambiguous version of "never constructed or called").
- Enabled toggle receives only `rerank_top_n` candidates: fake vector/BM25 results
  totaling more than `rerank_top_n`, a fake `Reranker` double that records what it
  was called with; assert `len(recorded_candidates) == config.rerank_top_n` (or the
  full fused count if that's smaller).
- Reranking happens before `final_k`: construct a fused order where the block that
  should win under the fake reranker's scoring is **outside** the raw fused top
  `final_k` but **inside** the `rerank_top_n` pool; assert it appears in
  `result.blocks` — the direct test for `DAY_11_ANALYSIS.md`'s exact concern
  (section 5.4).
- `stages_json["rerank"]` and `rerank_ms`: assert the serialized rerank stage
  matches the fake reranker's returned order/scores, and `"rerank_ms"` is present
  and non-negative only when `use_rerank=True`.
- `executed.rerank` accurately reflects the config: parametrized True/False case,
  including the zero-candidate-but-enabled case (empty vector/BM25 results,
  `use_rerank=True`) asserting `executed.rerank is True` and
  `stages_json["rerank"] == []`.
- Existing vector/BM25/RRF behavior unchanged when `use_rerank=False`: already
  guaranteed by not touching that code path (section 5.4) and by every pre-existing
  test in this file continuing to pass unmodified (full-suite run, section 8's
  command below) — no new test needed beyond the explicit "never called" test
  above.
- Other toggles remain independent: one test with `use_rerank=True` and
  `use_bm25=False` (vector-only-plus-rerank), confirming reranking runs against
  whatever candidates exist regardless of which retrievers produced them.
- `rerank_top_n <= 0`: parametrized `[0, -1]`, asserting an empty pool reaches
  `reranker.rerank` (a spy records the call with `[]`) and no crash, mirroring the
  existing `final_k`/`vector_k`/`bm25_k` non-positive tests already in this file.

**`tests/test_runner.py`** (additions and one update):
- **Update** `test_ablation_configs_are_explicit_and_support_recall_at_10`: expect
  four names ending in `"+ Cross-encoder rerank"`; assert `final_k >= 10` for all
  four; assert `use_rerank is False` for the first three and `True` for the fourth;
  keep `use_graph is False`/`use_rewrite is False` for all four.
- New: `run_benchmark` with a config with `use_rerank=True`, `pipeline.run_pipeline`
  monkeypatched to a stub that records the `reranker` keyword argument it received
  across all N questions — assert every call received the **same object**
  (`is`-identity, not just equality), proving one reranker instance is built and
  reused, not one per question (section 5.4). Also monkeypatch
  `runner.CrossEncoderReranker` to a fake class with a call counter; assert it was
  constructed exactly once for N questions, and zero times for a `use_rerank=False`
  config.
- New: the warm-up timing print happens exactly once per `run_benchmark` call when
  `use_rerank=True`, and not at all when `False` (capture stdout via `capsys`).

**Focused test commands** (offline unless noted; run after each step, per the
collaboration routine in section 0):
```bash
.venv/bin/python -m pytest -q tests/test_rerank.py
.venv/bin/python -m pytest -q tests/test_pipeline.py
.venv/bin/python -m pytest -q tests/test_runner.py
.venv/bin/python -m pytest -q tests/test_pgvector_store.py tests/test_bm25.py  # DB-dependent, skip-if-unreachable
```

**Full suite, required before accepting Day 12** (this will, for the first time in
this project, trigger the real model download the first time `test_rerank.py`'s
"real construction" path or the smoke test in section 9 runs — everything else in
`test_rerank.py` uses the injected fake model and never triggers it):
```bash
.venv/bin/python -m pytest -q
```
Expected: **194 (Days 1–11 baseline) + every new Day 12 test above, all passing,
zero regressions.** Section 10 restates this as the numeric acceptance bar without
guessing the new count in advance, for the same reason Days 8–11's plan gave for not
pre-guessing a new total: several of the new tests above (`test_rerank.py`
especially) don't have a fixed count decided yet.

## 9. Real Day 12 evaluation

**Step 1 — smoke test** (one real question, real model, real embedding + generation
call, run manually before the full 40-question evaluation): use `q020`, the
"DNS-definition question" already in the benchmark and already named in
`DAY_11_ANALYSIS.md` as a regression this feature targets:
```bash
.venv/bin/python scripts/ask.py <resolved-repo-id> \
  "Which blocks contain at least one private_dns_enabled = true setting?"
```
Confirm the answer is coherent and, ideally, that `module.vpc_endpoints` (q020's
`expected`) is among the cited blocks — a strong, informal signal before spending the
full 40-question budget, not a substitute for the real run below.

**Step 2 — cost confirmation, before any paid/local work**: `scripts/run_eval.py`'s
existing `confirm_cost` gate (unchanged) already prints the question/config count
and requires `y` (or `--yes` to skip, not used here). State plainly, before running:
- **~40 paid OpenAI embedding requests** (one per question, uncached, same as every
  prior config — section 3.7's Days 8–11 reasoning is unchanged and still applies).
- **Zero OpenAI generation requests** — evaluation never calls `answer_question`
  (unchanged guarantee, still tested).
- **Up to ~2,000 local cross-encoder pairs** (40 questions × up to 50 candidates
  each = 2,000; the real number will likely be somewhat lower wherever a question's
  fused candidate pool has fewer than 50 unique entries) — **local compute, not a
  paid API cost**, running on this machine's CPU.
- **One model warm-up** (section 5.4/5.6/7) before the timed loop — its wall-clock
  time (download-if-needed plus load) is printed separately and is **not** part of
  any question's `rerank_ms`.

**Step 3 — run only the fourth configuration**, not all four (the first three are
already accepted from Day 11; re-running them would waste real API cost for no new
information):
```bash
.venv/bin/python scripts/run_eval.py --repo-id <resolved-repo-id> \
  --config "+ Cross-encoder rerank"
```
This produces exactly one new timestamped JSON report (section 3.8's collision-safe,
exclusive-create behavior, unchanged) containing one `ConfigResult`, with full
provenance (unchanged `build_report`, section 4) — including `indexed_corpus_sha256`
matching the Day 10/11 reports' value, confirming the same indexed corpus.

**Step 4 — inspect before accepting, same standard as Day 11**:
- Compare the new row's aggregate Recall@5/Recall@10/MRR/`mean_latency_ms` against
  the Day 11 table (section 1) — is `rerank_ms` the dominant cost, as SPEC's own Day
  12 note ("it will be the slowest stage") predicts? If not, that itself is worth
  explaining, not silently accepting.
- **Specifically inspect `q020`, `q037`, `q038`, `q039`, `q014`, `q016`** —
  per-question `recall_at_5`/`recall_at_10` for the new row versus the same
  questions' values in the accepted Day 11 "Vector + BM25 + RRF" row. The concrete,
  falsifiable expectation from `DAY_11_ANALYSIS.md`'s own stated hypothesis:
  reranking should recover at least some of what RRF's fused ordering pushed below
  the final top 10 for these specific questions, since they're now inside the
  `rerank_top_n=50` pool and get re-scored against the real question text rather
  than RRF's rank-sum. If none of them improve, that's a real, reportable finding
  (write it down, per hard constraint 3), not a reason to adjust the benchmark or
  the config to manufacture an improvement.
- Compare per-category breakdowns the same way Day 11's analysis did — `attribute`
  is the category most represented among the named regressions, so it's the most
  informative one to check first.
- **Investigate anything surprising before accepting**, identical standard to Day
  11, Step 2 — a suspicious number is a bug to find, not a footnote.

**Step 5 — accept and commit, or fix and re-run**: if the numbers check out, commit
the new JSON report file, following the same deliberate review-then-stage workflow
as Days 8–11 (`git add` only the one accepted file, never a blanket `git add
data/eval_results/`). Write a short `DAY_12_ANALYSIS.md` alongside it (matching
`DAY_11_ANALYSIS.md`'s precedent) explaining the row-four numbers, the specific
per-question comparisons above, and whether reranking's latency cost matched
expectations. **Never hand-edit any measured metric** — if a bug is found, fix the
code, re-run the fourth configuration from scratch, and only then treat the numbers
as final. This is the same standard section 1's Day 11 baseline was held to.

## 10. Scope and process

- `SPEC.md` stays read-only; no edits proposed or made.
- The two existing accepted JSON reports and `DAY_11_ANALYSIS.md` are **not**
  modified — Day 12 adds one new report and one new analysis file alongside them.
- **Not implemented this cycle**: graph expansion (Day 13), query rewriting (Day
  15), Pinecone (`PineconeStore`, Day 20), RRF weighting/tuning, or a BM25-index
  caching fix (both already-flagged, deferred items from the Days 8–11 plan, still
  deferred). RRF-tuning ideas from `DAY_11_ANALYSIS.md`'s own "follow-up" section
  are recorded here as **future work only** — not built, not scheduled, not implied
  by anything in this plan.
- No credentials are ever exposed, printed, logged, or committed — unchanged
  standing rule; nothing about reranking touches `.env` or secrets differently than
  any prior stage.
- Model cache files (`~/.cache/huggingface/...`) are never committed — they live
  outside the repository entirely (section 5.1), so there is nothing to add to
  `.gitignore` and nothing to accidentally `git add`.
- `.venv/bin/python` is used in every command in this plan; no bare `python`/
  `python3` anywhere.
- `repo_id` is never hardcoded in application code — always a CLI/runtime argument
  (`--repo-id`, unchanged convention).
- Collaboration routine: section 0.

## 11. Acceptance criteria

Day 12 is complete only when all of the following hold:
- Reranking works behind `use_rerank`, with the exact semantics in section 5.3/5.4.
- Disabled (`use_rerank=False`) behavior is provably unchanged — every pre-existing
  test in `test_pipeline.py` still passes unmodified, plus the new explicit
  never-called test.
- Real model inference runs in one batched `.predict()` call per question (not one
  call per candidate), verified by the smoke test (section 9, step 1) and by
  `test_rerank.py`'s call-count assertion.
- `rerank_ms` and every reranked candidate's score are recorded
  (`latency_json`/`stages_json`), verified by tests and by the real report.
- `.venv/bin/python -m pytest -q` is fully green: the Day 8–11 baseline (194) plus
  every new Day 12 test.
- The fourth real ablation row exists, in one committed, timestamped JSON report,
  with full provenance (`indexed_corpus_sha256`, `git_revision`,
  `benchmark_sha256`, complete `RetrievalConfig`) matching the established schema.
- The result has been inspected (section 9, step 4) and is explainable — including
  an honest account if the named regression questions do *not* improve.
- The accepted report and its `DAY_12_ANALYSIS.md` are committed intentionally,
  together, in a commit separate from the code commit(s) that implemented
  reranking (matching Days 8–11's per-milestone commit convention, section 0/7 of
  the prior plan history).

## 12. Needs sign-off

**None.** Every decision in section 5 was resolvable directly from SPEC.md's
explicit text (9.6/9.7/9.11/10.2/10.3) or by following an existing, already-
established convention in this codebase (the `final_k=10` evaluation-override
precedent for the Recall@10 tension; the config-driven `executed` pattern already
used for vector/bm25/fusion; `fusion.py`'s `dataclasses.replace` and `(-score,
address)` tie-break convention; the `embedder`/`Reranker` injection-point pattern
already used for `OpenAIEmbeddingProvider`). If you disagree with any specific
resolution above — particularly section 5.2's default-vs-required choice for
`RetrievedBlock.embed_text`, or section 5.4's decision to have `run_benchmark`
construct and reuse one reranker rather than relying on `run_pipeline`'s own
default — flag it before implementation starts; both were judgment calls made with
explicit reasoning, not SPEC-mandated.

## 13. Audit — stale assumptions, duplication, impossible tests, accidental downloads

- **Stale assumptions**: checked `ripple/config.py`, `pipeline.py`,
  `runner.py`, `run_eval.py`, `fusion.py`, `vector_store.py`, `pgvector_store.py`,
  `bm25.py` fresh this cycle (section 4) rather than reasoning from SPEC pseudocode
  or from the Days 8–11 plan's own descriptions of them — every claim in sections
  4–7 traces to a specific file/line read this cycle, not to memory of an earlier
  cycle's plan.
- **Duplicated steps**: none of section 6's file changes overlap with unfinished
  Days 8–11 work (all of it is committed, section 1) or with each other; `fusion.py`
  and `scripts/run_eval.py` are explicitly listed as needing **no** change precisely
  to avoid a redundant "update it anyway" step.
- **Impossible tests**: every test in section 8 is either fully offline (fake
  model/reranker/pipeline injection, matching established patterns) or DB-dependent
  with the existing skip-if-unreachable convention — none require the real
  `BAAI/bge-reranker-base` download to pass, so the suite stays runnable on a
  machine with no network access, same as every prior day's suite.
- **Accidental model downloads in CI**: verified the design ensures merely
  importing `ripple.retrieval.rerank`/`pipeline.py` does not import
  `sentence_transformers` (deferred import inside `_get_model()`); every
  `use_rerank=False` test path (the default across ~all of `test_pipeline.py`)
  never constructs a `CrossEncoderReranker` at all; every `test_rerank.py` test
  injects a fake model. The only two places a real download can be triggered are
  the smoke test and the real evaluation run in section 9, both explicitly manual,
  confirmed steps — never part of `pytest`.
- **Conflicts with current code**: none found. `RetrievedBlock.embed_text`'s
  trailing-with-default placement was specifically checked against Python's
  dataclass field-ordering rule (non-default fields cannot follow default fields)
  to avoid a `TypeError` at class-definition time.

## 14. Summary

1. **Files to create**: `ripple/retrieval/rerank.py`, `tests/test_rerank.py`.
2. **Files to modify**: `ripple/retrieval/vector_store.py`,
   `ripple/retrieval/pgvector_store.py`, `ripple/retrieval/bm25.py`,
   `ripple/retrieval/pipeline.py`, `ripple/evaluation/runner.py`,
   `tests/test_pgvector_store.py`, `tests/test_bm25.py`, `tests/test_pipeline.py`,
   `tests/test_runner.py`, `tests/test_run_eval.py` (cosmetic rename only).
3. **Tests to add/update**: full new `tests/test_rerank.py`; additions to
   `test_pgvector_store.py`, `test_bm25.py`, `test_pipeline.py`, `test_runner.py`;
   one updated assertion each in `test_pipeline.py` and `test_runner.py` (section
   4/8); one cosmetic rename in `test_run_eval.py`.
4. **Paid/local compute expected**: ~40 OpenAI embedding requests (paid, uncached,
   same as every prior config), 0 OpenAI generation requests, up to ~2,000 local
   cross-encoder pairs across the 40 questions (CPU, this machine), plus one
   one-time model download/load (a few hundred MB, cached after the first time).
5. **Remaining ambiguity**: none requiring your sign-off (section 12) — two
   explicitly-flagged judgment calls (embed_text default-vs-required,
   reranker-reuse-in-run_benchmark) are open to your override before implementation
   if you disagree, but this plan is otherwise implementation-ready as written.
