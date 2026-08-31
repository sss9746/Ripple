# Implementation Plan — Day 13: Graph Expansion

## 0. Process note for this cycle

**`SPEC.md` is read-only.** Nothing below proposes editing it; any place SPEC's
text is genuinely ambiguous is resolved here with explicit reasoning and flagged
in section 12 for your review, never silently guessed past.

**Only this file is modified in this planning cycle.** No application code, tests,
or other files change until a step from section 6 is implemented.

This is a **revision** of the prior Day 13 plan, correcting a second round of
review findings that caught a real, evaluation-breaking algorithm bug: the
proposed `known_ids`-based dedup would have skipped promoting any graph neighbor
that already existed anywhere in the up-to-50-item reranked candidate pool,
including at rank 11–50 — meaning the `q011` smoke test the plan itself proposed
could fail even when `module.vpc` genuinely is a direct dependency, because a
neighbor sitting at rank 30 would never be moved next to its seed and would still
be cut by `final_k=10`. This revision replaces "skip if present anywhere" with a
three-case promote/add/leave-alone algorithm (section 5.4/5.7), corrects a score-
misattribution problem (a graph-only addition was inheriting its seed's reranker
score, which the model never actually assigned to it — section 5.5), tightens
graph query ordering to a genuine total order (section 5.3), corrects the plan's
own stated test baseline to match what was actually verified and by whom (section
1), and adds a required blast-radius verification alongside `q011`'s relational
one (section 9).

**A third round of review found four further corrections, all applied here**:
the blast-radius smoke question was `q014`, whose own top two candidates
(`aws_security_group.rds`, `module.vpc_endpoints`) are both immutable
`graph_seed_n=3` seeds — meaning the check asked the algorithm to produce
exactly the outcome section 5.4's seed-exclusion rule is designed to prevent;
replaced with `q016`, whose expected dependent
(`output.vpc_endpoints_security_group_arn`) sits at rank 4, genuinely eligible
for promotion (section 9). The `ORDER BY resource.address, edges.ref_text,
resource.id` tiebreaker from the prior round can still tie — two edge rows for
the same pair with identical `ref_text` also share the same joined
`resource.id` — so `edges.id` is added as a fourth, genuinely final tiebreaker
(section 5.3). The BM25-only graph integration test assumed two specific
addresses would be absent from the candidate pool without forcing it; it now
sets `bm25_k=1` explicitly so that's guaranteed by construction, not assumed
(section 8). The smoke script constructed a fresh `CrossEncoderReranker` for
each of its two questions; it now prepares one instance and shares it across
both (section 9).

This plan replaces the prior Day 13 plan (section 1 is a short completed-baseline
summary for Day 12). It covers **Day 13 only** — graph expansion.

**Collaboration routine, unchanged:**
1. Explain each step in plain language before it happens.
2. You decide whether you implement it or Codex does.
3. Run the focused tests for that step before moving on.
4. Review the diff.
5. Run the complete suite.
6. Run the smoke test, then the full evaluation, only after your explicit
   confirmation — two separate confirmations, matching Day 12's precedent.
7. Commit the accepted Day 13 code and the accepted Day 13 report separately.

`.venv/bin/python` is used in every command; never bare `python`/`python3`.

## 1. Completed baseline — Day 12 (for context, not re-execution)

- `ripple/retrieval/rerank.py`, `pipeline.py`'s rerank stage, `RetrievedBlock.
  embed_text` (required), and the fourth ablation row (`"+ Cross-encoder
  rerank"`) are implemented, tested, and accepted.
- The accepted Day 12 report
  (`data/eval_results/2026-08-31T00-52-40-886297Z.json`, `schema_version: 2`) and
  `data/eval_results/DAY_12_ANALYSIS.md` are **not modified by this cycle** — Day
  13 adds a new, separate report.
- Measured Day 12 numbers this plan's row five is compared against:

  | Configuration | Recall@5 | Recall@10 | MRR | Latency (ms) |
  |---|---:|---:|---:|---:|
  | Vector only | 0.746 | 0.821 | 0.696 | 2341.32 |
  | Vector + BM25 | 0.804 | 0.835 | 0.696 | 4831.00 |
  | Vector + BM25 + RRF | 0.702 | 0.821 | 0.658 | 4093.96 |
  | + Cross-encoder rerank | 0.854 | 0.900 | 0.746 | 6644.35 |

- `relational` remains the weakest category under reranking alone: Recall@5
  0.500, Recall@10 0.600, MRR 0.163 (`DAY_12_ANALYSIS.md`) — reranking can only
  reorder candidates that retrieval already found; it cannot add a missing
  dependency edge. This is exactly the gap SPEC frames graph expansion as closing.
