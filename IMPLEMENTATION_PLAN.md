# Implementation Plan — Day 13: Graph Expansion

## 0. Process note for this cycle

**`SPEC.md` is read-only.** Nothing below proposes editing it; any place SPEC's
text is genuinely ambiguous is resolved here with explicit reasoning and flagged
in section 12 for your review, never silently guessed past.

**Only this file is modified in this planning cycle.** No application code, tests,
or other files change until a step from section 6 is implemented.

This plan replaces the Day 12 plan, which is done (section 1 is a short
completed-baseline summary). It covers **Day 13 only** — graph expansion — per
SPEC section 11's own day boundary ("Day 13 — Graph expansion... Done when: row
five exists"). Day 15 (query rewriting), Pinecone, and RRF tuning stay out of
scope.

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
  miss** under reranking — `module.vpc` moved from rank 8 to outside the top 10.
  `module.vpc` is a **direct dependency** of the DynamoDB endpoint policy block,
  which is exactly the edge `graph.dependencies()` exists to surface. This plan's
  smoke test (section 9) uses this question specifically.
- Full suite: **213 tests passing** (194 Days 1–11 baseline + 19 Day 12), verified
  fresh this cycle. This is Day 13's baseline.

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
  5.5 below resolves the exact direction-to-label mapping this example only
  partially specifies (it shows one direction; this plan needs both).
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
  the per-category breakdown shows where it helped."`
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
resource itself **depends on**. Both queries already `ORDER BY resource.address` —
deterministic. Neither function is called from `pipeline.py` today.

