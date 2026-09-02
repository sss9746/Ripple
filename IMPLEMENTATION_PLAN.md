# Implementation Plan — Day 14: Full Table

## 0. Where this picks up

Days 1–13 are complete and accepted. Graph stabilization Sessions A–C and the
intent router are complete and committed (`7c5d054`, `7d2529b`, `484968c`,
`a570880`). Session C (`GRAPH_STABILIZATION_SESSION_C.md`,
`data/eval_results/2026-09-01T20-48-26-006545Z.json`) accepted the batched
and intent-routed graph policy:

| Configuration | Recall@5 | Recall@10 | MRR | graph_ms |
|---|---:|---:|---:|---:|
| Cross-encoder (Day 13, reused) | 0.8541666666666666 | 0.9 | 0.7456547619047619 | — |
| Original graph (Day 13, reused) | 0.8208333333333333 | 1.0 | 0.7889880952380952 | 8916.67 |
| Batched graph (new) | 0.8208333333333333 | 1.0 | 0.7889880952380952 | 609.64 |
| **Batched + routed graph (accepted)** | **0.9791666666666666** | **1.0** | **0.8183333333333334** | **307.19** |

`ABLATION_CONFIGS` row 5 still has `graph_route_by_intent` at its class
default (`False`) — never made the persisted default. `run_benchmark` has no
`embedder` parameter, no caching, and nothing validates the live corpus
against Session C before spending. This is the gap Day 14 closes.

**This is the fourth revision.** The third revision fixed the latency-bias
bug conceptually (pre-warming) and split Day-14-specific validation into a
dedicated wrapper, but its *pseudocode* had five remaining implementation
problems, all fixed here:
1. **A real circular import**: `execute_evaluation_run` (in `runner.py`)
   called `confirm_cost` (in `scripts/run_eval.py`), but `run_eval.py`
   already imports `runner.py` — `runner.py` cannot import back from
   `run_eval.py`. Fixed by removing all prompting from `runner.py` entirely
   (section 2).
2. **A silently undocumented latency change to the generic report**: once
   `run_eval.py` also pre-warms, its own reports need the same
   `embedding_precomputation`/`latency_methodology` honesty Day 14's report
   gets. Fixed by making these three fields part of `build_report`'s generic
   contract (section 4).
3. **A reranker-preparation ownership bug**: the previous pseudocode called
   `.prepare()` inside `execute_evaluation_run` *and* had `run_benchmark`
   check `prepare_ms` again — meaning by the time row 4 ran, the shared
   instance was already prepared, so row 4 would dishonestly report
   "reused." Fixed by making `run_benchmark` the **only** place `.prepare()`
   is ever called (section 5).
4. **A test-order-fragile separation check** (`sys.modules` membership).
   Replaced with a behavioral test (section 8).
5. **An incomplete vector-usage condition** (`config.use_vector` alone,
   ignoring `vector_k`). Fixed to `config.use_vector and config.vector_k >
   0` everywhere it's used (section 6).

## 1. Official Day 14 configuration

Unchanged from every prior revision — one field on row 5 only:

```python
(
    "+ Graph expansion",
    RetrievalConfig(
        use_vector=True, use_bm25=True, use_rrf=True, use_rerank=True,
        use_graph=True, use_rewrite=False, final_k=10,
        graph_route_by_intent=True,   # new — makes Session C's accepted policy explicit
    ),
),
```

`RetrievalConfig`'s class-level default for `graph_route_by_intent` stays
`False`; `ripple/config.py` is not modified; rows 1–4 are untouched.

## 2. `runner.py` performs no prompting — the circular-import fix

**The problem, precisely**: `scripts/run_eval.py` does (and must continue to
do) `from ripple.evaluation.runner import ...`. If `ripple/evaluation/
runner.py`'s `execute_evaluation_run` called `confirm_cost` — which lives in
`scripts/run_eval.py` — that would require `runner.py` to import from
`run_eval.py`, an import cycle. It also violates a real architectural
boundary: `runner.py` is a library module (already imported by both CLI
scripts and every test file), and terminal `input()` prompting has no
business inside it.

**Fix — `execute_evaluation_run` takes already-approved `entries`/`configs`
and does no prompting or confirmation of any kind**:

```python
# ripple/evaluation/runner.py
@dataclass
class EvaluationRun:
    results: list[ConfigResult]
    embedding_cache: dict
    embedding_precomputation: dict
    latency_methodology: dict


def execute_evaluation_run(
    repo_id: int,
    entries: list[BenchmarkEntry],
    configs: list[tuple[str, RetrievalConfig]],
) -> EvaluationRun:
    """Construct providers, pre-warm embeddings, run every selected
    configuration, and return the results plus generic execution metadata.
    Performs no prompting and no cost confirmation — callers confirm first."""
    uses_vector = any(
        config.use_vector and config.vector_k > 0
        for _name, config in configs
    )
    uses_rerank = any(config.use_rerank for _name, config in configs)
    unique_questions = sorted({entry.question for entry in entries})

    shared_embedder = None
    prewarm_total_ms = 0.0
    if uses_vector:
        shared_embedder = CachingEmbeddingProvider(OpenAIEmbeddingProvider())
        prewarm_start = time.perf_counter()
        for question in unique_questions:
            shared_embedder.embed([question])
        prewarm_total_ms = (time.perf_counter() - prewarm_start) * 1000
        print(f"Pre-warmed embedding cache: {len(unique_questions)} unique "
              f"questions in {prewarm_total_ms:.0f}ms "
              f"({prewarm_total_ms / max(len(unique_questions), 1):.1f}ms/question)")

    provider_calls_before_run = shared_embedder.request_count if shared_embedder else 0

    # section 5 — constructed here, but NOT prepared here; run_benchmark
    # owns the only call to .prepare() in the whole run.
    shared_reranker = CrossEncoderReranker() if uses_rerank else None

    results = [
        run_benchmark(
            repo_id=repo_id, entries=entries, config=config, config_name=config_name,
            embedder=shared_embedder, reranker=shared_reranker,
        )
        for config_name, config in configs
    ]

    provider_calls_after_run = shared_embedder.request_count if shared_embedder else 0

    return EvaluationRun(
        results=results,
        embedding_cache={
            "provider_calls": provider_calls_after_run,
            "cache_hits": shared_embedder.cache_hit_count if shared_embedder else 0,
            "unique_questions": len(unique_questions),
        },
        embedding_precomputation={
            "unique_questions": len(unique_questions),
            "provider_calls": provider_calls_before_run,
            "total_ms": prewarm_total_ms,
            "mean_ms_per_question": (
                prewarm_total_ms / len(unique_questions) if unique_questions else 0.0
            ),
        },
        latency_methodology={
            "description": (
                "The embedding cache was fully pre-warmed before any "
                "configuration ran. Every row's vector_query_ms/total_ms "
                "reflects only an in-memory cache lookup and the vector "
                "database query itself — never live OpenAI embedding "
                "network latency, for any row including the first. "
                "Embedding network latency is measured once, separately, "
                "as embedding_precomputation, and is excluded from every "
                "row's own reported latency."
            ),
            "provider_calls_before_run": provider_calls_before_run,
            "provider_calls_during_run": provider_calls_after_run - provider_calls_before_run,
            "valid": provider_calls_after_run == provider_calls_before_run,
        },
    )
```

**Required ordering — identical in both CLIs, stated once here, both
sections 3/7 follow it exactly**:
1. Free validation (benchmark/address checks; Day-14 wrapper additionally
   does Session C provenance + snapshot checks, section 7).
2. Calculate the cost estimate (pure Python — no provider constructed yet).
3. Display it and confirm (`confirm_cost`, unchanged, still lives in
   `scripts/run_eval.py`).
4. If declined, `confirm_cost` raises `SystemExit` — nothing past this point
   ever executes.
5. **Only after approval**, call `execute_evaluation_run`.
6. Provider construction (inside `execute_evaluation_run`).
7. Embedding pre-warming (inside `execute_evaluation_run`).
8. The evaluation itself (inside `execute_evaluation_run`).

**Where `confirm_cost` lives and how the Day 14 wrapper reuses it**:
unchanged location, `scripts/run_eval.py`. `scripts/run_day14_eval.py`
imports it directly — `from scripts.run_eval import confirm_cost` — which is
already a proven-safe import shape in this codebase: `tests/test_run_eval.py`
already does `from scripts import run_eval` today (confirmed by reading it),
demonstrating `scripts` is already importable as a namespace package from the
project root with no `scripts/__init__.py` needed. `run_day14_eval.py` uses
the same `PROJECT_ROOT`-on-`sys.path` bootstrap `run_eval.py` already has, so
this import resolves identically for both. No new "CLI utility module" is
needed — reusing the existing function directly is simpler.