- **`q011` is a named, accepted regression from Day 12**: `"What block does the
  DynamoDB endpoint policy directly depend on for its VPC ID?"` (relational,
  expected `["module.vpc"]`) went from a partial hit under RRF to a **complete
  miss** under reranking — `module.vpc` moved from rank 8 (pre-rerank) to
  somewhere outside the top 10 after reranking, though it remains **within** the
  up-to-50-item reranked candidate pool (it was never dropped by retrieval itself
  — reranking only reordered it). This plan's smoke test (section 9) uses this
  question specifically, and section 5.4/5.7's corrected algorithm is what makes
  recovering it actually possible (the prior draft's algorithm could not have).
- **Corrected test baseline, stated honestly rather than asserted as one number
  (finding 4 of this revision)**: `.venv/bin/python -m pytest -q` currently
  **collects 213 tests**. Two different, both-legitimate results exist for this
  baseline, from two different environments:
  - **This session's own verification, with a reachable database**: 213 passed,
    0 skipped, 0 failed (run fresh during Day 12's acceptance review, this
    conversation).
  - **The accepted Day 12 commit's own recorded acceptance run** (`DAY_12_
    ANALYSIS.md`, whichever environment Codex ran it in): **197 passed, 16
    environment-dependent integration tests skipped, 0 failed** — the 16 skips
    are the DB-dependent, skip-if-unreachable tests (`test_db.py`, `test_bm25.py`,
    `test_graph.py`, `test_pgvector_store.py`, and the new Day 12 integration
    test), correctly skipping when that environment had no reachable database.
  Both are legitimate, zero-failure results; they differ only in database
  reachability, not in code correctness. **This plan does not claim "213 passing"
  as an unconditional fact** — whoever runs Day 13's acceptance suite should
  state which of these two shapes they got, and should specifically **aim for
  the DB-enabled shape** (213 passed, 0 skipped) so the new Day 13 database
  integration test (section 8) actually runs and is not silently skipped.

## 2. Objective

Wire graph expansion (SPEC 9.8) into the pipeline behind `RetrievalConfig.
use_graph`, add graph-relationship provenance to `RetrievedBlock` and the
rendered prompt context, add the fifth ablation row (`"+ Graph expansion"`, SPEC
10.3), and produce one real, accepted, investigated evaluation report — without
query rewriting, RRF tuning, Pinecone work, or any change to the cross-encoder.

## 3. Relevant SPEC.md requirements, quoted

- **Section 6 (Architecture)**: `... RRF fusion → Cross-encoder reranking → Graph
  expansion → Context construction → LLM`. Graph expansion is the stage
  immediately after reranking and immediately before the blocks are formatted
  into the prompt.
- **Section 9.8 (Graph expansion)**, quoted in full:
  ```sql
  -- dependents: what references this block (blast radius)
  SELECT r.* FROM edges e JOIN resources r ON r.id = e.source_id
  WHERE e.target_id = $1;

  -- dependencies: what this block references
  SELECT r.* FROM edges e JOIN resources r ON r.id = e.target_id
  WHERE e.source_id = $1;
  ```
  > After reranking, for each of the top N results (default 3), fetch neighbors...
  > Limits, all configurable: depth 1, at most 10 added blocks total, deduplicated
  > against what retrieval already returned. Added blocks are marked with their
  > relationship and the block they came from, so the prompt can say *"referenced
  > by aws_instance.node"*.
  >
  > Do not expand blindly to depth 2 or more. In a VPC module almost everything
  > reaches the VPC within two hops, so depth 2 returns most of the repo.
- **Section 9.10 (Prompt)**, context format per block:
  ```
  [3] aws_security_group.worker
      examples/complete/main.tf:42-67
      Referenced by: aws_instance.node
      <body>
  ```
  This is the only worked example SPEC gives of relationship rendering. Section
  5.5 resolves the exact direction-to-label mapping this example only partially
  specifies (it shows one direction; this plan needs both).
- **Section 9.11 (RetrievalConfig)**: `use_graph: bool = True`, `graph_seed_n:
  int = 3`, `graph_max_added: int = 10` — already exactly present in
  `ripple/config.py` (section 4 confirms). **`use_graph` defaults to `True`** —
  section 4.1 shows why this matters as much as `use_rerank`'s default did on Day
  12.
- **Section 10.2 (Metrics/latency)**: `graph` is one of the named per-stage
  latency fields — `graph_ms` joins the existing pattern. No changes needed to
  `ripple/evaluation/metrics.py`.
- **Section 10.3 (Ablation table)**: row five's exact label is `"+ Graph
  expansion"`.
- **Section 11, Day 13**, quoted: `"Wire graph.py into the pipeline behind
  use_graph, with the limits from section 9.8. Verify on a blast-radius question
  that dependents appear in the final context. Done when: row five exists, and
  the per-category breakdown shows where it helped."` — section 9 of this plan
  now requires exactly this blast-radius verification explicitly, not just the
  relational `q011` check.
- **Section 12 (Risk register)**: `"Graph expansion shows no gain | Usually means
  the benchmark is all lookup questions. Check category mix on Day 9."` — not a
  concern here: the benchmark's category mix (15/10/8/7) was built specifically so
  `relational`/`blast_radius` carry real weight (Day 9).

## 4. Current-state audit — what the real code does today, read fresh this cycle

**`ripple/retrieval/graph.py`** — `GraphNeighbor` has `id, address, file_path,
start_line, end_line, body, ref_text`. **No `embed_text`, no `score`.**
`dependents(resource_id)` returns blocks whose body references `resource_id`
(`WHERE edges.target_id = %s`, joined to the referencing/`source_id` block) —
i.e., blocks that **depend on** the given resource. `dependencies(resource_id)`
returns blocks the given resource's body references (`WHERE edges.source_id =
%s`, joined to the referenced/`target_id` block) — i.e., the blocks the given
resource itself **depends on**. Both queries currently `ORDER BY
resource.address` only — **not a total order** (section 5.3 fixes this).
Neither function is called from `pipeline.py` today.

**`ripple/retrieval/vector_store.py`** — `RetrievedBlock` has `id, address,
file_path, start_line, end_line, body, embed_text, score` — `score: float`,
required, no graph-relationship fields.

**`ripple/retrieval/pipeline.py`** — builds `candidates` through vector/bm25/
fusion/rerank exactly as Day 12 left it, then does `blocks = candidates[:
config.final_k]`. **No graph stage exists.** `_build_config_json`'s `"executed"`
dict hardcodes `"graph": False`. `run_pipeline` has no graph-related parameter.

**`ripple/llm/prompts.py`** — `format_context` renders `"[{i}] {address}\n
{file_path}:{start_line}-{end_line}\n{body}"` for every block. **There is no
"Referenced by"/"Depends on" line at all today** — SPEC 9.10's example format
line has never been implemented, for any block, graph-sourced or not.

**`ripple/config.py`** — `RetrievalConfig` already has `use_graph: bool = True`,
`graph_seed_n: int = 3`, `graph_max_added: int = 10`, matching SPEC 9.11 exactly.
**No change needed here.**

**`ripple/evaluation/runner.py`** — `ABLATION_CONFIGS` has exactly the four Day
8–12 rows. `run_benchmark`/`build_report` are generic over `ConfigResult` and
need no structural change for a fifth row (section 5.9 explains why graph needs
no new report-schema field, unlike reranking's `reranker_json`).

**`scripts/run_eval.py`** — confirmed still fully generic over `ABLATION_CONFIGS`'
length (unchanged since Day 12). **No changes needed.**

### 4.1 The `use_graph` default-to-`True` problem — audited across every existing `RetrievalConfig(...)` in `tests/test_pipeline.py`

This is the exact same class of defect Day 12's review caught for `use_rerank`,
now present for `use_graph`: **`RetrievalConfig.use_graph` defaults to `True`,
and every single `RetrievalConfig(...)` construction in `tests/test_pipeline.py`
today — all 21 of them — never sets `use_graph`.** Once graph expansion is wired
in, every one of these tests would start executing the new graph stage, and —
for the majority of them, whose candidate lists are non-empty — would attempt a
**real, unmocked `graph.dependents`/`dependencies` call against whatever
database `ripple.db.get_connection()` resolves to** in that test process.

| Test | Line | Candidates non-empty at graph stage? | Verdict |
|---|---|---|---|
| `test_run_pipeline_vector_only` | 130 | yes | **must add `use_graph=False`** |
| `test_run_pipeline_bm25_only_without_openai_key` | 160 | yes | **must add `use_graph=False`** |
| `test_run_pipeline_fuses_vector_and_bm25_results` | 191 | yes | **must add `use_graph=False`** |
| `test_run_pipeline_concatenates_when_rrf_is_disabled` | 234 | yes | **must add `use_graph=False`** |
| `test_run_pipeline_with_both_retrievers_disabled` | 257 | empty | **must add `use_graph=False`** (exact-key-set assertions) |
| `test_config_json_separates_requested_and_executed_stages` | 273 | empty | **must add `use_graph=False`** — asserts `executed["graph"] is False`; once `executed.graph` becomes config-driven (section 5.7), that's only still true if the config disables it |
| `test_config_json_records_executed_fusion_method` (×2 params) | 311 | empty | **must add `use_graph=False`** |
| `test_unsupported_vector_backend_fails_before_external_calls` | 332 | never reached (raises earlier) | **must add `use_graph=False`** for audit consistency |
| `test_unused_unsupported_vector_backend_does_not_raise` | 350 | empty | **must add `use_graph=False`** |
| `test_nonpositive_final_k_returns_no_final_blocks` (×2 params) | 375 | yes | **must add `use_graph=False`** |
| `test_nonpositive_vector_k_skips_embedding_and_query` (×2 params) | 398 | empty | **must add `use_graph=False`** |
| `test_nonpositive_bm25_k_returns_no_bm25_results` (×2 params) | 422 | empty | **must add `use_graph=False`** |
| `test_negative_rrf_k_raises_when_rrf_runs` | 443 | never reached (raises earlier) | **must add `use_graph=False`** |
| `test_negative_rrf_k_is_ignored_when_rrf_is_disabled` | 460 | yes | **must add `use_graph=False`** |
| `test_negative_rrf_k_is_ignored_with_only_one_retriever` (×2 params) | 486 | yes | **must add `use_graph=False`** |
| `test_final_stage_matches_truncated_pipeline_blocks` | 515 | yes | **must add `use_graph=False`** |
| `test_disabled_rerank_never_calls_reranker` | 540 | yes | **must add `use_graph=False`** |
| `test_rerank_uses_fused_top_n_before_final_k` | 570 | yes | **must add `use_graph=False`** |
| `test_enabled_rerank_runs_with_vector_only` | 604 | yes | **must add `use_graph=False`** |
| `test_enabled_rerank_records_empty_stage_for_no_candidates` | 627 | empty | **must add `use_graph=False`** |
| `test_nonpositive_rerank_top_n_passes_empty_pool` | 657 | empty | **must add `use_graph=False`** |

**Net result: all 21 existing sites get `use_graph=False` added — zero
exceptions.** The new graph tests (section 8) are the only places
`use_graph=True` appears in this file, and every one injects fake
`dependents`/`dependencies` functions.

### 4.2 `tests/test_runner.py`'s existing assertion that will need updating

`test_ablation_configs_are_explicit_and_support_recall_at_10` currently asserts
`config.use_graph is False` **unconditionally, for every row** — this must
become conditional on row index once the fifth row sets `use_graph=True`
(section 5.9/8).

## 5. Design decisions

### 5.1 Pipeline ordering — resolved and now confirmed (no longer a sign-off item)

**Graph expansion runs after reranking (or after fusion/retrieval, if reranking
is disabled) and *before* `final_k` truncation — graph-discovered blocks
therefore compete for slots inside `final_k`; they are not appended after it.
You have approved this decision explicitly.** The reasoning, unchanged: if
graph-discovered blocks were appended *after* an already-`final_k`-truncated
(10-item, for evaluation) base list, they would occupy positions 11+ in
`result.blocks`, and SPEC's own `recall_at_k` formula (`set(retrieved[:k])`)
means a block at position 11+ can never appear in `retrieved[:10]` or
`retrieved[:5]` — making graph expansion structurally incapable of ever
affecting a Recall@5/Recall@10 number, contradicting SPEC's Day 13 "Done when"
and the risk register's expectation of a measurable gain.

**What changes this revision is *how* blocks compete for those slots**
(sections 5.4/5.7) — the prior draft's naive "skip if present anywhere in the
top 50" dedup rule technically kept this ordering decision but implemented it in
a way that could never actually promote a real, relevant, lower-ranked
candidate — the exact bug this revision fixes. The ordering decision itself
(graph before `final_k`) is unchanged and confirmed.

**Consequence, stated plainly, unchanged**: this is a genuine trade, not a free
addition — promoting or inserting a graph-discovered block ahead of a
lower-ranked retrieval candidate can push that candidate below `final_k` and out
of the result entirely. The per-category breakdown (section 9) is where you see
whether the trade was worth it.

**Context construction**: `result.blocks` is still exactly the list sent to
`format_context` — capped at `final_k` (10 for evaluation). Graph expansion
never causes more than `final_k` blocks to reach the prompt.

### 5.2 Seeds and limits

- **Seeds**: the first `graph_seed_n` entries of the candidate list at the point
  graph expansion runs (post-rerank, pre-`final_k`), in that list's existing
  rank order — a strict prefix, not a re-sort, captured as an **immutable
  snapshot** (`seeds = candidates[:seed_n]`) before any promotion/insertion
  begins. This snapshot, and the `seed_ids` set derived from it, never change
  during the expansion pass — satisfying "seed selection must still use an
  immutable snapshot of the original top `graph_seed_n` candidates" exactly.
- **`graph_seed_n <= 0`**: no seeds, no expansion. `seed_n = max(config.
  graph_seed_n, 0)`; `candidates[:0] == []` — no negative-slice reinterpretation
  risk.
- **`graph_max_added <= 0`**: no promotions and no additions, regardless of how
  many seeds or neighbors exist. `max_added = max(config.graph_max_added, 0)`;
  the shared `action_count < max_added` guard (section 5.7) is `False` from the
  start.
- **Depth is exactly one, structurally**: the seed loop iterates over the
  **original, unmodified** pre-graph `candidates` list and the **immutable**
  `seeds`/`seed_ids` snapshot; neither a freshly-added block nor a promoted
  block is ever added to `seed_ids` or iterated over as a new seed. There is no
  recursive call and no code path that could reach a depth-two neighbor. Section
  8's dedicated test proves `dependents`/`dependencies` are called **only** with
  the original seeds' `id`s.
- **`graph_max_added` is a single, global cap covering both promotions and
  fresh additions together** — an explicit, tested decision (section 5.4),
  not left ambiguous.

### 5.3 Both edge directions, and a genuinely total deterministic order

For each seed, **both** `graph.dependents(seed.id)` and
`graph.dependencies(seed.id)` are checked — **dependents first, then
dependencies**, matching SPEC 9.8's own SQL comment order. Seeds are processed
in the candidate list's existing rank order.

**Corrected this revision, made genuinely total (a second fix, not just the
first)**: `graph.py`'s two queries change their `ORDER BY` from
`resource.address` alone to **`resource.address, edges.ref_text, resource.id,
edges.id`**. `resource.address` alone is not sufficient: if the same resource
pair were ever connected by more than one edge row with different `ref_text`
values (e.g. a data anomaly, a future relaxation of Day 4's one-edge-per-pair
dedup rule, or a manually inserted test fixture — section 8 constructs exactly
this scenario), two rows would tie on `resource.address` and their relative
order would be database-implementation-defined. `edges.ref_text` breaks that
tie. **`resource.id` alone is not a sufficient final tiebreaker either — this
was wrong in the immediately prior revision of this plan and is corrected
here**: two edge rows connecting the *same* `(source_id, target_id)` pair with
the *same* `ref_text` also join to the *same* `resource.id` (it's the identical
resource row on both sides of the join), so all three of `address`, `ref_text`,
and `id` can tie simultaneously, leaving the order still
implementation-defined. **`edges.id`** — the edge row's own primary key, always
unique per row regardless of how many columns of the *joined* resource happen
to match — is the fourth and genuinely final tiebreaker. `GraphNeighbor` itself
does not need to expose `edges.id` as a returned field; it is used only inside
`ORDER BY`. **No new randomness is introduced anywhere in this stage** — the
entire expansion is a pure function of `candidates`' (already-deterministic)
order plus this now-genuinely-total database order.

### 5.4 Deduplication and promotion — the exact three-case rule, replacing the prior "skip if present" bug

**The prior draft's bug, precisely**: it computed `known_ids = {block.id for
block in candidates}` once (the *entire* up-to-50-item reranked pool) and
skipped *any* discovered neighbor whose `id` was already in that set —
regardless of *where* in the pool it was. A neighbor sitting at reranked rank 30
is "already retrieved" in the sense that dedup cares about (it was already found
by earlier stages), but it is *not* going anywhere near `final_k=10` on its own
— and the prior algorithm had no mechanism to move it there. This is why `q011`
could fail the smoke test even when `module.vpc` is a real, correct dependency:
if it happened to already be present somewhere past rank 10 in the reranked
pool, it would be silently skipped, never promoted, and cut by `final_k` exactly
as if graph expansion had never run.

**Corrected rule — three cases, not one**, evaluated for every neighbor
discovered from a seed, in traversal order (section 5.3), against the
**immutable** pre-graph `candidates` snapshot and its position index
(`original_position: dict[id, int]`, built once before expansion starts):

1. **Absent from `candidates` entirely** (`original_position.get(neighbor.id) is
   None`) → **add** it fresh, immediately after the seed, with graph provenance
   attached and `graph_score_status="unscored"` (section 5.5 — it was never
   independently scored).
2. **Present, and it is itself one of the immutable seeds**
   (`neighbor.id in seed_ids`) → **leave it completely untouched**: no move, no
   provenance, no duplicate, not counted toward `graph_max_added`. This is the
   *only* case in which a discovered neighbor can already be "ranked earlier
   than the graph insertion position" — since `seeds = candidates[:seed_n]` is a
   prefix of a fully rank-ordered list, **every** non-seed candidate is,
   definitionally, ranked worse than **every** seed; there is no other
   configuration in which an existing candidate could already outrank the
   insertion point. Excluding seeds from promotion also avoids a block
   simultaneously being both an independent seed *and* a labeled "dependent of
   another seed" — two conflated identities this plan avoids on purpose.
3. **Present, and it is not a seed** (`original_position.get(neighbor.id)` is a
   real, non-seed position) → **promote** it: locate the original block object
   at that position, `dataclasses.replace(...)` it with the graph-provenance
   fields set and `graph_score_status="promoted"` (its own `score` field is left
   completely unchanged — section 5.5), remove its old occurrence (tracked via
   `moved_ids`, checked at the top of the main loop so its original slot is
   skipped rather than duplicated), and insert the replacement immediately after
   the seed, exactly like a fresh addition.

**First discovery wins, across seeds and directions, unchanged in spirit from
the prior draft**: a single `handled_ids: set[int]` accumulates across the
*entire* expansion stage; once a neighbor `id` has been decided (added,
promoted, or found to be a seed), it is never reconsidered by a later seed or
direction. If the same block is reachable as both a dependent of seed A and a
dependency of seed B, only the first-reached occurrence's relationship/origin/
`ref_text` is recorded — this is unchanged from the prior draft's rule, now
explicitly tested (section 8) with **duplicate neighbor rows carrying different
`ref_text` values**, to prove which provenance wins under the new total
`ORDER BY` (section 5.3).

**`graph_max_added` counts promotions and additions identically, on one shared
counter — an explicit choice, not left ambiguous**: SPEC 9.8 frames the limit as
"at most 10 added blocks total," and a promotion is just as much a
graph-driven change to the result (it moves a block's position and attaches
provenance) as a fresh addition is. Counting them separately would let
promotions bypass the cap entirely — e.g. relocating 50 low-ranked blocks "for
free" while 10 more fresh additions are also allowed — which would let graph
expansion touch far more of the result than SPEC's stated ceiling intends.
Section 8 has a dedicated test proving the shared cap is exact (a mix of
promotions and additions that together hit `graph_max_added` and no more).

**Preserve exactly one output occurrence per resource id**: guaranteed by
construction — a promoted candidate is removed from its old slot (`moved_ids`)
and appears exactly once at its new slot; a fresh addition appears exactly once
at its insertion slot; an untouched candidate (including every excluded seed)
appears exactly once at its original slot. No id can appear twice in
`augmented`.

**Self-loops** remain already impossible by construction — Day 4's reference
extraction explicitly excludes `source_id == target_id` edges (SPEC 9.2).

### 5.5 Graph provenance and honest score representation — corrected this revision

**Four new trailing fields on `RetrievedBlock`** (one more than the prior
draft), and **`score`'s type widens to `float | None`** — still a required
field (every constructor still passes it explicitly; only its accepted value
type grows to include `None`), not a separate result/provenance type:

```python
@dataclass
class RetrievedBlock:
    id: int
    address: str
    file_path: str
    start_line: int
    end_line: int
    body: str
    embed_text: str
    score: float | None                       # widened this revision
    graph_relationship: str | None = None     # "dependent" | "dependency" | None
    graph_origin_address: str | None = None   # the seed address this came from
    graph_ref_text: str | None = None         # the literal reference text
    graph_score_status: str | None = None     # "promoted" | "unscored" | None
```

**The prior draft's problem, precisely**: a freshly-added graph-only block
inherited its *seed's* reranker score unchanged. That score was never computed
against the added block at all — the cross-encoder scored the seed's
`(question, seed.embed_text)` pair, not `(question, neighbor.embed_text)`.
Displaying that number next to the neighbor in `stages_json`/`result.blocks`
could be misread as "the model rated this block's relevance at 0.94," which is
false — the model never saw this block.

**Corrected representation — chosen and documented, per case**:
- **A promoted existing candidate keeps its own original score, unchanged** —
  it *was* independently scored (by vector search, BM25, and/or the
  cross-encoder, whichever stages ran), and that score remains an honest,
  real measurement of relevance. `graph_score_status="promoted"` records that
  this block's position/provenance were touched by graph expansion, without
  implying its score was.
- **A genuinely new graph-only block gets `score=None` and
  `graph_score_status="unscored"`** — an explicit, typed statement that no
  retrieval or reranking stage ever scored this block, rather than a plausible-
  looking fabricated number. This was chosen over an unscored-but-still-a-float
  sentinel (e.g. `float("nan")`, rejected because `NaN` is not valid strict
  JSON and some parsers reject it, unlike `null`) specifically because `None`
  serializes to JSON `null` — a value every JSON consumer already understands as
  "absent," with zero risk of being misread as a real number.
- **`stages_json` distinguishes the two explicitly**: `_serialize_graph`
  (section 5.7) emits both `"score"` (the real value, possibly `null`) and a
  separate `"score_status"` key (`"promoted"` or `"unscored"`) for every entry
  in the graph stage's audit trail — a reader never has to guess which kind of
  score they're looking at.

**Downstream type/serialization effects, inspected before choosing (not
assumed)**:
- `fusion.py` and `rerank.py`'s sort keys (`-block.score` / `(-block.score,
  block.address)`) never encounter a graph-added block — both stages run
  *before* graph expansion in the pipeline, and a `None`-scored block is never
  constructed until after both have already finished. No change needed to
  either file, and no risk of `-None` raising `TypeError`.
- `ripple/evaluation/metrics.py`'s `score_question`/`aggregate` and
  `runner.py`'s `run_benchmark` never read `.score` at all — only `.address` (to
  build `retrieved`) and `.latency` are consumed for scoring. `score=None` on a
  graph-only block has **zero** effect on any Recall/Precision/MRR computation.
- `dataclasses.asdict(...)` and `json.dump(...)` both serialize `None` to `null`
  natively — no change needed to `build_report`, `write_report`, or
  `scripts/run_eval.py` for this.
- No existing test in `test_pipeline.py`, `test_fusion.py`, `test_rerank.py`, or
  `test_runner.py` asserts `isinstance(block.score, float)` or otherwise rejects
  `None` — confirmed by the same fresh read that produced section 4's audit;
  every existing assertion compares `.score` against a concrete float value on
  blocks that are never graph-touched, so widening the type is invisible to
  them.

**Why a separate provenance type was still rejected** (unchanged reasoning):
`pipeline.py`'s candidate list, `fusion.py`, `format_context`, and every
existing test fixture all operate on `list[RetrievedBlock]` uniformly; a second
type would force every consumer to branch on type. Four **optional** fields
(three unchanged from the prior draft, one new) and one widened-but-still-
required field cost nothing to every existing `RetrievedBlock(...)` construction
across the repository — zero fixture changes required anywhere except the new
graph-specific tests.

**Direction → prompt-label mapping, unchanged from the prior draft, derived from
the two example strings the original Day 13 request supplied**:
- A block found via **`dependents(seed)`** (the block depends on/references the
  seed) → `graph_relationship = "dependent"` → rendered as **`"Depends on:
  {seed_address}"`**.
- A block found via **`dependencies(seed)`** (the seed references the block) →
  `graph_relationship = "dependency"` → rendered as **`"Referenced by:
  {seed_address}"`**.

`ref_text` is retained on the block and in `stages_json` for auditability but is
not rendered in the prompt line itself, matching SPEC 9.10's own example exactly.

### 5.6 `GraphNeighbor` gets a real `embed_text`, deliberately — not a placeholder

Unchanged from the prior draft: `GraphNeighbor` gains one new required field,
`embed_text: str`, populated from a real `resources.embed_text` column read in
both `dependents`/`dependencies`' `SELECT` statements (positioned right after
`body`, with `ref_text` moved last to match: `id, address, file_path,
start_line, end_line, body, embed_text, ref_text`). `GraphNeighbor` does **not**
gain a `score` field — `score` (and now `graph_score_status`) are
`RetrievedBlock`-only concepts, populated by `pipeline.py` when converting a
`GraphNeighbor` into a `RetrievedBlock` (section 5.5/5.7).