**`ripple/retrieval/vector_store.py`** — `RetrievedBlock` has `id, address,
file_path, start_line, end_line, body, embed_text, score` — all required, no
graph-relationship fields.

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
need no structural change for a fifth row (section 5.6 explains why graph needs
no new report-schema field, unlike reranking's `reranker_json`).

**`scripts/run_eval.py`** — confirmed still fully generic over `ABLATION_CONFIGS`'
length (unchanged since Day 12). **No changes needed.**

### 4.1 The `use_graph` default-to-`True` problem — audited across every existing `RetrievalConfig(...)` in `tests/test_pipeline.py`

This is the exact same class of defect Day 12's review caught for `use_rerank`,
now present for `use_graph`, discovered by reading the file fresh rather than
assuming Day 12's fixes already covered it: **`RetrievalConfig.use_graph`
defaults to `True`, and every single `RetrievalConfig(...)` construction in
`tests/test_pipeline.py` today — all 21 of them, including the ones Day 12 fixed
for `use_rerank` — sets `use_bm25`/`use_rerank`/etc. explicitly but **never sets
`use_graph`**. Once graph expansion is wired in, every one of these tests would
start executing the new graph stage, and — for the majority of them, whose
candidate lists are non-empty — would attempt a **real, unmocked
`graph.dependents`/`dependencies` call against whatever database
`ripple.db.get_connection()` resolves to** in that test process. This must not
happen in a pure unit-test file.

| Test | Line | Candidates non-empty at graph stage? | Verdict |
|---|---|---|---|
| `test_run_pipeline_vector_only` | 130 | yes | **must add `use_graph=False`** |
| `test_run_pipeline_bm25_only_without_openai_key` | 160 | yes | **must add `use_graph=False`** |
| `test_run_pipeline_fuses_vector_and_bm25_results` | 191 | yes | **must add `use_graph=False`** |
| `test_run_pipeline_concatenates_when_rrf_is_disabled` | 234 | yes | **must add `use_graph=False`** |
| `test_run_pipeline_with_both_retrievers_disabled` | 257 | empty | **must add `use_graph=False`** (exact-key-set assertions) |
| `test_config_json_separates_requested_and_executed_stages` | 273 | empty | **must add `use_graph=False`** — this test already asserts `executed["graph"] is False`; once `executed.graph` becomes config-driven (section 5.4), that assertion is only still true if the config actually disables it |
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

**Net result: all 21 existing sites get `use_graph=False` added — zero exceptions,
since none of them is a graph-specific test.** The new graph tests (section 8)
are the only places `use_graph=True` appears in this file, and every one of
those injects fake `dependents`/`dependencies` functions (section 5.7).

### 4.2 `tests/test_runner.py`'s existing assertion that will need updating

`test_ablation_configs_are_explicit_and_support_recall_at_10` currently asserts
`config.use_graph is False` **unconditionally, for every row** — this must
become conditional on row index once the fifth row sets `use_graph=True`
(section 5.6/8).

## 5. Design decisions

### 5.1 Pipeline ordering — resolved decisively (flagged for your review, section 12)

**Graph expansion runs after reranking (or after fusion/retrieval, if reranking
is disabled) and *before* `final_k` truncation — graph-discovered blocks
therefore compete for slots inside `final_k`, they are not appended after it.**
This is the single most consequential interpretive call in this plan, because
SPEC 9.8 doesn't say which of the two options finding 1 named is correct, and
the two options produce **measurably different evaluation results**:

- **Rejected alternative — "`final_k` base results plus graph additions appended
  after"**: if graph-discovered blocks were appended *after* an already-`final_k`
  -truncated (10-item, for evaluation) base list, they would occupy positions 11+
  in `result.blocks`. SPEC's own `recall_at_k` formula is `set(retrieved[:k])` —
  a block at position 11 or later **can never appear in `retrieved[:10]`**, let
  alone `retrieved[:5]`. Under this alternative, graph expansion could still
  change the rendered *prompt context* (more blocks shown to the LLM) but would
  be **structurally incapable of ever changing a single Recall@5 or Recall@10
  number**, for any question, ever — which would make the fifth ablation row's
  entire evaluative purpose moot, contradicting SPEC's own Day 13 "Done when: ...
  the per-category breakdown shows where it helped" and the risk register's
  explicit expectation that graph expansion *can* show a gain.
- **Chosen design**: after the rerank stage (or fusion/retrieval if reranking is
  off) produces its ranked `candidates` list, graph expansion seeds from the
  first `graph_seed_n` entries of *that* list (SPEC's literal "top N results...
  after reranking") and **inserts** each seed's newly-discovered, deduplicated
  neighbors immediately after that seed's own position in the list (section
  5.4's exact algorithm). `final_k` truncation is then applied, unchanged in its
  own logic, to this graph-augmented list — exactly where it already runs today,
  just one stage later. Because insertions happen right next to an already
  high-ranked seed (rank ≤ `graph_seed_n`, i.e. ≤ 3 by default), a graph addition
  has a real, by-construction chance of surviving into the top 5 or top 10,
  which is what makes it possible for graph expansion to move a Recall@5/
  Recall@10 number at all.
- **Consequence, stated plainly**: this is a genuine trade, not a free addition —
  inserting a graph-discovered block ahead of a lower-ranked retrieval candidate
  can push that candidate below `final_k` and out of the result entirely. SPEC's
  own ablation methodology exists precisely to measure trades like this one; the
  per-category breakdown (section 9) is where you see whether the trade was
  worth it for `blast_radius`/`relational` at the cost of anything else.
- **Context construction**: `result.blocks` is still exactly the list sent to
  `format_context` — capped at `final_k` (10 for evaluation), same as every
  prior day. Graph expansion never causes more than `final_k` blocks to reach
  the prompt.

### 5.2 Seeds and limits

- **Seeds**: the first `graph_seed_n` entries of the candidate list at the point
  graph expansion runs (post-rerank, pre-`final_k`), in that list's existing
  rank order — a strict prefix, not a re-sort.
- **`graph_seed_n <= 0`**: no seeds, no expansion. Guarded the same way as every
  other non-positive-limit case in this pipeline (`final_k`, `vector_k`,
  `bm25_k`, `rerank_top_n`): `seed_n = max(config.graph_seed_n, 0)`, and
  `candidates[:0] == []` — no negative-slice reinterpretation risk.
- **`graph_max_added <= 0`**: no additions, regardless of how many seeds or
  neighbors exist. `max_added = max(config.graph_max_added, 0)`; the insertion
  loop's `len(graph_additions) < max_added` guard is `False` from the start.
- **Depth is exactly one, structurally — not just by convention**: the seed loop
  iterates over the **original, unmodified** pre-graph `candidates` list; newly
  discovered neighbor blocks are appended only to a separate `augmented` output
  list and are **never** iterated over as new seeds. There is no recursive call,
  no loop over `graph_additions`, and no code path that could reach a
  depth-two neighbor. Section 8's dedicated test proves this by asserting
  `dependents`/`dependencies` are called **only** with the original seeds'
  `id`s, never with any newly-discovered neighbor's `id`.
- **`graph_max_added` is a global cap across all seeds and both directions
  combined**, not a per-seed or per-direction budget — checked before starting
  each direction's neighbor loop and again before adding each individual
  neighbor, so the cap is exact, never overshot.

### 5.3 Both edge directions, and a deterministic order

For each seed, **both** `graph.dependents(seed.id)` and
`graph.dependencies(seed.id)` are checked — **dependents first, then
dependencies**, matching the literal order SPEC 9.8's own SQL comments are
written in. Seeds are processed in the candidate list's existing rank order
(deterministic, since reranking's own tie-break is already deterministic).
Within one direction, `graph.py`'s existing `ORDER BY resource.address` makes
neighbor order deterministic too. **No new randomness is introduced anywhere in
this stage** — the entire expansion is a pure function of `candidates`'
(already-deterministic) order plus the database's current content.

### 5.4 Deduplication — exact rule, resolved

- **Against all base retrieval results**: before expansion starts, `known_ids =
  {block.id for block in candidates}` — the *complete* pre-graph candidate list,
  not just the seeds and not just what will survive `final_k`. Any neighbor
  whose `id` is already in `known_ids` is skipped.
- **Against neighbors reached through multiple seeds, directions, or edges**: a
  single `added_ids: set[int]` accumulates across the *entire* expansion stage
  (all seeds, both directions) — once a neighbor is added, it is never added
  again, regardless of how many other seeds or directions would also have
  discovered it.
- **Multiple relationship explanations are not preserved — the first discovery
  wins, explicitly, not by accident.** If the same block would be reachable as
  both a dependent of seed A and a dependency of seed B (or via both directions
  of the same seed, for a bidirectional reference), only whichever occurrence is
  reached first in the deterministic traversal order (seed rank order, then
  dependents-before-dependencies, section 5.3) is added, carrying *that*
  occurrence's relationship/origin/`ref_text`. This is a simple, fully
  deterministic rule, chosen over merging multiple explanations onto one block
  because SPEC 9.8 describes each added block as carrying "their relationship
  and the block they came from" (singular), not a list of relationships.
- **Self-loops** are already impossible by construction — Day 4's reference
  extraction explicitly excludes `source_id == target_id` edges (SPEC 9.2), so
  neither `dependents` nor `dependencies` can ever return the seed itself.
- Tested deterministically (section 8): running the same expansion twice against
  the same fake/real data produces byte-identical output, and a neighbor
  reachable via two different paths appears exactly once, with the
  first-discovered relationship.

### 5.5 Graph provenance — `RetrievedBlock`, not a separate type

**Three new trailing, defaulted fields on `RetrievedBlock`** — not a separate
result/provenance type, and not an overload of `score`:

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
    score: float
    graph_relationship: str | None = None   # "dependent" | "dependency" | None
    graph_origin_address: str | None = None # the seed address this came from
    graph_ref_text: str | None = None       # the literal reference text
```

**Why a separate type was rejected**: `pipeline.py`'s candidate list, `fusion.py`,
`format_context`, and every existing test fixture all operate on
`list[RetrievedBlock]` uniformly. Introducing a second type (e.g. a
`GraphAddition` wrapper) would force `format_context`, `answer_question`, and
every consumer of `result.blocks` to branch on type — exactly the kind of
special-casing this project's `VectorStore`/`Reranker` interfaces have
consistently avoided. Three **optional** fields, defaulted to `None`, cost
nothing: every existing `RetrievedBlock(...)` construction across the entire
repository (production and all 8 test files touched by Day 12's `embed_text`
change) continues to work completely unmodified — **zero fixture changes
required anywhere except the new graph-specific tests themselves**, a
meaningfully smaller blast radius than Day 12's `embed_text` change, precisely
*because* these three fields are genuinely optional (a block either came from
graph expansion or it didn't) where `embed_text` was not (every block has real
text, always).

**Why `score` is never overloaded**: a graph-added block was never independently
scored by any retrieval or reranking process — assigning it a fabricated score
computed to *mean* something about its graph relationship would be exactly the
"silently overload the reranker score with graph meaning" this plan must avoid.
Instead, **a graph-added block inherits its originating seed's own score
unchanged** — a simple, honest placeholder ("as relevant as the seed that
produced it," not an independently computed rank), while the *actual* graph
semantics live entirely in the three new dedicated fields above, never in
`score`. This also means graph additions sort/display sensibly if anything ever
re-sorts by `.score` (e.g. debugging tooling), without any risk of `score` being
misread as an "expansion confidence" number it was never designed to be.

**Direction → prompt-label mapping, derived from the two example strings the
Day 13 request itself supplies** (SPEC 9.10's own example shows only one
direction, so the request's two examples — `"Referenced by: aws_instance.node"`
and `"Depends on: module.vpc"` — are what pin down the second):
- A block found via **`dependents(seed)`** (the block's body references the
  seed — the block *depends on* the seed) → `graph_relationship = "dependent"`
  → rendered as **`"Depends on: {seed_address}"`**.
- A block found via **`dependencies(seed)`** (the seed's body references the
  block — the block *is referenced by* the seed) → `graph_relationship =
  "dependency"` → rendered as **`"Referenced by: {seed_address}"`**.

`ref_text` is retained on the block (and in `stages_json`, section 5.7) for
auditability but is **not** rendered in the prompt line itself — SPEC 9.10's
example line doesn't show it either (`"Referenced by: aws_instance.node"`, no
literal reference text appended), and the block's own unmodified `body` already
contains the real reference text where it actually occurs in the source.

### 5.6 `GraphNeighbor` gets a real `embed_text`, deliberately — not a placeholder

**Resolved, not left as a placeholder**: `GraphNeighbor` gains one new field,
`embed_text: str` (required, no default — this dataclass is internal-only,
constructed in exactly two places in `graph.py` itself), populated from a real
`resources.embed_text` column read, exactly the way `BM25Document.embed_text`
was resolved on Day 12. `graph.py`'s two `SELECT` statements add
`resource.embed_text` to their column lists (positioned right after `body`, so
`GraphNeighbor(*row)`'s existing positional construction keeps working
unchanged once the dataclass's field order matches: `id, address, file_path,
start_line, end_line, body, embed_text, ref_text` — `ref_text` moves to last
position to match). **Why not default it to `body`, as a cheaper shortcut**: a
graph-added block's `embed_text` is never actually consumed by any embedding or
reranking computation in this pipeline (graph expansion runs *after* reranking,
and added blocks are never re-embedded or re-scored) — so reusing `body` here
would be *inert* today, unlike Day 12's problem where a missing `embed_text`
silently corrupted a value the reranker actually read. Even so, this plan uses
the real database value rather than a stand-in, for one concrete reason: it
keeps the invariant "`RetrievedBlock.embed_text` is always a genuine embed_text
value, never a body substitute" true **everywhere**, with no carved-out
exception a future change could trip over if graph-added blocks are ever fed
into an embedding-or-reranking step later (e.g., a future re-ranking pass over
the *expanded* context). `GraphNeighbor` does **not** gain a `score` field —
`score` is a `RetrievedBlock`-only concept; the caller (`pipeline.py`) supplies
it when converting a `GraphNeighbor` into a `RetrievedBlock` (section 5.5).

### 5.7 Pipeline integration — the exact algorithm

`pipeline.py` imports `dependents`/`dependencies` directly from
`ripple.retrieval.graph`, matching the existing `build_index` import-and-
monkeypatch convention exactly — tests patch `pipeline.dependents`/
`pipeline.dependencies`, the same pattern already used for `pipeline.
build_index`. No new injectable class is introduced for graph (unlike the
reranker's `CrossEncoderReranker`): `graph.dependents`/`dependencies` are cheap,
stateless, per-call database reads with no model to load and no reuse-across-
questions cost to amortize — module-level monkeypatching is sufficient and
consistent with `build_index`'s own already-accepted, documented
rebuild-per-call precedent (Day 12's cost/runtime section).

Inserted between the existing rerank block and the existing `final_k`
truncation — **`final_k`'s own line of code moves down one block, but its logic
is completely unchanged**:

```python
if config.use_graph:
    graph_start = time.perf_counter()
    seed_n = max(config.graph_seed_n, 0)
    max_added = max(config.graph_max_added, 0)
    known_ids = {block.id for block in candidates}
    added_ids: set[int] = set()
    augmented: list[RetrievedBlock] = []
    graph_additions: list[RetrievedBlock] = []

    for position, block in enumerate(candidates):
        augmented.append(block)
        if position >= seed_n or len(graph_additions) >= max_added:
            continue

        for relationship, fetch in (
            ("dependent", dependents),
            ("dependency", dependencies),
        ):
            if len(graph_additions) >= max_added:
                break
            for neighbor in fetch(block.id):
                if len(graph_additions) >= max_added:
                    break
                if neighbor.id in known_ids or neighbor.id in added_ids:
                    continue
                added_ids.add(neighbor.id)
                new_block = RetrievedBlock(
                    id=neighbor.id,
                    address=neighbor.address,
                    file_path=neighbor.file_path,
                    start_line=neighbor.start_line,
                    end_line=neighbor.end_line,
                    body=neighbor.body,
                    embed_text=neighbor.embed_text,
                    score=block.score,
                    graph_relationship=relationship,
                    graph_origin_address=block.address,
                    graph_ref_text=neighbor.ref_text,
                )
                augmented.append(new_block)
                graph_additions.append(new_block)

    candidates = augmented
    latency_json["graph_ms"] = (time.perf_counter() - graph_start) * 1000
    stages_json["graph"] = _serialize_graph(graph_additions)

if config.final_k > 0:
    blocks = candidates[: config.final_k]
else:
    blocks = []
```

`_serialize_graph` (new, alongside the existing `_serialize`) emits the richer
shape finding 5/7 requires — every field needed to audit *why* a block was
added, not just its id/address/score:

```python
def _serialize_graph(blocks: list[RetrievedBlock]) -> list[dict]:
    return [
        {
            "id": block.id,
            "address": block.address,
            "score": block.score,
            "relationship": block.graph_relationship,
            "origin_address": block.graph_origin_address,
            "ref_text": block.graph_ref_text,
        }
        for block in blocks
    ]
```

`stages_json["graph"]` holds **only the newly-added blocks** (not the whole
augmented list) — parallel to how "fusion" holds the fused candidates and
"final" separately holds what actually survived truncation; "graph" is
specifically an audit trail of *this stage's own contribution*.

**`_build_config_json`'s `"executed"` dict**: `"graph": False` (hardcoded)
becomes `"graph": config.use_graph` — the same config-driven pattern already
established for vector/bm25/fusion/rerank. An enabled graph stage with zero
seeds or zero discovered neighbors still shows `executed.graph: true` and
`stages_json["graph"]: []` — consistent with how `use_rerank=True` with an
empty candidate pool already behaves (Day 12).

**When `use_graph=False`** (all four pre-existing
`ABLATION_CONFIGS` rows, section 4.2): the new block is skipped entirely — no
`dependents`/`dependencies` call, no `graph_ms` key, no `stages_json["graph"]`
key, and **`final_k` truncation runs against the exact same `candidates` it
always has** (the graph block is a complete no-op when disabled, so moving
`final_k`'s code position doesn't change its *behavior* for any config that
disables graph — which, after section 4.1's fixes, is every existing test).

**`run_pipeline`'s signature is unchanged** — no new parameter. Graph expansion
needs no per-call injection point for production use (unlike the reranker),
since `dependents`/`dependencies` have no state or cost worth sharing across
questions; tests inject fakes via monkeypatching the module-level names instead
(section 8).

### 5.8 Prompt integration

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

**Existing formatting for ordinary (non-graph) blocks is byte-for-byte
unchanged**: when `graph_relationship is None` (true for every block
`format_context` has ever been called with until this cycle), the new
conditional line is skipped entirely and the output is identical to today's —
verified by section 8's requirement that every existing `test_prompts.py`
assertion keeps passing unmodified.

### 5.9 No new report-schema field is needed for graph (unlike reranking)

Reranking needed `reranker_json` because it introduced an **external model**
with its own version/revision that `RetrievalConfig` alone couldn't describe.
Graph expansion introduces no external resource: it's SQL against the same
already-indexed, already-fingerprinted corpus (`indexed_corpus_sha256`,
established Day 10). `dataclasses.asdict(result.config)` already includes
`use_graph`/`graph_seed_n`/`graph_max_added` for every row. **No new
`ConfigResult` field, no `schema_version` bump** — the report stays
`schema_version: 2`.

## 6. Exact file scope

**Create:** none.

**Modify:**
- `ripple/retrieval/graph.py` — add `GraphNeighbor.embed_text: str` (required);
  add `resource.embed_text` to both `SELECT` statements, positioned so
  `GraphNeighbor(*row)`'s existing positional construction still matches field
  order (section 5.6).
- `ripple/retrieval/vector_store.py` — add three trailing, defaulted
  `RetrievedBlock` fields: `graph_relationship`, `graph_origin_address`,
  `graph_ref_text` (section 5.5).
- `ripple/retrieval/pipeline.py` — new graph stage inserted between rerank and
  `final_k` truncation; `_serialize_graph` helper; `executed.graph` becomes
  `config.use_graph`; import `dependents`/`dependencies` from
  `ripple.retrieval.graph` (section 5.7).
- `ripple/llm/prompts.py` — `format_context` renders the relationship line for
  graph-sourced blocks only (section 5.8).
- `ripple/evaluation/runner.py` — add the fifth `ABLATION_CONFIGS` row (section
  5.9's "no other runner.py change needed" — confirmed, not assumed, by reading
  `run_benchmark`/`build_report` this cycle, section 4).
- `tests/test_graph.py` — add an assertion that `embed_text` round-trips through
  `dependents`/`dependencies` against the real reference fixture, distinct from
  `body` (section 8).
- `tests/test_pipeline.py` — **audit-driven changes only**: add `use_graph=False`
  to all 21 existing sites (section 4.1's table); add the new graph-expansion
  tests (section 8).
- `tests/test_prompts.py` — add `graph_relationship`/`graph_origin_address`
  parameters to the `_block` helper (defaulted to `None`, so all existing calls
  are unaffected); add new tests for both relationship directions and for
  repository-content safety (section 8).
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
# (ORDER BY resource.address unchanged)
```

## 8. Tests — exact files and assertions

**`tests/test_pipeline.py`**:
- All 21 sites from section 4.1's table get `use_graph=False` — **no behavioral
  change to any existing assertion**, only the `RetrievalConfig(...)`
  construction.
- New fakes, matching the file's existing style: `_FakeGraphNeighbor` factory
  (or reuse `graph.GraphNeighbor` directly, since it's a plain dataclass) and
  `_install_graph(monkeypatch, dependents_by_id, dependencies_by_id)` installing
  `pipeline.dependents`/`pipeline.dependencies` as lookups into two dicts,
  recording every call made.
- Disabled toggle never calls the graph functions: inject fake `dependents`/
  `dependencies` that raise if called at all; run with `use_graph=False` and
  non-empty candidates; assert no error.
- Seeds are exactly the first `graph_seed_n` candidates: 5 fake candidates,
  `graph_seed_n=2`; assert `dependents`/`dependencies` were called with only the
  first two candidates' `id`s, never the third, fourth, or fifth.
- Both directions checked, dependents before dependencies: one seed, fake
  `dependents` returning one neighbor and fake `dependencies` returning a
  different neighbor; assert both appear in `stages_json["graph"]` with the
  correct `relationship` value each, and that `dependents` was recorded as
  called before `dependencies` for that seed.
- **Depth stays exactly one**: assert `dependents`/`dependencies` are called
  **only** with the original seed `id`s — a newly-discovered neighbor's `id`
  must never appear as an argument to either fake, even when the fake would
  return further neighbors if it were (checked by having the fakes assert-fail
  if invoked with an id outside the known seed set).
- Deduplication against base results: a fake neighbor whose `id` matches an
  existing (non-seed) candidate is not added; assert it's absent from
  `stages_json["graph"]`.
- Deduplication across seeds/directions: two different seeds' fake neighbor
  lookups both return the same neighbor `id`; assert it appears exactly once,
  carrying the **first** seed's relationship/origin (not the second's) —
  proving "first discovery wins," not a merge.
- Global cap: `graph_max_added=2`, three seeds each with two available fake
  neighbors; assert exactly 2 blocks appear in `stages_json["graph"]`, not 6.
- `graph_seed_n <= 0` and `graph_max_added <= 0`, parametrized `[0, -1]` each:
  no expansion, `stages_json["graph"] == []`, fake `dependents`/`dependencies`
  never called.
- Empty candidate list with `use_graph=True`: `stages_json["graph"] == []`,
  `executed.graph is True`, `graph_ms` present, no fake call attempted (nothing
  to seed from).
- **Graph competes inside `final_k` (section 5.1's resolved design)**: a
  constructed scenario where a fake neighbor, inserted next to a top-ranked
  seed, pushes a real, lower-ranked candidate below the `final_k` cutoff; assert
  the neighbor **is** present in `result.blocks` and the pushed-out candidate is
  **not** — the direct test proving graph additions can actually change what
  `recall_at_k` sees, not just what's in the prompt.
- Score inheritance: assert an added block's `.score` equals its originating
  seed's `.score` exactly (section 5.5).
- Provenance fields: assert `graph_relationship`/`graph_origin_address`/
  `graph_ref_text` are set correctly on every added block, and are `None` on
  every non-graph block (including the seeds themselves).
- `stages_json["graph"]`/`graph_ms`/`executed.graph` present only when
  `use_graph=True`.
- **Supabase integration test** (new, DB-dependent, skip-if-unreachable, same
  convention as `test_graph.py`/`test_bm25.py`): index the existing
  `reference_repo` fixture for real; run `pipeline.run_pipeline` with
  `use_vector=False, use_bm25=True, use_rerank=False, use_graph=True`, a
  question that puts `aws_vpc.main` at BM25 rank 1 (e.g. the address itself);
  assert the **real** `graph.dependents` result (`aws_security_group.worker`,
  `aws_subnet.public` — both already proven to reference `aws_vpc.main` in
  `test_graph.py`) appears in `stages_json["graph"]` with `relationship ==
  "dependent"`, `origin_address == "aws_vpc.main"`, and the correct real
  `ref_text`. This exercises the real database and real `graph.py`, and makes
  **no** OpenAI call (`use_vector=False`) and no real reranker call
  (`use_rerank=False`).

**`tests/test_prompts.py`**:
- `_block` helper gains `graph_relationship: str | None = None`,
  `graph_origin_address: str | None = None` parameters, passed through to
  `RetrievedBlock`. Every existing call site is unaffected by the new defaults.
- **Existing tests unmodified**: `test_format_context_preserves_order_and_
  citation_shape` continues to pass exactly as written (no relationship line for
  ordinary blocks).
- New: a block with `graph_relationship="dependent"` renders `"Depends on:
  {graph_origin_address}"` on its own line, in the position between the
  file/line citation and the body.
- New: a block with `graph_relationship="dependency"` renders `"Referenced by:
  {graph_origin_address}"`.
- New: a mixed list (some ordinary, some graph-sourced) renders the relationship
  line **only** on the graph-sourced entries, at their correct index.
- New: repository-content safety — a graph-added block whose `body` or
  `graph_origin_address` contains text resembling an instruction (mirroring
  this project's existing prompt-injection posture, hard constraint 6) is
  rendered as inert data, not specially interpreted; `format_context` performs
  no interpretation of block content at all, so this is a straightforward
  regression test that such content passes through unmodified as plain text
  inside the rendered section, never executed or treated as a directive by the
  formatter itself.

**`tests/test_graph.py`**: add an assertion (to an existing or new test using
the already-indexed `resource_ids` fixture) that `neighbors[0].embed_text`
equals the real `embed_text` column value for that resource, fetched
independently in the test (e.g. via `db.fetch_resource_bodies` plus a direct
`embed_text` column read, or a small ad hoc query) — and that it differs from
`body`, making this a real, discriminating check.

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

**Full suite** (this command never downloads/constructs the real reranker
model, and every new graph test either injects fakes or is a DB-only,
skip-if-unreachable integration test — no OpenAI call, paid or otherwise, is
introduced by any test in this plan):
```bash
.venv/bin/python -m pytest -q
```
Expected: **213 (Days 1–12 baseline) + every new Day 13 test above, all
passing, zero regressions.**

## 9. Real Day 13 evaluation

**Step 0 — first explicit confirmation, before any spending of any kind**: get
explicit go-ahead before Step 1 — this is a manual, human-run procedure. The
smoke test below makes one real OpenAI embedding request; nothing runs before
confirmation.

**Step 1 — relational smoke test, using `q011` (section 1's named Day 12
regression)**:
```bash
.venv/bin/python -c "
from ripple.evaluation.runner import ABLATION_CONFIGS
from ripple.retrieval import pipeline

REPO_ID = ...  # never hardcoded -- resolve independently, e.g.:
                # from ripple import db
                # with db.get_connection() as conn, conn.cursor() as cur:
                #     cur.execute(
                #         \"SELECT id FROM repos WHERE name = 'vpc-complete' \"
                #         \"ORDER BY id DESC LIMIT 1\"
                #     )
                #     print(cur.fetchone())
QUESTION = 'What block does the DynamoDB endpoint policy directly depend on for its VPC ID?'  # q011
EXPECTED_ADDRESS = 'module.vpc'

config = dict(ABLATION_CONFIGS)['+ Graph expansion']
result = pipeline.run_pipeline(REPO_ID, QUESTION, config)

print('--- graph additions ---')
for row in result.stages_json['graph']:
    print(row)

print('--- final addresses ---')
print([b.address for b in result.blocks])

found = EXPECTED_ADDRESS in [b.address for b in result.blocks]
print(f'--- did {EXPECTED_ADDRESS} reach the final context? {found} ---')

print('--- graph_ms ---')
print(result.latency_json['graph_ms'])
"
```
Confirm the printed `stages_json["graph"]` rows show `module.vpc` with
`"relationship": "dependency"` and the correct `origin_address`/`ref_text`
before treating this as evidence of a fix — this is the direct, real-database
proof that a graph neighbor entered the final context with provenance, per
SPEC's own Day 13 "Done when" criterion.

**Step 2 — second explicit confirmation, before the full 40-question run**:
state plainly beforehand: ~40 paid OpenAI embedding requests (unchanged), zero
generation requests, and additional **local** database read latency for graph
expansion (no new paid cost — `graph.dependents`/`dependencies` are plain SQL
reads against the already-indexed corpus).

**Step 3 — run only the fifth configuration**:
```bash
.venv/bin/python scripts/run_eval.py --repo-id <resolved-repo-id> \
  --config "+ Graph expansion"
```
Produces one new timestamped, `schema_version: 2` JSON report containing one
`ConfigResult`.

**Step 4 — inspect before accepting**:
- Compare aggregate Recall@5/Recall@10/MRR/`mean_latency_ms` against the Day 12
  table (section 1).
- **Specifically re-check `relational` and `blast_radius` category breakdowns**
  against Day 12's row, and **specifically re-check `q011`** — did `module.vpc`
  return to the final context, and did `recall_at_10` for that question recover?
- Investigate anything surprising before accepting — a suspicious number is a
  bug to find, not a footnote. In particular: if `lookup`/`attribute` category
  recall *drops* relative to Day 12, that's evidence graph additions are
  displacing good candidates out of `final_k` (section 5.1's named trade) —
  worth explaining explicitly, not silently accepting.

**Step 5 — accept and commit, or fix and re-run**: same deliberate
review-then-stage workflow as every prior day. Write `DAY_13_ANALYSIS.md`
alongside the accepted report: aggregate metrics, per-category metrics, `q011`'s
specific before/after, `graph_ms`, and an honest account of any category that
regressed. **Never hand-edit any measured metric.** Commit the accepted report
and analysis **separately** from the implementation commit(s), matching every
prior day's convention.

## 10. Scope and process

- `SPEC.md` stays read-only.
- The existing accepted reports and `DAY_11_ANALYSIS.md`/`DAY_12_ANALYSIS.md` are
  not modified.
- **Not implemented this cycle**: query rewriting (Day 15), RRF tuning, Pinecone
  work. The cross-encoder itself is not retrained, retuned, or reconfigured —
  `rerank.py` is untouched (section 6).
- No credentials are ever exposed, printed, logged, or committed. No Hugging
  Face cache files are ever committed — unaffected by this cycle, since graph
  expansion touches no model.
- `repo_id` is never hardcoded in application code.

## 11. Acceptance criteria

Day 13 is complete only when all of the following hold:
- Graph expansion works behind `use_graph`, with the exact semantics in section
  5.1–5.8.
- Disabled (`use_graph=False`) behavior is provably unchanged: every
  pre-existing test in `test_pipeline.py`, `test_prompts.py`, and
  `test_runner.py` passes with only the audited `use_graph=False` additions —
  no assertion values changed except the one (`test_config_json_separates_
  requested_and_executed_stages`) whose expectation depends on the now-dynamic
  `executed.graph`.
- Depth stays exactly one, provably — the dedicated test in section 8 confirms
  no neighbor `id` is ever used as a seed.
- `graph_ms`, `stages_json["graph"]` (with relationship/origin/`ref_text` on
  every added block), and `executed.graph` are all correct and consistent with
  the design in section 5.7.
- `format_context` renders both relationship directions correctly and leaves
  ordinary-block formatting untouched.
- `.venv/bin/python -m pytest -q` is fully green: 213 baseline + every new Day
  13 test.
- The fifth real ablation row exists in one committed, `schema_version: 2` JSON
  report.
- The smoke test (section 9, step 1) was actually run and its `stages_json
  ["graph"]` output inspected before the full evaluation — `q011`'s recovery (or
  lack of it) is confirmed with real data, not assumed.
- The result has been inspected (section 9, step 4), including an honest account
  of any category that regressed as a result of graph additions displacing
  other candidates.
- The accepted report and its `DAY_13_ANALYSIS.md` are committed together, in a
  commit separate from the implementation commit(s).

## 12. Needs sign-off

**One item, genuinely open, and the most consequential design decision in this
plan**: section 5.1's resolution that graph-discovered blocks are inserted
*before* `final_k` truncation and therefore compete for slots inside it, rather
than being appended after an already-`final_k`-truncated base result. This
plan's reasoning is that the rejected alternative would make graph expansion
structurally incapable of ever affecting a Recall@5/Recall@10 number, which
contradicts SPEC's own stated expectation that graph expansion can show a
measurable gain — but SPEC 9.8 does not state this ordering explicitly, so this
is this plan's interpretation, not a quoted requirement. **If you want the
alternative (graph additions shown in context but never scored), say so before
implementation begins** — it changes section 5.1, 5.7's algorithm, and several
of section 8's tests.

No other item requires sign-off: every other decision in section 5 was either
directly stated by SPEC 9.8/9.10 or resolved from the two example strings the
Day 13 request itself supplied (section 5.5's direction-to-label mapping), or
follows an established codebase convention (module-level monkeypatching for
stateless functions, optional trailing fields for provenance, real-value
resolution over placeholders per Day 12's `embed_text` precedent).

## 13. Audit — verified against the actual repository

- **`RetrievalConfig.use_graph` defaults to `True`, exactly like `use_rerank`
  did on Day 12** — every one of the 21 existing `RetrievalConfig(...)` sites in
  `tests/test_pipeline.py` audited individually (section 4.1); all 21 need
  `use_graph=False`, zero exceptions, because none is a graph-specific test.
- **`GraphNeighbor`/`RetrievedBlock` mismatch resolved deliberately, not with a
  placeholder** — `GraphNeighbor` gains a real, database-sourced `embed_text`
  (section 5.6); `RetrievedBlock` gains three optional provenance fields, never
  overloading `score` (section 5.5).
- **Depth-one enforced structurally**, not just documented — the algorithm has
  no code path that iterates over newly-added blocks as seeds (section 5.7),
  and section 8 has a dedicated test proving it.
- **`format_context`'s existing behavior for ordinary blocks is unchanged** —
  verified by requiring every current `test_prompts.py` assertion to keep
  passing unmodified (section 8).
- **`scripts/run_eval.py` needs no changes** — confirmed by re-reading it this
  cycle (section 4), not assumed from Day 12's finding that it was already
  generic.
- **No new report-schema field or `schema_version` bump** — confirmed by reading
  `build_report`'s current implementation this cycle (section 4/5.9); unlike
  reranking, graph expansion introduces no external, independently-versioned
  resource.
- **The one genuine ambiguity in this plan (final_k ordering) is named
  explicitly** in section 12, not silently resolved and hidden.

## 14. Summary

1. **Files to create**: none.
2. **Files to modify**: `ripple/retrieval/graph.py`, `ripple/retrieval/
   vector_store.py`, `ripple/retrieval/pipeline.py`, `ripple/llm/prompts.py`,
   `ripple/evaluation/runner.py`, `tests/test_graph.py`, `tests/test_pipeline.py`,
   `tests/test_prompts.py`, `tests/test_runner.py` — 9 files total.
3. **Tests to add/update**: a full audit-driven pass over all 21
   `RetrievalConfig(...)` sites in `test_pipeline.py` (no assertion changes) plus
   roughly a dozen new graph-specific tests there (including one real-database
   integration test); new direction/safety tests in `test_prompts.py`; one new
   assertion in `test_graph.py`; one updated assertion in `test_runner.py`.
4. **Paid/local compute expected**: ~40 OpenAI embedding requests for the full
   evaluation run (unchanged from every prior config), 0 generation requests,
   additional local database read latency for graph expansion (no new paid
   cost, no model, no download).
5. **Remaining decision needing sign-off**: whether graph-discovered blocks
   should compete for slots inside `final_k` (this plan's resolved design,
   section 5.1) or be appended after an already-truncated base result (which
   this plan argues would make graph expansion invisible to Recall@5/Recall@10
   entirely) — section 12.