## 3. Both CLIs — the exact `main()` sequence, side by side

**`scripts/run_eval.py`** (generic — unchanged in scope from the third
revision, corrected in mechanics):

```python
def main(argv=None):
    args = parse_args(argv)
    digest = benchmark_sha256(args.benchmark)
    entries = load_validated_benchmark(args.benchmark, args.repo_id)
    configs = select_configs(args.config)

    uses_vector = any(
        c.use_vector and c.vector_k > 0 for _name, c in configs
    )
    unique_questions = {entry.question for entry in entries}
    estimated_requests = len(unique_questions) if uses_vector else 0

    confirm_cost(
        question_count=len(entries), config_count=len(configs),
        estimated_requests=estimated_requests, skip=args.yes,
    )
    # --- only reached after an explicit 'y' (or --yes) ---

    run = execute_evaluation_run(args.repo_id, entries, configs)

    print(render_markdown_table(run.results))
    report = build_report(
        repo_id=args.repo_id, benchmark_path=str(args.benchmark),
        benchmark_sha256=digest, results=run.results,
        embedding_cache=run.embedding_cache,
        embedding_precomputation=run.embedding_precomputation,
        latency_methodology=run.latency_methodology,
    )
    output_path = timestamped_path()
    write_report(report, output_path)
    print(f"Wrote {output_path}")
```

**`scripts/run_day14_eval.py`** (new, Day-14-only — section 7 covers the
Day-14-specific pieces in full; shown here only to make the shared sequence
visually obvious):

```python
def main(argv=None):
    args = parse_day14_args(argv)
    digest = benchmark_sha256(args.benchmark)

    session_c_report = load_session_c_reference()
    validate_repo_matches_session_c(args.repo_id, session_c_report)
    entries = load_validated_benchmark(args.benchmark, args.repo_id)
    validate_benchmark_matches_session_c(digest, session_c_report)
    validate_corpus_matches_session_c(args.repo_id, session_c_report)
    validate_embedding_model_matches_session_c(session_c_report)

    configs = ABLATION_CONFIGS
    validate_approved_five_row_configuration(configs)

    uses_vector = any(
        c.use_vector and c.vector_k > 0 for _name, c in configs
    )
    unique_questions = {entry.question for entry in entries}
    estimated_requests = len(unique_questions) if uses_vector else 0

    confirm_cost(
        question_count=len(entries), config_count=len(configs),
        estimated_requests=estimated_requests, skip=args.yes,
    )
    # --- only reached after an explicit 'y' (or --yes) ---

    run = execute_evaluation_run(args.repo_id, entries, configs)
    print(render_markdown_table(run.results))
    ...   # section 7
```

Both scripts follow section 2's eight-step ordering exactly; the only
difference is the Day-14 wrapper's extra free-validation steps before the
estimate is computed, and its extra post-run steps after `execute_
evaluation_run` returns (section 7).

## 4. Generic report design: `build_report` carries all three execution-
metadata fields; Day-14-only fields stay in the wrapper

**Decision: extend `build_report`, not attach-in-both-CLIs** (your preferred
option) — `embedding_cache`, `embedding_precomputation`, and `latency_
methodology` are all generic execution metadata describing *how the run was
performed*, true of any caller of `execute_evaluation_run`, not a Day-14
concept:

```python
# ripple/evaluation/runner.py
def build_report(
    repo_id: int, benchmark_path: str, benchmark_sha256: str,
    results: list[ConfigResult],
    embedding_cache: dict | None = None,
    embedding_precomputation: dict | None = None,
    latency_methodology: dict | None = None,
) -> dict:
    ...
    report = {
        "schema_version": 3,
        ... # existing keys, unchanged
        "results": [asdict(result) for result in results],
    }
    if embedding_cache is not None:
        report["embedding_cache"] = embedding_cache
    if embedding_precomputation is not None:
        report["embedding_precomputation"] = embedding_precomputation
    if latency_methodology is not None:
        report["latency_methodology"] = latency_methodology
    return report
```