### 5.7 Pipeline integration — the exact algorithm, corrected

`pipeline.py` imports `dependents`/`dependencies` directly from
`ripple.retrieval.graph`, matching the existing `build_index` import-and-
monkeypatch convention — tests patch `pipeline.dependents`/`pipeline.
dependencies`. No new injectable class is introduced for graph: `dependents`/
`dependencies` are cheap, stateless, per-call database reads with no model to
load and no reuse-across-questions cost, unlike the reranker.

Inserted between the existing rerank block and the existing `final_k`
truncation — **`final_k`'s own line of code moves down one block, its logic
unchanged**:

```python
if config.use_graph:
    graph_start = time.perf_counter()
    seed_n = max(config.graph_seed_n, 0)
    max_added = max(config.graph_max_added, 0)
    seeds = candidates[:seed_n]                 # immutable snapshot
    seed_ids = {block.id for block in seeds}
    original_position = {
        block.id: position for position, block in enumerate(candidates)
    }

    moved_ids: set[int] = set()     # promoted ids -- skip re-emitting their old slot
    handled_ids: set[int] = set()   # every id already decided this stage
    graph_actions: list[RetrievedBlock] = []   # promotions + additions, in order
    augmented: list[RetrievedBlock] = []
    action_count = 0

    for position, block in enumerate(candidates):
        if block.id in moved_ids:
            continue                            # already relocated; no duplicate
        augmented.append(block)

        if position >= seed_n:
            continue                            # immutable seed set, fixed above

        insertions_here: list[RetrievedBlock] = []
        for relationship, fetch in (
            ("dependent", dependents),
            ("dependency", dependencies),
        ):
            if action_count >= max_added:
                break
            for neighbor in fetch(block.id):
                if action_count >= max_added:
                    break
                if neighbor.id in handled_ids or neighbor.id in seed_ids:
                    continue   # first discovery wins; never move/relabel a seed
                handled_ids.add(neighbor.id)

                existing_position = original_position.get(neighbor.id)
                if existing_position is None:
                    new_block = RetrievedBlock(
                        id=neighbor.id,
                        address=neighbor.address,
                        file_path=neighbor.file_path,
                        start_line=neighbor.start_line,
                        end_line=neighbor.end_line,
                        body=neighbor.body,
                        embed_text=neighbor.embed_text,
                        score=None,
                        graph_relationship=relationship,
                        graph_origin_address=block.address,
                        graph_ref_text=neighbor.ref_text,
                        graph_score_status="unscored",
                    )
                else:
                    original_block = candidates[existing_position]
                    new_block = dataclasses.replace(
                        original_block,
                        graph_relationship=relationship,
                        graph_origin_address=block.address,
                        graph_ref_text=neighbor.ref_text,
                        graph_score_status="promoted",
                    )
                    moved_ids.add(neighbor.id)

                insertions_here.append(new_block)
                graph_actions.append(new_block)
                action_count += 1

        augmented.extend(insertions_here)

    candidates = augmented
    latency_json["graph_ms"] = (time.perf_counter() - graph_start) * 1000
    stages_json["graph"] = _serialize_graph(graph_actions)

if config.final_k > 0:
    blocks = candidates[: config.final_k]
else:
    blocks = []
```