**Result**: a plain `scripts/run_eval.py` run against any repository now
also honestly documents that its latency numbers exclude live embedding
calls — the exact gap you identified ("must not silently omit that
information") — with **zero** Day-14-specific concepts anywhere in
`runner.py` or the generic report shape.

**Day-14-only fields remain added directly by `scripts/run_day14_eval.py`
onto the dict `build_report` returns** (unchanged from the third revision):
`day14_vs_session_c_ordering`, `acceptance_gates`, `embedding_accounting`,
`day14_accepted`. `build_report` itself has and needs no parameter for any
of these.

**Schema version 3 remains appropriate** — unchanged reasoning from the
third revision (same precedent as Day 12's `reranker_json` addition;
confirmed by reading every consumer of a written report that nothing reads
`schema_version` back in today).

## 5. Reranker preparation — one owner, honest per-row reporting

**The bug in the prior revision's pseudocode**: `execute_evaluation_run`
called `shared_reranker.prepare()` itself, immediately after construction.
By the time `run_benchmark` ran for row 4, `prepare_ms` was already set, so
row 4's own `if prepare_ms is None` check would be `False` — row 4 would
print "reused" even though *this process* never printed "prepared" for
anyone, an honesty regression, not merely a redundant call.

**Fix: `execute_evaluation_run` constructs the shared instance but never
calls `.prepare()` (section 2's pseudocode already reflects this) — `run_
benchmark` is the only place `.prepare()` is ever invoked, for any caller,
Day-14 or generic**:

```python
# ripple/evaluation/runner.py
def run_benchmark(
    repo_id: int, entries: list[BenchmarkEntry], config: RetrievalConfig,
    config_name: str, *,
    embedder: EmbeddingProvider | None = None,
    reranker: "PreparedReranker | None" = None,
) -> ConfigResult:
    active_reranker = None
    reranker_json = None
    if config.use_rerank:
        active_reranker = (
            reranker if reranker is not None else CrossEncoderReranker()
        )
        if active_reranker.prepare_ms is None:
            active_reranker.prepare()
            verb = "prepared"
        else:
            verb = "reused"
        reranker_json = active_reranker.describe()
        print(f"[{config_name}] reranker {verb} "
              f"({active_reranker.prepare_ms:.0f}ms, one-time; "
              "excluded from question latency)")
    ...
```

**Resulting lifecycle for a real five-row run, exactly as you specified**:
one `CrossEncoderReranker` object, constructed once in `execute_evaluation_
run`; row 4 (`+ Cross-encoder rerank`, always processed before row 5 —
`ABLATION_CONFIGS`' fixed order) is the first call with `config.use_rerank`
True, sees `prepare_ms is None`, calls `.prepare()` once, prints "prepared";
row 5 (`+ Graph expansion`) receives the identical object, sees `prepare_ms`
already set, prints "reused." **One model load, one preparation, honest
per-row reporting, preparation excluded from every row's question
latency** (unchanged — `prepare()` was never inside any per-question timer
in any revision of this plan).

`reranker if reranker is not None else CrossEncoderReranker()` (not `or`)
is unchanged from the prior revision — still needed for the same falsy-
object-safety reason.

## 6. Vector-enabled accounting — the exact, complete condition

**Corrected condition, used identically everywhere it appears**: a
configuration requires query embeddings only when `config.use_vector and
config.vector_k > 0` — matching `pipeline.run_pipeline`'s own actual guard
exactly (confirmed by reading it: `if config.use_vector: ... if config.
vector_k > 0: embedder.embed(...)` — a `use_vector=True, vector_k=0`
configuration never calls `embed()` at all). Every place the second and
third revisions wrote `config.use_vector` alone as this condition is
corrected to the compound form:

- **Whether an embedder is constructed** (`execute_evaluation_run`, section 2).
- **The paid-request estimate** (`uses_vector`/`estimated_requests`, both
  CLIs, section 3).
- **`vector_config_count`**, used in `cache_hits`'s expected value
  (`day14_acceptance.validate_embedding_accounting`, section 7) — computed
  as `sum(1 for _name, c in configs if c.use_vector and c.vector_k > 0)`.

**The approved Day 14 five rows are unaffected in practice** — every row's
`vector_k` is the dataclass default, `30`, never `0` — so the concrete
expected values stay exactly what they were: `provider_calls == 40`,
`cache_hits == 200`, `unique_questions == 40`. The fix matters for
correctness and for any future configuration (including a hypothetical
generic `run_eval.py` selection) that might set `vector_k=0` to skip
embedding on purpose — that row must never be silently counted as
"vector-enabled" for cost-estimation or cache-accounting purposes.

## 7. Day-14-only validation (`ripple/evaluation/day14_acceptance.py` and `scripts/run_day14_eval.py`)

**Unchanged in substance from the third revision** — repeated here only
where the surrounding mechanics changed (sections 2/3/6); full detail is not
re-derived where nothing about it changed.

**Provenance checks** (`validate_repo_matches_session_c`, `validate_
benchmark_matches_session_c`, `validate_corpus_matches_session_c`,
`validate_embedding_model_matches_session_c`) — unchanged, still the first
four things `run_day14_eval.py`'s `main()` calls, still before the cost
estimate is even computed. Still call the now-public `runner.
indexed_corpus_fingerprint` (renamed from `_indexed_corpus_fingerprint` in
the third revision so a sibling module can call it).

**Configuration snapshot** (`_APPROVED_DAY14_ROWS`, a list of `(name,
asdict)` pairs; `validate_approved_five_row_configuration`) — unchanged,
still rejects renamed/reordered/missing/extra rows and any field drift.

**Post-run validation, now completed with section 6's exact condition**:

```python
# ripple/evaluation/day14_acceptance.py
def relabel_ordering_comparison(raw: dict) -> dict:
    """Translate compare_ordered_results' generic accepted/batched keys into
    Day-14-specific labels without modifying that function."""
    return {
        **raw,
        "differences": [
            {
                "entry_id": difference["entry_id"],
                "session_c_routed": difference["accepted"],
                "day14_row5": difference["batched"],
            }
            for difference in raw["differences"]
        ],
    }


def validate_embedding_accounting(
    embedding_cache: dict, *, unique_questions: int, entry_count: int,
    vector_config_count: int,
) -> dict:
    expected_provider_calls = unique_questions
    expected_cache_hits = entry_count * vector_config_count
    valid = (
        embedding_cache["provider_calls"] == expected_provider_calls
        and embedding_cache["cache_hits"] == expected_cache_hits
        and embedding_cache["unique_questions"] == unique_questions
    )
    return {
        "expected_provider_calls": expected_provider_calls,
        "actual_provider_calls": embedding_cache["provider_calls"],
        "expected_cache_hits": expected_cache_hits,
        "actual_cache_hits": embedding_cache["cache_hits"],
        "expected_unique_questions": unique_questions,
        "actual_unique_questions": embedding_cache["unique_questions"],
        "valid": valid,
    }
```

**`scripts/run_day14_eval.py`'s `main()`, continuing from section 3's
shared prefix**:

```python
    day5 = next(r for r in run.results if r.config_name == "+ Graph expansion")
    day4 = next(r for r in run.results if r.config_name == "+ Cross-encoder rerank")
    routed_baseline = extract_session_c_routed_row(session_c_report)
    ordering = relabel_ordering_comparison(
        compare_ordered_results(routed_baseline, day5)
    )
    day13_graph_row = load_day13_accepted_graph_row()
    gates = evaluate_gates(
        cross_encoder=asdict(day4), day13_graph=day13_graph_row, routed=day5,
    )

    vector_config_count = sum(
        1 for _name, c in configs if c.use_vector and c.vector_k > 0
    )
    embedding_accounting = validate_embedding_accounting(
        run.embedding_cache,
        unique_questions=len(unique_questions),
        entry_count=len(entries),
        vector_config_count=vector_config_count,
    )

    report = build_report(
        repo_id=args.repo_id, benchmark_path=str(args.benchmark),
        benchmark_sha256=digest, results=run.results,
        embedding_cache=run.embedding_cache,
        embedding_precomputation=run.embedding_precomputation,
        latency_methodology=run.latency_methodology,
    )
    report["day14_vs_session_c_ordering"] = ordering
    report["acceptance_gates"] = gates
    report["embedding_accounting"] = embedding_accounting
    report["day14_accepted"] = (
        ordering["equal"] and gates["accepted"]
        and embedding_accounting["valid"] and run.latency_methodology["valid"]
    )

    output_path = timestamped_path()
    write_report(report, output_path)
    print(f"Wrote {output_path}")

    if not report["day14_accepted"]:
        print(
            "Day 14 validation FAILED — see day14_vs_session_c_ordering, "
            "acceptance_gates, embedding_accounting, and latency_methodology "
            "in the written report. This is a DIAGNOSTIC artifact, not the "
            "accepted Day 14 report."
        )
        raise SystemExit(1)
    print("Day 14 validation passed: ordering matches Session C, all "
          "acceptance gates hold, embedding accounting is exact, and "
          "latency methodology is valid.")
```

`day14_accepted` still requires all four components (unchanged from the
third revision) — ordering equality, quality/latency gates, embedding
accounting, and latency methodology.

## 8. Tests

**`tests/test_runner.py`** (generic):
- `test_execute_evaluation_run_performs_no_prompting` — `builtins.input` is
  patched to raise `AssertionError` if called at all; assert `execute_
  evaluation_run` completes normally (proving it never prompts, directly,
  not just "doesn't import `confirm_cost`").
- `test_execute_evaluation_run_prewarms_cache_before_first_run_benchmark_call`
  — unchanged from the third revision: a real `CachingEmbeddingProvider`
  wraps a fake counting delegate; a fake `pipeline.run_pipeline` calls
  `embedder.embed([question])` itself when given one; a shared ordered-
  events list proves every delegate call precedes every pipeline call.
- `test_execute_evaluation_run_all_vector_rows_see_full_cache_hits`,
  `test_execute_evaluation_run_makes_zero_provider_calls_during_timed_rows`,
  `test_execute_evaluation_run_records_precomputation_metadata` — unchanged
  from the third revision.
- `test_execute_evaluation_run_treats_zero_vector_k_as_vector_disabled` (new,
  section 6) — a config with `use_vector=True, vector_k=0` selected alone:
  assert no embedder is constructed, `embedding_cache` is all-zero.
- `test_execute_evaluation_run_skips_embedder_and_reranker_when_unused` —
  unchanged.
- **`test_run_benchmark_is_the_only_place_prepare_is_called_across_a_shared_
  reranker_run`** (new, replaces the third revision's decline-focused
  reranker test with the actual lifecycle bug fix, section 5) — construct
  one real `CrossEncoderReranker`-shaped fake with an instrumented
  `prepare_calls` counter; call `run_benchmark` twice with it shared, using
  `config`s equivalent to row 4 then row 5 (`use_rerank=True` both times);
  assert: `prepare_calls == 1` total after both calls; the first call's
  captured stdout contains "prepared"; the second call's captured stdout
  contains "reused"; `CrossEncoderReranker` (the real class) is constructed
  **zero** times (since a pre-built fake was injected both times).
- `test_run_benchmark_never_reprepares_an_already_prepared_external_reranker`
  — unchanged (a fake whose `.prepare()` raises if called at all, injected
  already-prepared; asserts no exception).
- `test_run_benchmark_prepares_an_unprepared_external_reranker` — unchanged.
- `test_run_benchmark_does_not_replace_a_falsy_but_valid_injected_reranker`
  — unchanged.
- `test_build_report_uses_schema_version_3` — unchanged.
- `test_build_report_includes_embedding_cache_precomputation_and_methodology_
  when_provided` / `test_build_report_omits_all_three_when_not_provided` —
  extended from the third revision's embedding-cache-only test to cover all
  three new generic parameters (section 4).
- Rename fixups for `indexed_corpus_fingerprint` — unchanged.

**`tests/test_run_eval.py`** (generic CLI):
- `test_main_computes_estimate_and_confirms_before_calling_execute_
  evaluation_run` (new, section 2/3) — patches `run_eval.execute_evaluation_
  run` to record whether it was called and patches `run_eval.confirm_cost`
  to record call order relative to it; asserts `confirm_cost` is called
  first, and — for the decline case — `execute_evaluation_run` is **never**
  called at all (your item 1's explicit test requirement).
- `test_main_decline_never_calls_execute_evaluation_run_or_constructs_
  providers` — `confirm_cost` raises `SystemExit` (decline); assert `execute_
  evaluation_run` was never called, and (since that function is where
  providers are constructed) this transitively proves no provider
  construction happened without needing to patch `OpenAIEmbeddingProvider`/
  `CrossEncoderReranker` directly in this test file at all.
- `test_main_passes_precomputation_and_methodology_into_build_report` (new,
  section 4) — asserts `build_report` receives non-`None` `embedding_
  precomputation`/`latency_methodology` matching what a fake `execute_
  evaluation_run` returned.
- Existing `main()`-level tests (`test_main_runs_only_requested_config_and_
  writes_one_report`, `test_main_runs_all_configured_rows_when_config_is_
  omitted`) — updated to patch `run_eval.execute_evaluation_run` as the new
  boundary, consistent with the third revision's intent, now correctly
  reflecting that `main()` no longer calls `run_benchmark` directly at all.
- `confirm_cost` tests — unchanged.
- **Replaces the third revision's `sys.modules`-based separation test**
  (your item 4) — `test_main_never_reaches_day14_validation_functions`:
  monkeypatch every function in `ripple.evaluation.day14_acceptance`
  (`validate_repo_matches_session_c`, `validate_benchmark_matches_session_c`,
  `validate_corpus_matches_session_c`, `validate_embedding_model_matches_
  session_c`, `validate_approved_five_row_configuration`) to raise
  `AssertionError` if called; run `run_eval.main([...])` with a full,
  unfiltered config selection (the scenario most likely to accidentally
  invoke Day-14 logic if the separation ever broke); assert it completes
  normally. This is stable regardless of test execution order or which
  module happened to import `day14_acceptance` first, since it proves the
  absence of a *call*, not the absence of an *import record*.

**`tests/test_day14_acceptance.py`** (new) — unchanged from the third
revision's list (provenance rejection tests, snapshot rejection tests for
rename/reorder/missing/extra/field-drift, `relabel_ordering_comparison`
label test, `validate_embedding_accounting` pass/fail tests) — plus:
- `test_validate_embedding_accounting_uses_vector_k_aware_config_count` (new,
  section 6) — a `vector_config_count` computed from a config list containing
  one `vector_k=0` row: assert that row is excluded from the expected
  `cache_hits` calculation.

**`tests/test_run_day14_eval.py`** (new) — unchanged from the third
revision's list (four provenance-mismatch tests, ordering/gate/accounting/
methodology failure tests each proving the report is written before
`SystemExit`, and the full-pass test) — plus:
- `test_main_confirms_before_execute_evaluation_run_and_never_calls_it_on_
  decline` — the Day-14-wrapper equivalent of the generic CLI's new test
  above, same technique.

**Required**: `.venv/bin/python -m pytest -q` with Supabase reachable before
accepting Day 14.

## 9. Manual execution workflow

Unchanged from the third revision in shape — one command per CLI, Day 14
always via the dedicated wrapper:

**A. Free validation and focused tests**
```bash
.venv/bin/python -m pytest -q tests/test_runner.py tests/test_run_eval.py \
  tests/test_day14_acceptance.py tests/test_run_day14_eval.py
.venv/bin/python -m pytest -q tests/test_graph_stabilization.py
```

**B. The paid run — one command, one process**:
```bash
.venv/bin/python scripts/run_day14_eval.py --repo-id 13
```

**C. Final full test suite**
```bash
.venv/bin/python -m pytest -q
```

**D. Code commit** — `ripple/evaluation/runner.py`, `ripple/evaluation/
day14_acceptance.py` (new), `ripple/retrieval/rerank.py` (new
`PreparedReranker` protocol only), `scripts/run_eval.py`, `scripts/
run_day14_eval.py` (new), and the test files from section 8.

**E. Evaluation/report commit** — the new JSON report and `DAY_14_
ANALYSIS.md`, committed separately from D.

**Expected paid work, exact**: `provider_calls == 40` (measured, in the
written report's `embedding_cache`); zero OpenAI answer-generation calls;
~81 local, free `predict()` calls total (~40 row 4, ~40 row 5, exactly 1
shared preparation — section 5); one model preparation, shared, honestly
reported as "prepared" once (row 4) and "reused" once (row 5).

## 10. Day 14 analysis artifact

Unchanged from the third revision's required content list (run identity;
five-row table; per-category tables; latency by stage; Session C comparison;
per-stage contribution narrative; RRF regression discussion; graph latency/
quality tradeoff discussion; benchmark/corpus limitations; exact artifact
path; no claim beyond measured evidence) plus the same explicit latency-
methodology language: embedding generation excluded from table latency;
separately-measured `embedding_precomputation` cost quoted from the actual
report; why the five rows are comparable (uniform pre-warming, section 2);
why direct `total_ms` comparison with Day 11/12/13's reports may not be
valid (those reports had no shared cache — every row paid live embedding
cost); `graph_ms` remains directly comparable across all reports, old and
new, since it was never affected by embedding latency in any revision of
this plan.

**Not modified**: `DAY_11/12/13_ANALYSIS.md`, `GRAPH_STABILIZATION_SESSION_
A/B/C.md`, `GRAPH_STABILIZATION_INTENT_ROUTER.md`, any existing JSON report.

## 11. Scope and stop condition

Unchanged from the third revision: on failure, stop, inspect the four
written validation objects, determine the cause (configuration drift,
corpus drift, model drift, or a code bug in this plan's own new logic), do
not tune parameters to force a pass. Out-of-scope list unchanged: Day 15
query rewriting, Day 16 safety work, the API, README work, RRF tuning, graph
scoring, seed/cap tuning, any new retrieval experiment. `ripple/retrieval/
graph.py`, `ripple/retrieval/intent.py`, `ripple/retrieval/pipeline.py`,
`ripple/retrieval/fusion.py`, `ripple/config.py`, `ripple/evaluation/
graph_stabilization.py`, `SPEC.md` are not modified. `ripple/retrieval/
rerank.py` gains only the `PreparedReranker` protocol definition.

**Day 14 is complete only when**: all five fresh rows exist in one new
report with `schema_version: 3`, `embedding_cache`, `embedding_
precomputation`, and `latency_methodology` all present and accurate;
`day14_accepted` is `true` (or the failure is fully documented and Day 14 is
explicitly not declared complete); `.venv/bin/python -m pytest -q` is green;
implementation and evaluation artifacts are committed separately; `scripts/
run_eval.py` remains fully usable for any other repository or filtered
config selection, now also honestly reporting its own latency methodology.

## 12. Audit of this revision

- **No import cycle**: `runner.py` contains no reference to `confirm_cost`,
  `argparse`, or `input()` anywhere; `execute_evaluation_run`'s only
  "control-flow" responsibility is running configs after being handed
  already-approved `entries`/`configs` — verified by re-reading section 2's
  final signature, which takes no confirmation-related parameter at all.
- **No prompting inside `runner.py`**: confirmed — `confirm_cost` is defined
  and stays in `scripts/run_eval.py`; `runner.py`'s only prints are status
  messages (pre-warm summary, reranker prepared/reused), never a `[y/N]`
  prompt.
- **No provider construction before confirmation**: both `main()`s (section
  3) call `confirm_cost` strictly before `execute_evaluation_run`, and
  `execute_evaluation_run` is the only place `OpenAIEmbeddingProvider`/
  `CachingEmbeddingProvider`/`CrossEncoderReranker` are constructed — so
  nothing is ever constructed before approval, tested directly (section 8).
- **No generic report with undocumented prewarmed latency**: `build_report`
  (section 4) now always receives and serializes `embedding_precomputation`/
  `latency_methodology` from any caller using `execute_evaluation_run`,
  generic or Day-14.
- **Exactly one reranker preparation with honest prepared/reused output**:
  section 5's fix moves `.prepare()` entirely into `run_benchmark`, tested
  directly against a shared fake across two sequential calls.
- **Stable, non-test-order-dependent separation tests**: section 8's
  replacement test patches Day-14 functions to raise and proves they're
  never *called*, not merely absent from `sys.modules`.
- **Exact embedding accounting**: section 6's `use_vector and vector_k > 0`
  condition applied consistently in `execute_evaluation_run`, both CLIs'
  estimate calculations, and `validate_embedding_accounting`'s
  `vector_config_count` — no remaining place uses the incomplete
  `use_vector`-alone condition.
- **Day-14-only validation isolated from the reusable evaluator**: `runner.
  py`/`scripts/run_eval.py` contain zero references to Session C, the
  approved snapshot, or acceptance gates — all of that lives in `ripple/
  evaluation/day14_acceptance.py` and `scripts/run_day14_eval.py` only.

## 13. Remaining blocking decisions

**None. Implementation can begin.** Every issue raised across all four
rounds of review has an explicit, concrete, implemented resolution in this
document: the persisted configuration (section 1), the module/script split
protecting the generic CLI (sections 2–3), fair pre-warmed latency
accounting (sections 2, 6) with honest generic and Day-14 reporting
(sections 4, 10), a single correctly-attributed reranker preparation
(section 5), Session C provenance and exact configuration-snapshot
validation (section 7), a complete four-part acceptance condition (section
7), and a full, non-fragile test matrix (section 8). Nothing here was left
implicit, deferred, or invented as a new approval point.