Note that `existing_position`, once the `neighbor.id in seed_ids` check has
already passed, can only ever be `None` (absent) or a genuine non-seed position
— never a position earlier than the current seed's own position — which is
exactly section 5.4's proof that seed-exclusion is the *complete* resolution of
"already ranked earlier than the insertion point." No separate position
comparison is needed beyond the seed-membership check.

`_serialize_graph` (new, alongside the existing `_serialize`) now includes
`score_status` alongside every other provenance field:

```python
def _serialize_graph(blocks: list[RetrievedBlock]) -> list[dict]:
    return [
        {
            "id": block.id,
            "address": block.address,
            "score": block.score,
            "score_status": block.graph_score_status,
            "relationship": block.graph_relationship,
            "origin_address": block.graph_origin_address,
            "ref_text": block.graph_ref_text,
        }
        for block in blocks
    ]
```

`stages_json["graph"]` holds **only the blocks the graph stage touched**
(promotions and fresh additions, `graph_actions`) — not the whole augmented
list — parallel to how "fusion" holds the fused candidates and "final"
separately holds what actually survived truncation.

**`_build_config_json`'s `"executed"` dict**: `"graph": False` (hardcoded)
becomes `"graph": config.use_graph`, matching the existing config-driven
pattern. An enabled graph stage with zero seeds or zero discovered neighbors
still shows `executed.graph: true` and `stages_json["graph"]: []`.

**When `use_graph=False`** (all four pre-existing `ABLATION_CONFIGS` rows): the
new block is skipped entirely — no `dependents`/`dependencies` call, no
`graph_ms` key, no `stages_json["graph"]` key, and `final_k` truncation runs
against the exact same `candidates` it always has.

**`run_pipeline`'s signature is unchanged** — no new parameter.

### 5.8 Prompt integration

Unchanged from the prior draft:

```python
_GRAPH_RELATIONSHIP_LABELS = {
    "dependent": "Depends on",
    "dependency": "Referenced by",
}

def format_context(blocks: list[RetrievedBlock]) -> str:
    sections = []
    for index, block in enumerate(blocks, start=1):
        lines = [
            f"[{index}] {block.address}",
            f"    {block.file_path}:{block.start_line}-{block.end_line}",
        ]
        if block.graph_relationship is not None:
            label = _GRAPH_RELATIONSHIP_LABELS[block.graph_relationship]
            lines.append(f"    {label}: {block.graph_origin_address}")
        lines.append(f"    {block.body}")
        sections.append("\n".join(lines))
    return "\n\n".join(sections)
```

`format_context` never reads `.score`/`.graph_score_status` at all, so
section 5.5's score-representation change has no effect on prompt rendering —
confirmed by re-reading this function this cycle. Existing formatting for
ordinary (non-graph) blocks is byte-for-byte unchanged (`graph_relationship is
None` skips the new line entirely).

### 5.9 No new report-schema field is needed for graph (unlike reranking)

Unchanged: `dataclasses.asdict(result.config)` already includes `use_graph`/
`graph_seed_n`/`graph_max_added` for every row; graph expansion introduces no
external, independently-versioned resource the way the reranker did. No new
`ConfigResult` field, no `schema_version` bump — the report stays
`schema_version: 2`.

## 6. Exact file scope

**Create:** none.

**Modify:**
- `ripple/retrieval/graph.py` — add `GraphNeighbor.embed_text: str` (required);
  add `resource.embed_text` to both `SELECT` statements; change both
  `ORDER BY` clauses to `resource.address, edges.ref_text, resource.id,
  edges.id` (section 5.3/5.6).
- `ripple/retrieval/vector_store.py` — widen `RetrievedBlock.score` to `float |
  None`; add four trailing, defaulted fields: `graph_relationship`,
  `graph_origin_address`, `graph_ref_text`, `graph_score_status` (section 5.5).
- `ripple/retrieval/pipeline.py` — new graph stage (promote/add/leave-alone
  algorithm) inserted between rerank and `final_k` truncation;
  `_serialize_graph` helper; `executed.graph` becomes `config.use_graph`; import
  `dependents`/`dependencies` from `ripple.retrieval.graph` (section 5.7).
- `ripple/llm/prompts.py` — `format_context` renders the relationship line for
  graph-sourced blocks only (section 5.8).
- `ripple/evaluation/runner.py` — add the fifth `ABLATION_CONFIGS` row (section
  5.9 — confirmed no other change needed by reading `run_benchmark`/
  `build_report` this cycle).
- `tests/test_graph.py` — add an assertion that `embed_text` round-trips
  through `dependents`/`dependencies` against the real reference fixture,
  distinct from `body`; add a total-order determinism test using manually
  inserted duplicate edge rows with different `ref_text` (section 8).
- `tests/test_pipeline.py` — **audit-driven changes**: add `use_graph=False` to
  all 21 existing sites (section 4.1's table); add the new graph-expansion,
  promotion, and score-status tests (section 8).
- `tests/test_prompts.py` — add `graph_relationship`/`graph_origin_address`
  parameters to the `_block` helper (defaulted to `None`); add new tests for
  both relationship directions and for repository-content safety (section 8).
- `tests/test_runner.py` — update `test_ablation_configs_are_explicit_and_
  support_recall_at_10` for five rows (section 4.2).

**Do not modify**: `SPEC.md`, `sql/schema.sql`, `docker-compose.yml`,
`.env`/`.env.example`, `requirements.txt`, `ripple/config.py` (already correct,
section 4), `ripple/retrieval/fusion.py`, `ripple/retrieval/bm25.py`,
`ripple/retrieval/pgvector_store.py`, `ripple/retrieval/rerank.py`,
`ripple/llm/embeddings.py`, `ripple/llm/rewrite.py`, `ripple/llm/generate.py`,
`ripple/evaluation/dataset.py`, `ripple/evaluation/metrics.py`, `ripple/db.py`,
`ripple/ingest/*`, `scripts/run_eval.py` (confirmed still fully generic, section
4), `scripts/index_repo.py`, `scripts/ask.py`, `AGENTS.md`, `CLAUDE.md`,
`README.md`, `data/benchmark.json`, every existing file under
`data/eval_results/`, `data/eval_results/DAY_11_ANALYSIS.md`,
`data/eval_results/DAY_12_ANALYSIS.md`, `tests/test_dataset.py`,
`tests/test_metrics.py`, `tests/test_db.py`, `tests/test_fusion.py`,
`tests/test_bm25.py`, `tests/test_pgvector_store.py`, `tests/test_rerank.py`,
`tests/test_ask.py`, `tests/test_generate.py`, `tests/test_run_eval.py`.

## 7. Interfaces — the parts not already fully written in section 5

```python
# ripple/retrieval/graph.py -- the changed SELECT + dataclass
@dataclass
class GraphNeighbor:
    id: int
    address: str
    file_path: str
    start_line: int
    end_line: int
    body: str
    embed_text: str        # new, required
    ref_text: str          # moved last, to match the new SELECT order

# both dependents() and dependencies(): SELECT list becomes
# resource.id, resource.address, resource.file_path, resource.start_line,
# resource.end_line, resource.body, resource.embed_text, edges.ref_text
# ORDER BY resource.address, edges.ref_text, resource.id, edges.id -- genuinely total
```

## 8. Tests — exact files and assertions

**`tests/test_pipeline.py`**:
- All 21 sites from section 4.1's table get `use_graph=False` — no behavioral
  change to any existing assertion.
- New fakes: `_install_graph(monkeypatch, dependents_by_id, dependencies_by_id)`
  installing `pipeline.dependents`/`pipeline.dependencies` as lookups into two
  dicts (`id -> list[graph.GraphNeighbor]`), recording every call made, and
  raising if called with an id outside an explicitly-declared "allowed" set (for
  the depth-one test below).
- Disabled toggle never calls the graph functions.
- Seeds are exactly the first `graph_seed_n` candidates.
- Both directions checked, dependents before dependencies.
- **Depth stays exactly one**: fakes raise if called with any id outside the
  original seed set; assert no failure, and separately assert a scenario where
  a fake neighbor's own `id` is deliberately also present in the fakes'
  lookup tables (as if it were a valid seed) is never queried, proving newly
  discovered blocks are never used as seeds regardless of whether fake data
  *would* answer for them.
- **Promotion — the corrected algorithm's core new test**: a fake candidate
  list of more than `final_k` items where the seed's fake `dependents` result
  includes the `id` of a candidate sitting at **rank 11–50** (i.e., a rank that
  would be cut by `final_k=10` without intervention); assert that candidate
  **is** present in `result.blocks`, appears immediately after its seed,
  carries `graph_relationship`/`graph_origin_address`/`graph_ref_text`, has
  `graph_score_status == "promoted"`, and — critically — **its `.score` is its
  own original value, not the seed's**. This is the direct regression test for
  the bug this revision fixes, and the direct proof that `q011`'s smoke-test
  reasoning (section 9) is actually achievable by this algorithm.
- **Non-demotion**: a fake neighbor lookup that returns the `id` of *another
  seed* (a candidate at a position within the original top `graph_seed_n`);
  assert that seed is **not** moved, gains **no** graph-provenance fields, is
  **not** counted toward `graph_max_added`, and appears at its original
  position, unchanged.
- **Score representation, both cases in one test file** (finding 2): the
  promotion test above covers "promoted keeps its own score"; a second test
  covers a genuinely new (absent-from-candidates) fake neighbor — assert its
  `.score is None` and `graph_score_status == "unscored"`, and that
  `stages_json["graph"]`'s corresponding entry has `"score": None,
  "score_status": "unscored"` while a promoted entry in the same stage's output
  has a real float `"score"` and `"score_status": "promoted"`.
- Deduplication against base results and across seeds/directions, **including a
  duplicate-`ref_text` case** (finding 3): two fake neighbor rows for the same
  `id` with different `ref_text`, returned in a deliberately-scrambled order by
  the fakes; assert only one occurrence survives, carrying the provenance from
  whichever fake call happened first in the deterministic seed/direction
  traversal order (section 5.3) — not from whichever row the fake's own
  internal list ordering would have picked if the fakes didn't already return
  data in the real, total-order-respecting shape.
- **Global cap counts promotions and additions together** (finding 1's explicit
  requirement): a scenario with two seeds, one whose discovered neighbor would
  be a promotion and one whose discovered neighbor would be a fresh addition,
  `graph_max_added=1`; assert exactly one of the two graph actions happens
  (whichever is discovered first in traversal order), never both.
- `graph_seed_n <= 0` and `graph_max_added <= 0`, parametrized `[0, -1]` each.
- Empty candidate list with `use_graph=True`.
- **Graph competes inside `final_k`**: unchanged intent from the prior draft,
  now exercised via the promotion test above (a promoted rank-30 candidate
  surviving into `result.blocks` *is* the proof).
- `stages_json["graph"]`/`graph_ms`/`executed.graph` present only when
  `use_graph=True`.
- **Supabase integration test — made deterministic this revision (finding 3)**:
  the prior draft assumed `aws_security_group.worker`/`aws_subnet.public` would
  be *absent* from the BM25 candidate list, which is unsafe — both blocks'
  `embed_text`/`body` mention `aws_vpc.main` (that's the reference edge itself),
  so a lexical query for `aws_vpc.main` with the default `bm25_k=30` could
  plausibly retrieve them too, already present in `candidates` before graph
  expansion ever runs — which would make them **promotions**, not fresh
  additions, and silently invalidate the test's own premise. **Corrected**: set
  `bm25_k=1` explicitly in this test's `RetrievalConfig` (`use_vector=False,
  use_bm25=True, bm25_k=1, use_rerank=False, use_graph=True`), against the
  indexed `reference_repo` fixture, querying for `aws_vpc.main` directly. First
  assert the single BM25 base result actually **is** `aws_vpc.main` (not
  assumed) — with `bm25_k=1`, `candidates` has exactly one entry, so neither
  `aws_security_group.worker` nor `aws_subnet.public` can possibly already be
  present, by construction, not by assumption about ranking. Then assert the
  real `graph.dependents` result (`aws_security_group.worker`,
  `aws_subnet.public`) appears in `stages_json["graph"]` as genuinely **new**
  graph-only blocks: `relationship == "dependent"`, `origin_address ==
  "aws_vpc.main"`, correct real `ref_text`, `score is None`, and `score_status
  == "unscored"` (section 5.5) — never `"promoted"`, since `bm25_k=1` guarantees
  they weren't already in the candidate pool. Makes no OpenAI call and no
  reranker call.

**`tests/test_graph.py`**:
- Add an assertion that `neighbors[0].embed_text` equals the real `embed_text`
  column value for that resource, distinct from `body`.
- **New determinism test with duplicate edges — strengthened this revision
  (finding 2)**: `resource.address, edges.ref_text, resource.id` alone can
  still tie: two edge rows connecting the **same** `(source_id, target_id)`
  pair with the **same** `ref_text` also share the same `resource.id` (it's the
  same joined resource row both times), so all three columns tie and the order
  between them is still database-implementation-defined. **Corrected**: both
  `ORDER BY` clauses become `resource.address, edges.ref_text, resource.id,
  edges.id` — `edges.id` (the edge row's own primary key, always unique) is the
  final, guaranteed tiebreaker; `GraphNeighbor` itself does **not** need to
  expose `edges.id` as a field — it's used only inside `ORDER BY`, never
  selected or returned. Using a throwaway repo/resources setup, directly
  `INSERT` **two separate sets** of duplicate edge rows connecting the same
  `(source_id, target_id)` pair (bypassing the indexer's own one-edge-per-pair
  dedup): (a) two rows with **different** `ref_text` values, and (b) two rows
  with **identical** `ref_text` values (differing only in their own `edges.id`,
  which is whatever the database assigns on insert — the test reads it back
  after inserting, rather than assuming a specific value). Assert:
  `dependents`/`dependencies` return case (a)'s two rows ordered by `ref_text`
  (the third column breaks the tie); return case (b)'s two rows ordered by
  their actual `edges.id` values (the fourth column breaks the tie that
  `ref_text` alone could not); and running either query twice produces
  byte-identical results both times.

**`tests/test_prompts.py`**: unchanged from the prior draft — `_block` helper
gains `graph_relationship`/`graph_origin_address` parameters (defaulted to
`None`); existing tests stay unmodified; new tests for both relationship
directions, a mixed ordinary/graph-sourced list, and repository-content safety.

**`tests/test_runner.py`**: update `test_ablation_configs_are_explicit_and_
support_recall_at_10` — five names ending in `"+ Graph expansion"`; `final_k >=
10` for all five; `use_rerank is True` for rows 4 and 5, `False` for 1–3;
`use_graph is True` only for row 5; `use_rewrite is False` for all five.

**Focused test commands**:
```bash
.venv/bin/python -m pytest -q tests/test_graph.py
.venv/bin/python -m pytest -q tests/test_pipeline.py
.venv/bin/python -m pytest -q tests/test_prompts.py
.venv/bin/python -m pytest -q tests/test_runner.py
```

**Full suite** (no test in this plan downloads/constructs the real reranker
model or makes an OpenAI call; every graph test either injects fakes or is a
DB-only, skip-if-unreachable integration test):
```bash
.venv/bin/python -m pytest -q
```
Expected, stated honestly per section 1's corrected baseline: **213 collected
(the Days 1–12 baseline) plus every new Day 13 test, all passing.** Report the
actual passed/skipped split for whichever environment ran it — aim for the
DB-enabled shape (0 skipped) so the new graph integration tests actually run.

## 9. Real Day 13 evaluation

**Step 0 — first explicit confirmation, before any spending of any kind**: get
explicit go-ahead before Step 1. The smoke test below makes **two** real OpenAI
embedding requests (one per question checked), **zero** generation requests,
**one** real reranker preparation, and **two** local batched rerank
predictions (finding 4 — see the corrected script below); nothing runs before
confirmation.

**Step 1 — two smoke checks, relational (`q011`) and blast-radius (`q016`,
corrected this revision) — both required**: SPEC's own Day 13 "Done when"
explicitly calls out verifying dependents for a blast-radius question, not
just dependencies for a relational one; `q011` alone is not sufficient
evidence that the `dependents` direction works against real data.

**`q014` is replaced with `q016` — the prior draft's `q014` check was
incompatible with the algorithm it was meant to test (finding 1).** The
accepted Day 12 report ranks `q014`'s candidates
`["aws_security_group.rds", "module.vpc_endpoints", "module.vpc", ...]` — with
`graph_seed_n=3`, **both** `aws_security_group.rds` (rank 1) **and**
`module.vpc_endpoints` (rank 2) are already immutable seeds. Section 5.4's
case 2 rule *deliberately* leaves a discovered neighbor untouched, with no
move and no `stages_json["graph"]` entry, whenever it is already one of the
immutable seeds — so `module.vpc_endpoints` could **never** legitimately
appear in `stages_json["graph"]` as a dependent of the `aws_security_group.rds`
seed, no matter how correctly the algorithm runs. The prior smoke test asked
for exactly the outcome the algorithm is specifically designed *not* to
produce.

**`q016`** ("What is directly affected if `module.vpc_endpoints` is removed?")
is compatible: `module.vpc_endpoints` itself is Day 12's rank-1 result (the
seed), and `output.vpc_endpoints_security_group_arn` sits at **rank 4** —
outside `graph_seed_n=3` — so it is eligible for **promotion**, not excluded
like `q014`'s scenario was.

Both checks share one script, reusing **one** prepared `CrossEncoderReranker`
instance across both questions (finding 4 — the prior draft's script called
`run_pipeline` twice with no injected reranker, which would construct and load
the real model twice):

```bash
.venv/bin/python -c "
from ripple.evaluation.runner import ABLATION_CONFIGS
from ripple.retrieval import pipeline
from ripple.retrieval.rerank import CrossEncoderReranker

REPO_ID = ...  # never hardcoded -- resolve independently, e.g.:
                # from ripple import db
                # with db.get_connection() as conn, conn.cursor() as cur:
                #     cur.execute(
                #         \"SELECT id FROM repos WHERE name = 'vpc-complete' \"
                #         \"ORDER BY id DESC LIMIT 1\"
                #     )
                #     print(cur.fetchone())
config = dict(ABLATION_CONFIGS)['+ Graph expansion']

reranker = CrossEncoderReranker()
reranker.prepare()  # one real model load + one dummy prediction, shared below
print(f'--- reranker prepared in {reranker.prepare_ms:.0f}ms (one-time) ---')

CHECKS = [
    (
        'q011 (relational, dependencies)',
        'What block does the DynamoDB endpoint policy directly depend on for its VPC ID?',
        'module.vpc',
    ),
    (
        'q016 (blast_radius, dependents)',
        'What is directly affected if module.vpc_endpoints is removed?',
        'output.vpc_endpoints_security_group_arn',
    ),
]

for label, question, expected_address in CHECKS:
    result = pipeline.run_pipeline(REPO_ID, question, config, reranker=reranker)
    print(f'=== {label} ===')
    print('--- graph actions ---')
    for row in result.stages_json['graph']:
        print(row)
    addresses = [b.address for b in result.blocks]
    print('--- final addresses ---')
    print(addresses)
    print(f'--- did {expected_address} reach the final context? '
          f'{expected_address in addresses} ---')
    print(f'--- graph_ms: {result.latency_json[\"graph_ms\"]} ---')
    print()
"
```

For **`q011`**, confirm `stages_json["graph"]` shows `module.vpc` with
`"relationship": "dependency"`, the correct `origin_address`/`ref_text`, and
either `"score_status": "promoted"` (with its own real score, if it was already
present in the reranked pool — the expected case per section 1) or
`"unscored"` (if retrieval had dropped it entirely) — either is valid evidence
of a fix, but the two mean different things and should be reported accurately,
not conflated.

For **`q016`**, confirm `stages_json["graph"]` shows
`output.vpc_endpoints_security_group_arn` with:
- `"relationship": "dependent"`;
- `"origin_address": "module.vpc_endpoints"`;
- the correct real `ref_text`;
- `"score_status": "promoted"` (it was already present at rank 4, not absent —
  a fresh `"unscored"` result here would indicate the real database's ranking
  no longer matches Day 12's accepted report, worth investigating before
  proceeding, not silently accepted);
- its **own original reranker score preserved**, not overwritten;
- and that it actually reaches `result.blocks` (the promotion moved it inside
  `final_k`).

**Step 2 — second explicit confirmation, before the full 40-question run**:
state plainly beforehand: ~40 paid OpenAI embedding requests (unchanged), zero
generation requests, additional local database read latency for graph
expansion (no new paid cost).

**Step 3 — run only the fifth configuration**:
```bash
.venv/bin/python scripts/run_eval.py --repo-id <resolved-repo-id> \
  --config "+ Graph expansion"
```

**Step 4 — inspect before accepting**:
- Compare aggregate Recall@5/Recall@10/MRR/`mean_latency_ms` against the Day 12
  table (section 1).
- Specifically re-check `relational` and `blast_radius` category breakdowns,
  and specifically re-check `q011`'s own recall values.
- Investigate anything surprising before accepting — if `lookup`/`attribute`
  category recall drops relative to Day 12, that's evidence graph
  additions/promotions are displacing good candidates out of `final_k` — worth
  explaining explicitly.

**Step 5 — accept and commit, or fix and re-run**: same deliberate
review-then-stage workflow as every prior day. Write `DAY_13_ANALYSIS.md`
alongside the accepted report: aggregate metrics, per-category metrics, `q011`'s
specific before/after, the `q016` blast-radius promotion check's outcome
(including whether `output.vpc_endpoints_security_group_arn`'s `score_status`
was `"promoted"` as expected), `graph_ms`, and an honest account of any
category that regressed. **Never hand-edit any measured metric.** Commit the
accepted report and analysis separately from the implementation commit(s).

## 10. Scope and process

- `SPEC.md` stays read-only.
- The existing accepted reports and `DAY_11_ANALYSIS.md`/`DAY_12_ANALYSIS.md` are
  not modified.
- **Not implemented this cycle**: query rewriting (Day 15), RRF tuning, Pinecone
  work. The cross-encoder itself is not retrained, retuned, or reconfigured —
  `rerank.py` is untouched.
- No credentials are ever exposed, printed, logged, or committed. No Hugging
  Face cache files are ever committed.
- `repo_id` is never hardcoded in application code.

## 11. Acceptance criteria

Day 13 is complete only when all of the following hold:
- Graph expansion works behind `use_graph`, with the exact promote/add/
  leave-alone semantics in section 5.4/5.7.
- Disabled (`use_graph=False`) behavior is provably unchanged: every
  pre-existing test in `test_pipeline.py`, `test_prompts.py`, and
  `test_runner.py` passes with only the audited `use_graph=False` additions.
- Depth stays exactly one, provably.
- A candidate ranked 11–50 in the reranked pool **can** be promoted into
  `final_k` when a seed's real dependency/dependent edge points at it — proven
  by a dedicated test (section 8), not just asserted in prose.
- An already-higher-ranked candidate (specifically: another seed) is never
  demoted, duplicated, or relabeled.
- `graph_max_added` counts promotions and additions on one shared counter,
  exactly, proven by a dedicated test.
- **No graph-added block's score is misattributed to the reranker**: a
  genuinely new block has `score=None`/`graph_score_status="unscored"`; a
  promoted block retains its own original score with
  `graph_score_status="promoted"`; `stages_json["graph"]` shows both explicitly.
- Both `graph.py` queries have a genuinely total deterministic order, proven by
  a dedicated duplicate-edge test.
- `graph_ms`, `stages_json["graph"]`, and `executed.graph` are all correct.
- `format_context` renders both relationship directions correctly and leaves
  ordinary-block formatting untouched.
- `.venv/bin/python -m pytest -q` is green, with the actual passed/skipped
  counts reported honestly for the environment it ran in (section 1) — not
  asserted as a single unconditional number.
- The fifth real ablation row exists in one committed, `schema_version: 2` JSON
  report.
- **Both** smoke checks (section 9, step 1 — `q011` relational and `q016`
  blast-radius) were actually run and their `stages_json["graph"]` output
  inspected before the full evaluation, using **one shared, prepared**
  `CrossEncoderReranker` instance for both questions.
- The result has been inspected (section 9, step 4), including an honest
  account of any category that regressed.
- The accepted report and its `DAY_13_ANALYSIS.md` are committed together, in a
  commit separate from the implementation commit(s).

## 12. Needs sign-off

**None.** The one item flagged in the prior draft — whether graph-discovered
blocks compete for slots inside `final_k` — is now explicitly approved
(section 5.1) and is no longer open. Every decision introduced or corrected by
this revision (the three-case promotion algorithm, promotions counting toward
`graph_max_added`, the `score`/`score_status` representation, the total
`ORDER BY`) was something this round's review explicitly instructed be chosen
and documented, not deferred back for a decision — each is resolved above with
its reasoning stated in full. If any of these specific choices don't match your
intent, say so before implementation; none of them is presented as the only
possible design, but all are presented as this plan's actual, decided answer.

## 13. Audit — re-verified against every item named for re-audit

- **Pipeline ordering**: unchanged and now confirmed, not just proposed —
  graph runs after rerank/fusion and before `final_k` (section 5.1).
- **Immutable seed selection**: `seeds`/`seed_ids` are captured once, before
  any promotion or insertion, and never mutated during the pass (section 5.2/
  5.7) — re-verified against the corrected algorithm's actual code this
  revision, not just asserted.
- **One-hop enforcement**: the seed loop iterates only the original
  `candidates`/`seeds` snapshot; no promoted or added block is ever eligible to
  seed further expansion (section 5.2/5.7); tested directly (section 8).
- **Deduplication**: `handled_ids` (first-discovery-wins, across seeds/
  directions) plus `seed_ids` exclusion (section 5.4) — re-derived from
  scratch this revision to replace the prior draft's single, insufficient
  `known_ids` check.
- **Promotion of existing low-ranked candidates**: the core fix this revision
  makes — a candidate at reranked rank 11–50 is now actually relocatable next
  to its seed, not just checked-and-skipped (section 5.4/5.7), with a dedicated
  test proving it and proving the `q011` scenario is achievable.
- **Truthful score provenance**: promoted blocks keep their own real score;
  new blocks are explicitly `None`/`"unscored"`; nothing inherits a seed's
  reranker score for a block the model never scored (section 5.5), with
  downstream serialization/type effects inspected and confirmed safe before
  choosing this design, not after.
- **Deterministic ordering**: both `graph.py` queries now order by
  `resource.address, edges.ref_text, resource.id, edges.id` — a genuine total
  order, `edges.id` added this revision after confirming `resource.id` alone
  still ties for edges connecting the same pair with identical `ref_text` —
  tested with manually-inserted duplicate edges covering **both** the
  different-`ref_text` and identical-`ref_text` cases, not assumed safe because
  the indexer "shouldn't" produce them (section 5.3/8).
- **Graph caps**: `graph_max_added` is a single shared counter across
  promotions and additions, an explicit, tested choice (section 5.2/5.4/8).
- **`q011` recovery logic**: directly re-checked against the corrected
  algorithm — `module.vpc` sitting at reranked rank 11–50 is now the case the
  promotion branch (case 3, section 5.4) exists to handle; the smoke test
  (section 9) reports whether the real data confirms it, and reports
  `promoted` vs. `unscored` honestly rather than assuming which applies.
- **Real dependent/blast-radius verification**: `q016` (corrected this
  revision from `q014`, which was structurally incompatible with the
  seed-exclusion rule — its two top candidates are both immutable seeds, so
  its expected dependent could never legitimately appear in
  `stages_json["graph"]`) is the required second smoke check (section 9),
  verifying a real `dependents`-direction **promotion** — `origin_address`,
  `ref_text`, `score_status == "promoted"`, and its preserved original score —
  for a candidate confirmed (against the accepted Day 12 report) to sit at
  rank 4, outside `graph_seed_n=3` and therefore eligible for promotion.

## 14. Summary

1. **Files to create**: none.
2. **Files to modify**: `ripple/retrieval/graph.py`, `ripple/retrieval/
   vector_store.py`, `ripple/retrieval/pipeline.py`, `ripple/llm/prompts.py`,
   `ripple/evaluation/runner.py`, `tests/test_graph.py`, `tests/test_pipeline.py`,
   `tests/test_prompts.py`, `tests/test_runner.py` — 9 files total, unchanged in
   count from the prior draft.
3. **Tests to add/update**: the same audit-driven `use_graph=False` pass over
   all 21 `test_pipeline.py` sites, plus promotion, non-demotion, score-status
   (both cases), and shared-cap tests as before, now joined by: the BM25-only
   integration test corrected to set `bm25_k=1` explicitly and assert the
   single base result before checking for genuinely-new (`"unscored"`) graph
   additions (finding 3); and `test_graph.py`'s determinism test extended to
   cover **both** duplicate-edges-with-different-`ref_text` and
   duplicate-edges-with-identical-`ref_text` (the latter needing `edges.id` as
   the actual tiebreaker, finding 2). Direction/safety tests in
   `test_prompts.py` unchanged; one updated assertion in `test_runner.py`.
4. **Paid/local compute expected, corrected this revision**: ~40 OpenAI
   embedding requests for the full evaluation run (unchanged), plus 2 for the
   two required smoke checks (`q011`, `q016`); **0 generation requests; one
   reranker preparation (not two) and two local batched rerank predictions**,
   since the smoke script now shares one prepared `CrossEncoderReranker`
   instance across both questions (finding 4) instead of constructing/loading
   the real model separately for each; additional local database read latency
   for graph expansion (no new paid cost, no model, no download beyond the one
   reranker preparation already required for the evaluated configuration).
5. **Remaining ambiguity requiring your decision**: none. `final_k` competition
   remains approved and closed; the `q014` → `q016` substitution, the
   `edges.id` tiebreaker, the deterministic `bm25_k=1` integration test, and
   the shared-reranker smoke script are all corrections to match the plan's
   own already-approved design, not new open design choices.
