# Implementation Plan — Days 8–11: Benchmark and First Ablation Rows

## 0. Process note for this cycle

**`SPEC.md` is read-only**, same as every prior cycle. If anything below runs into a
genuine SPEC ambiguity or apparent bug, it is flagged in section 11, never silently
resolved by editing `SPEC.md`.

This is a **consolidated plan covering four SPEC.md days** (8, 9, 10, 11) in one
document, because they form one continuous arc — build the benchmark (8, 9), build the
machinery to score it (10), then use that machinery to produce the first real numbers
(11) — and the design decisions for one day constrain the others. The **execution
stays incremental**: one small step at a time, you decide who implements each step
(you or Codex), test and review before the next step, and commit at the end of each
completed day rather than one giant commit at the end. Section 7 lays out that
step-by-step order explicitly.

**Two-level explanations.** Several ideas here (Recall@k, MRR, why embeddings still
cost money without "asking" anything) get both a **Simple version** (plain language,
no jargon) and a **Technical version** (precise, matches SPEC.md's formulas) — both
because you asked for it explicitly, and because getting these exactly right matters
more here than in any prior cycle: this is the day the project's actual deliverable
(measured numbers) starts existing.

**Python interpreter portability.** This machine does not reliably have `python` or
`python3` on `PATH` — the same issue Day 7's own `docker-compose` acceptance script
had to work around. Every Python invocation described anywhere in this plan — running
`pytest`, running `scripts/run_eval.py`, the manual benchmark-validation command,
the address-inventory authoring helpers, and any inline `-c` snippet — resolves the
interpreter once, the same way, and verifies it before use:
```bash
PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
if [ ! -x "$PYTHON_BIN" ]; then
  echo "Python interpreter not found or not executable: $PYTHON_BIN" >&2
  exit 1
fi
```
Every command shown later in this plan that runs Python — `"$PYTHON_BIN" -m pytest`,
`"$PYTHON_BIN" scripts/run_eval.py ...`, `"$PYTHON_BIN" -c "..."`, or a scratch `.py`
file run as `"$PYTHON_BIN" scratch.py` — assumes `$PYTHON_BIN` has already been
resolved and verified this way. **Bare `python` or `python3` is never described as
portable anywhere in this plan.**

## 1. Objective

Build the 40-question labeled benchmark (Days 8–9), the machinery to score any
`RetrievalConfig` against it — metrics, a runner, `scripts/run_eval.py` (Day 10) — and
use that machinery to produce the first three real, honestly-measured ablation rows
(Day 11): Vector only, Vector + BM25 (no RRF), Vector + BM25 + RRF.

This sits on top of Days 1–7, all implemented and verified (123 passing tests,
`docker compose` reproducibility confirmed). Days 12 (reranking), 13 (graph
expansion), and 15 (query rewriting) are **not** touched — this plan explicitly stops
short of them (section 8, section 9).

## 2. Relevant SPEC.md requirements

- **Section 10.1 (Dataset format)**, quoted in full:
  ```json
  [
    {
      "id": "q001",
      "question": "Which resource creates the NAT gateway?",
      "expected": ["aws_nat_gateway.this"],
      "category": "lookup"
    },
    {
      "id": "q002",
      "question": "What breaks if I delete the worker security group?",
      "expected": ["aws_security_group.worker", "aws_instance.node"],
      "category": "blast_radius"
    }
  ]
  ```
  > Categories: `lookup` (one specific block), `relational` (depends-on questions),
  > `blast_radius` (what references this), `attribute` (which blocks have property X).
  >
  > Aim for roughly 15 lookup, 10 relational, 8 blast radius, 7 attribute. Blast
  > radius and relational questions are where graph expansion earns its row in the
  > table — if the benchmark is all lookups, graph expansion will show no gain and the
  > ablation is uninformative.
  >
  > **Every `expected` address must exist in the `resources` table.** Write a
  > validator that checks this and run it as part of the test suite. A typo'd address
  > silently caps your Recall at less than 100% and you will spend a day debugging
  > retrieval that works fine.
  - **`q002`'s worked example is load-bearing for this plan's labeling policy** (section
    3.2): note that `expected` includes `aws_security_group.worker` *itself*, alongside
    `aws_instance.node` (the thing that references it). This is the only worked
    example SPEC.md gives for a non-`lookup` category, and it directly resolves one of
    this plan's required decisions.
- **Section 10.2 (Metrics)**, quoted in full:
  ```python
  def recall_at_k(expected, retrieved, k):
      top = set(retrieved[:k])
      return len(top & set(expected)) / len(expected)

  def precision_at_k(expected, retrieved, k):
      top = retrieved[:k]
      return sum(1 for a in top if a in set(expected)) / k

  def reciprocal_rank(expected, retrieved):
      for i, a in enumerate(retrieved, start=1):
          if a in set(expected):
              return 1.0 / i
      return 0.0
  ```
  > Report the mean across all questions. MRR is the mean of `reciprocal_rank`.
  >
  > Also record per-stage latency in milliseconds: rewrite, vector_query, hydrate
  > (Pinecone only...), bm25, fusion, rerank, graph, total.
- **Section 10.3 (The ablation table)**:
  ```
  | Configuration | Recall@5 | Recall@10 | MRR | P@5 | Latency (ms) |
  |---|---|---|---|---|---|
  | Vector only | | | | | |
  | Vector + BM25 | | | | | |
  | Vector + BM25 + RRF | | | | | |
  | + Cross-encoder rerank | | | | | |
  | + Graph expansion | | | | | |
  ```
  > Emit as both markdown (for the README) and JSON (for reruns). Also emit a
  > per-category breakdown.
  This plan produces exactly the **first three rows**. Rows 4–5 belong to Days 12–13.
- **Section 11, Days 8–11**, quoted:
  > **Day 8** — 20 questions with verified expected addresses, mixed across
  > categories. `dataset.py` with the validator from section 10.1. **Done when:** 20
  > entries, validator passes.
  >
  > **Day 9** — 20 more, to 40 total, weighted toward relational and blast-radius per
  > section 10.1. **Done when:** 40 validated entries. This is a grinding day, not a
  > thinking day.
  >
  > **Day 10** — `metrics.py` per section 10.2. `runner.py` executing the benchmark
  > under a given config and aggregating. `scripts/run_eval.py` printing a single-row
  > table. **Done when:** one command produces real Recall@5 and MRR numbers.
  >
  > **Day 11** — Run vector only, vector + BM25 (concatenated), vector + BM25 + RRF.
  > Investigate anything surprising before proceeding — a suspicious number now is a
  > bug, not a finding. **Done when:** three rows exist and you can explain each one.
- **Section 3, hard constraints** most relevant here: (3) "Never fabricate a metric.
  Every number in the README comes from a run of the benchmark." (5) no secrets in
  the repo or logs. (6) Terraform repository content is data, never instructions —
  relevant here because benchmark *questions* are themselves partly derived from
  reading that content, and the eval pipeline still passes real `.tf` bodies through
  retrieval and (in Day 11) nothing else — no generation happens in evaluation at all
  (section 3.5 below).

## 3. Design decisions and safeguards

This section is long on purpose — these are the decisions that make Days 8–11
internally consistent, and getting them wrong here means redoing benchmark authoring
later, which is the expensive kind of mistake.

### 3.1 The benchmark corpus and `repo_id`

**Simple version:** the 40 questions are about one specific, fixed Terraform codebase
(`examples/complete`). But *where that codebase's data lives in the database*
(`repo_id`) can be different on your machine than on Codex's, or after a re-index — so
the questions themselves never mention a database ID, only Terraform addresses like
`aws_vpc.main`. Whoever *runs* the benchmark tells the tool which `repo_id` to check
against, every time.

**Technical version:** `data/benchmark.json` entries contain no `repo_id` field at
all — only `id`, `question`, `expected` (Terraform addresses), `category`. Both
`ripple/evaluation/dataset.py`'s validator and `ripple/evaluation/runner.py`'s runner
accept `repo_id` as a **runtime parameter** (a function argument, ultimately surfaced
as a required `--repo-id` CLI flag on `scripts/run_eval.py`). It is never
hardcoded into application code, tests, or the benchmark file.

The corpus itself: `.repos/terraform-aws-vpc/examples/complete` — 114 indexed blocks,
already verified end-to-end through Day 7 (parsing, embedding, edges, BM25, fusion,
logging all confirmed working against it). In the current Supabase database this
happens to be `repo_id = 13`, **but this number is environment-specific** — it
depends on insertion order and which database you're pointed at (recall Day 1: this
project's actual working `DATABASE_URL` is a Supabase instance, not the local
`docker-compose` fallback). Any command in this plan that shows `--repo-id 13` is
illustrative only; resolve the real value independently in your own environment, via
`"$PYTHON_BIN"` (section 0 — e.g. `"$PYTHON_BIN" -c "..."` with the snippet below, or
a scratch file run as `"$PYTHON_BIN" scratch.py`):
```python
from ripple import db
with db.get_connection() as conn, conn.cursor() as cur:
    cur.execute("SELECT id FROM repos WHERE name = 'vpc-complete' ORDER BY id DESC LIMIT 1")
    print(cur.fetchone())
```
(`ORDER BY id DESC LIMIT 1` — same Day 7 lesson: repo names aren't unique, so always
take the most recent match rather than assuming exactly one row.)

### 3.2 Labeling policy — precise and non-negotiable once set

This is the single most important decision in this plan to get right and get
consistent, because 40 hand-authored questions with an inconsistent policy produces
a benchmark that silently measures the wrong thing.

**What goes in `expected`, per category:**

- **`lookup`** — "which resource does X." `expected` is exactly the block(s) that
  directly answer the question — normally **one address**. SPEC's own framing
  (section 1: "Ground truth is cheap... one correct answer a human can verify in
  seconds") is the design target: write lookup questions so they have exactly one
  correct answer. There is no separate "subject" here — the answer *is* the lookup
  target.
- **`blast_radius`** — "what breaks if I delete/change X" / "what references X."
  **`expected` = {the subject block itself} ∪ {every block that directly references
  it}.** This is not a guess — it is exactly what SPEC's own worked example does:
  `q002` asks about `aws_security_group.worker` and its `expected` list is
  `["aws_security_group.worker", "aws_instance.node"]` — the subject *and* its
  dependent, both included. The reasoning generalizes cleanly: if you delete X, X
  itself is gone (it "breaks" in the most literal sense) *and* everything that
  pointed at X breaks too. Retrieval-wise: `graph.dependents(subject_id)` gives the
  "everything that references it" half; the subject's own row is the other half.
- **`relational`** — "what does X depend on." **`expected` = the block(s) X directly
  references — `graph.dependencies(subject_id)` — and does *not* include the subject
  itself.** SPEC gives no worked example for this category, so this is a genuine
  judgment call, made explicitly rather than left ambiguous: "what does X depend on"
  is not naturally answered by "X" (a thing doesn't "depend on itself"), unlike
  `blast_radius`'s inclusive "what breaks" framing. This is also the choice that
  keeps the category meaningful for measurement — if the subject (already named in
  the question text) were always in `expected`, a retrieval method that does nothing
  smarter than matching the literally-named address would get partial credit without
  doing any actual relational reasoning, which is exactly the signal `relational`
  and `blast_radius` exist to isolate (SPEC: "Blast radius and relational questions
  are where graph expansion earns its row"). **This asymmetry between the two
  categories is deliberate and must be documented in `ripple/evaluation/dataset.py`**
  — as the module or `BenchmarkEntry` docstring, stating both rules explicitly and
  naming SPEC.md's `q002` as the source of the `blast_radius` convention — so it's
  never "fixed" into inconsistency later by someone assuming both categories work
  the same way. **Not in `data/benchmark.json` itself** — JSON has no comment syntax,
  so nothing here is ever proposed as an in-file annotation; `dataset.py` (or, if a
  longer explanation is wanted later, a short standalone doc under `data/` or
  `ripple/evaluation/`) is the only place this policy lives in the codebase.
- **`attribute`** — "which blocks have property X" (e.g., "which security groups
  allow inbound traffic on port 22?"). **`expected` = every block in the corpus that
  actually has that property — exhaustively, not just one example remembered from
  skimming the file.** This is the category most at risk of a silently-wrong
  denominator: SPEC's `recall_at_k` formula divides by `len(expected)`, so an
  *incomplete* `expected` set doesn't just under-count — it can make recall look
  artificially perfect if retrieval happens to return exactly the (incomplete) set
  you wrote down. Section 3.4's address-inventory workflow exists largely because of
  this category — you cannot write a correct `attribute` question without actually
  querying/grepping the whole corpus for every match, not recalling one from memory.

**Duplicate-address rule**: the *same* address may legitimately appear in `expected`
across *different* questions (e.g., `aws_vpc.main` can be the answer to a `lookup`
question and also appear inside a `blast_radius` question's `expected` set for a
different subject) — that's normal and expected. What must never happen is a
duplicate address *within one entry's own* `expected` list (`["aws_vpc.main",
"aws_vpc.main"]`) — that is always a copy-paste bug, and section 3.3's structural
validator rejects it.

### 3.3 Two kinds of validation, kept explicitly separate

**Structural validation** (`ripple/evaluation/dataset.py`, pure, offline, no database,
no `OPENAI_API_KEY`):
- The file is a JSON array (matching section 10.1's literal format — not an object
  wrapper).
- Every entry has `id` (non-empty string, matching the `q\d{3}` convention SPEC's own
  examples use, and **unique** across the file), `question` (non-empty string),
  `category` (one of exactly `lookup`/`relational`/`blast_radius`/`attribute`), and
  `expected` (a non-empty list of non-empty strings, no duplicate address within the
  same entry — section 3.2).
- Fails loudly, identifying the offending entry by `id` and index, never silently
  drops or "fixes" a malformed entry.

**Database validation** (`ripple/evaluation/dataset.py`, needs a real `repo_id` and a
reachable database — the direct implementation of SPEC 10.1's required validator):
- Every address in every entry's `expected` list exists in `resources.address` for
  the given `repo_id`. Reports *every* missing `(entry_id, address)` pair in one pass,
  not just the first — 40 questions means typos are likely, and fixing them one
  test-run at a time would be exactly the "spend a day debugging retrieval that works
  fine" trap SPEC.md warns about.

**How this gets tested is deliberately different from how it gets used for real
acceptance, and must stay that way:** `validate_addresses_exist`'s *logic* (correctly
reporting every missing pair, not just the first) is unit-tested by **monkeypatching
`db.fetch_resource_addresses`** to return a small, fixed, in-test address set — no
real database connection, and no dependency on which repo happens to be indexed on
whatever machine runs the test suite. Separately, `db.fetch_resource_addresses`
*itself* (the real SQL) is integration-tested against a **temporary, throwaway
repo/resources setup** created and torn down inside the test (this project's existing
ad hoc fixture pattern — insert a couple of rows, assert, then `DELETE FROM repos
WHERE id = %s`, same cleanup convention used since Day 6/7) — **never** a lookup by a
real repo's name like `vpc-complete`, which would make the test suite's pass/fail
depend on which repo happens to exist in whatever database it's pointed at. Running
`validate_addresses_exist` against the **real** `data/benchmark.json` (20 entries
after Day 8, 40 after Day 9) and the **real, currently-indexed corpus's** `repo_id` is
still a required acceptance step for both days — but it is a **manual command**
(section 6's Day 8/9 acceptance), run once per day by whoever is finishing that day,
using the real `repo_id` resolved per section 3.1, **not** an automated `pytest`
test. This keeps `"$PYTHON_BIN" -m pytest` (section 0) fully portable — passable on
any machine, with any database state or none at all, per the existing
skip-if-unreachable convention — while still requiring the real validation to
actually happen before either day is called done.

**Semantic verification — a required human/Codex process step, not something
`dataset.py` can check automatically:** confirming an address *exists* in the
database is not the same as confirming the *question and its `expected` answer are
actually true of the real code*. Before adding any entry to `data/benchmark.json`,
whoever authors it must open the real source at that block's `file_path`/
`start_line`/`end_line` (or read its `body` straight from `db.fetch_resource_bodies`)
and confirm the question's premise genuinely holds — not "does this address exist"
but "does this Terraform block actually do/reference/have what the question claims."
This is a checklist item for every single entry (section 6, Day 8/9 steps), not a
unit test — there is no automated way to check semantic truth without a second LLM
judge, which is out of scope and not requested.

### 3.4 Address-inventory workflow (for authoring, not application code)

Writing 40 accurate questions means being able to answer, quickly and correctly:
"what addresses exist," "what does X reference," "what references X," and "which
blocks actually have property Y" — all against the real, indexed corpus. These are
**one-off authoring aids**, not new production code — no new script or module is
proposed for this; every one of the following reuses functions that already exist
(plus the one new `db.py` function in section 4). Run these directly via `"$PYTHON_BIN"`
(section 0) — either an interactive REPL (`"$PYTHON_BIN"` with no arguments) or a
scratch file (`"$PYTHON_BIN" scratch.py`) — while authoring, substituting the real
`repo_id` from section 3.1:

```python
from ripple import db
from ripple.retrieval import graph

REPO_ID = ...  # resolved per section 3.1, never hardcoded in committed files

# 1. Every indexed address for this repo (new db.py function, section 4):
addresses = db.fetch_resource_addresses(REPO_ID)

# 2. address -> id map, needed because graph.dependents/dependencies take an id:
by_address = {addr: rid for rid, addr, body in db.fetch_resource_bodies(REPO_ID)}
by_id_body = {rid: body for rid, addr, body in db.fetch_resource_bodies(REPO_ID)}

# 3. What references aws_security_group.rds (candidate blast_radius subject):
subject_id = by_address["aws_security_group.rds"]
[n.address for n in graph.dependents(subject_id)]

# 4. What aws_security_group.rds itself references (candidate relational subject):
[n.address for n in graph.dependencies(subject_id)]

# 5. Read the real body before writing/confirming a question about it:
print(by_id_body[subject_id])
```
For `attribute` questions specifically: **grep the real `.tf` files directly**
(`grep -rn "ingress" .repos/terraform-aws-vpc/examples/complete/`, or scan
`db.fetch_resource_bodies`'s `body` field for every row) rather than relying on
memory of what you skimmed earlier — this is the exhaustiveness check section 3.2
requires.

### 3.5 Evaluation stays independent of answer generation

**Simple version:** grading the benchmark only checks "did the search find the right
Terraform blocks," never "did the AI write a good sentence about them." So the eval
code never has to actually ask the AI to write an answer — it just runs the search
part and checks the results, which is much cheaper and faster.

**Technical version:** `ripple/evaluation/runner.py` calls `pipeline.run_pipeline(
repo_id, question, config)` and reads `result.blocks` (comparing `[block.address for
block in result.blocks]` against `entry.expected`) and the **full** `result.
latency_json` mapping, unchanged (section 3.6 explains why the complete per-stage
mapping is preserved rather than reduced to `total_ms` at this point). **It never
calls `ripple.llm.generate.answer_question` anywhere.** This
is a hard requirement, verified by a dedicated test (section 6, Day 10) that fails
loudly if `answer_question` is ever invoked during a benchmark run — protecting
against exactly the "40 or 120 unnecessary generation calls" risk named in the
request.

This does **not** mean evaluation is free: every config in this plan has
`use_vector=True`, and the vector stage still needs to turn the question text into an
embedding to compare against stored vectors — that's a **retrieval** step (finding
the right blocks), completely separate from **generation** (writing a natural-language
answer about them). BM25 needs no embeddings at all (pure lexical matching); RRF/
fusion needs no API calls either (pure math over already-retrieved lists). So: every
question, under every one of this plan's three configs, costs exactly one embedding
request — real counts and why they are not reduced by caching are in section 3.7.

### 3.6 Metrics — exact formulas, explicit edge-case policy

`ripple/evaluation/metrics.py` reproduces SPEC 10.2's three functions **exactly**
for every valid input, plus explicit, documented behavior for the inputs SPEC's
snippet doesn't address (rather than letting Python's own exceptions or slicing
semantics decide silently — the same posture every prior day in this project has
taken toward numeric edge cases):

| Condition | Behavior | Why |
|---|---|---|
| `k <= 0` in `recall_at_k`/`precision_at_k` | raise `ValueError` | `retrieved[:k]` with negative `k` is Python's negative-slice reinterpretation (the exact bug class Day 6 fixed in `pipeline.py`) — never let it happen silently here either. |
| `expected == []` in `recall_at_k` | raise `ValueError` | SPEC's formula divides by `len(expected)`; zero has no sensible "recall against nothing" reading. In practice this should never occur — section 3.3's structural validator requires non-empty `expected` for every entry — this guard is defense in depth, not an expected runtime path. |
| `retrieved` has fewer than `k` items | no special case | `retrieved[:k]` naturally returns what's there; SPEC's formula already handles this correctly as written. |
| No matches at all | no special case | All three functions already return `0.0` correctly per SPEC's own formulas — nothing to add. |
| Aggregating an empty list of `QuestionResult`s | raise `ValueError` | mirrors the same "undefined, not zero" policy as the `expected == []` case; should never occur given 40 real entries, defense in depth again. |
| `QuestionResult.latency` key sets differ across inputs to one `aggregate()`/`aggregate_by_category()` group | raise `ValueError` (finding 4) | all inputs to one aggregate ran the same `RetrievalConfig`, so they should have identical latency keys; a mismatch means a real per-question inconsistency, not something to average over silently. |
| Per-category breakdown | grouped by `category`, categories emitted in **sorted order** | deterministic output — two runs over the same data always print categories in the same order. |

`retrieved` must always be the address list **in pipeline rank order** —
`[block.address for block in result.blocks]`, never re-sorted — since both
`reciprocal_rank` and every `top-k` slice depend on order being preserved exactly as
the pipeline produced it.

**`final_k` must be at least 10 for Recall@10 to be meaningful.**
`RetrievalConfig.final_k` defaults to `8` (`ripple/config.py`) — a production default
tuned for answer-generation context size, not for evaluation. `PipelineResult.blocks`
is capped at `final_k` items, so if evaluation scored `PipelineResult.blocks` under a
`RetrievalConfig` using that default, `retrieved` could never contain 10 items and
`recall_at_k(expected, retrieved, k=10)` would be structurally incapable of measuring
what a `k=10` cutoff is supposed to measure — it would silently degrade to
`recall_at_8` while still being printed and labeled as `Recall@10`. **This is an
evaluation-only setting, not a change to `ripple/config.py`'s production default**:
`ripple/evaluation/runner.py`'s `ABLATION_CONFIGS` (section 5) sets `final_k=10`
explicitly on all three configs, alongside the already-set `vector_k=30`/`bm25_k=30`.
Neither `ripple/config.py` nor `ripple/retrieval/pipeline.py` is touched — this is
purely a choice of *which config values evaluation constructs and passes in*, exactly
like every other field `ABLATION_CONFIGS` already sets explicitly (`use_rerank=False`
etc.). Tests (section 6, Day 10) assert every entry in `ABLATION_CONFIGS` has
`final_k >= 10`, and separately assert that a canned 10-block `PipelineResult` has all
10 addresses survive into `QuestionResult.retrieved` unmodified. **Flagged for your
awareness, not something this plan resolves inside `SPEC.md`**: SPEC.md's own
`RetrievalConfig` default (`final_k=8`) and SPEC 10.3's `Recall@10` column are in
tension for any caller that doesn't override `final_k` — e.g. `scripts/ask.py`'s
default config returns at most 8 blocks, so a hypothetical Recall@10 measured against
`ask.py`'s literal default would read differently (and worse) than what this plan
reports using `final_k=10`. This plan resolves the tension only for its own three
ablation rows, as SPEC 10.3's table demands; it does not edit `SPEC.md` or
`ripple/config.py` to resolve the tension more broadly, since that's a genuine
specification ambiguity, not a bug in this plan's own code (see also section 11).

**Per-stage latency is preserved end to end, not collapsed to a total.** SPEC 10.2
asks for per-stage latency (`rewrite`, `vector_query`, `hydrate`, `bm25`, `fusion`,
`rerank`, `graph`, `total`), and `PipelineResult.latency_json` already reports exactly
the stages that ran, keyed by name — a vector-only config's dict has only
`{"vector_query_ms", "total_ms"}`; a vector+BM25+RRF config's has
`{"vector_query_ms", "bm25_ms", "fusion_ms", "total_ms"}` — stages that did not
execute are simply **absent** from the dict, never present with a misleading `0`.
This plan preserves that shape all the way through: `QuestionResult` stores the
**complete** `latency_json` mapping for that question (section 5's `latency` field),
not just `total_ms`.

**`aggregate`/`aggregate_by_category` require every input's latency keys to match
exactly (finding 4) — this is a consistency check, not a partial average.** All the
`QuestionResult`s passed into one `aggregate()` (or, within `aggregate_by_category`,
all the results sharing one category) were scored under the **same**
`RetrievalConfig`, so they should all have run the same pipeline stages and therefore
have **identical** latency dict keys. Before computing any stage mean, `aggregate`
collects the set of `latency` keys from every input `QuestionResult`; if those key
sets are not all identical, it raises `ValueError` naming the mismatch — this is
deliberately **not** averaged over silently, because a genuine per-question
difference in which stages ran (e.g. one question in a `"Vector + BM25 + RRF"` run
somehow missing `fusion_ms`) means something is actually wrong with that run, not
that some other question happens not to need that stage. If all key sets match,
`AggregateMetrics.mean_latency_by_stage` is the mean of **every** key in that shared
set, computed across **all** questions in the aggregate — never a partial mean over
only the questions that happened to have a key, since after the consistency check
every question has every key. **Missing stages are still never synthesized as
`0.0`**: a key that is absent from every `QuestionResult` in one aggregate (because
that config never executes that stage) is simply absent from `mean_latency_by_stage`
too. **Different configurations may legitimately have different latency-key sets** —
a `"Vector only"` `ConfigResult`'s aggregate has a different key set than a `"Vector +
BM25 + RRF"` `ConfigResult`'s, and that's expected: each is aggregated
**independently**, so this consistency rule applies *within* one config's own
results, never *across* configs. `ConfigResult`'s JSON serialization (section 3.9)
carries both the raw per-question latency mappings and the aggregated per-stage
means; the printed markdown table still shows only the single mean-`total_ms`
**Latency (ms)** column, matching SPEC 10.3's table shape exactly — the richer
per-stage data lives in JSON, without changing the table's column count. Tests
(section 6, Day 10) cover: hand-computed aggregation when every input's latency keys
match; `aggregate`/`aggregate_by_category` raising `ValueError` when they don't;
two independent aggregates (simulating two different configs) each succeeding with
their own, mutually different key sets; and that a key absent from an entire
config's results is absent from its aggregate, never a synthesized zero.

### 3.7 Cost and runtime — inspected, and deliberately left uncached

**Simple version:** asking the AI "what does this text mean as a vector of numbers"
(an embedding) costs a little money and takes a little time, every time. Running all
40 questions through all 3 configurations in Day 11 means 120 of these calls — the
same 40 questions asked 3 times over. That sounds wasteful, and it would be tempting
to compute each question's embedding once and reuse it across all 3 configurations.
**This plan deliberately does not do that**, because the pipeline's own timing
measurement would stop being trustworthy if it did — see below for why.

**Technical version:** `pipeline.run_pipeline(repo_id, question, config,
embedder=None)` measures `vector_query_ms` as *one* span that starts before the
question is embedded and ends after `vector_store.query` returns — the embedding API
call's latency is **inside** `vector_query_ms`, not separated out (SPEC.md's own
`hydrate_ms` precedent aside, there is currently no distinct `embedding_ms` field).
If a single embedding were computed once and reused across all three
`ABLATION_CONFIGS`, whichever configuration happened to run **first** for a given
question would pay the real embedding latency, and the two that ran **after** it
would show artificially low `vector_query_ms` from a cache hit that has nothing to
do with that configuration's actual retrieval cost. The reported latency column
would then depend on *the order the configs happened to run in* — exactly the kind
of measurement artifact this project's whole "never fabricate a metric" posture
exists to prevent. Recall/MRR would be unaffected (identical embeddings produce
identical retrieval results either way), but the latency numbers would not be
honest, comparable measurements anymore.

**So**: `ripple/evaluation/runner.py` uses `OpenAIEmbeddingProvider` directly,
uncached, exactly as `pipeline.run_pipeline`'s default already does — no wrapper, no
new class, `embedder` is simply never overridden by the runner. Every question, under
every configuration, pays its own real embedding call. Cost, stated plainly:
**Day 10's single-configuration run makes approximately 40 embedding requests; Day
11's three-configuration run makes approximately 120** (3 × 40, no sharing). Both
numbers are real, not reduced by an optimization that would have compromised the
latency column's validity.

**Recorded as future work, not built now:** if per-question embedding reuse is
wanted later, it needs one of two things first — either `pipeline.py` starts
recording a separate `embedding_ms` span (so a cached lookup's near-zero time isn't
silently folded into `vector_query_ms`), or embeddings are precomputed *outside* the
timed region entirely (e.g., embed every question once before starting any timed
run, then pass precomputed vectors in) as a deliberate `pipeline.py` interface
change. Either is a real design decision for whichever future day takes it on, not
something to slip in quietly under Days 8–11.

**The same reasoning does not apply to BM25, and it stays a known, low-priority
inefficiency rather than something worth fixing here for a different reason**:
`pipeline.run_pipeline` calls `build_index(repo_id)` **internally**, with no
injection point equivalent to `embedder=`, so nothing outside `pipeline.py` could
reuse a `BM25Index` across calls even if it wanted to. Rebuilding a 114-block BM25
index costs no API money and is fast (pure local CPU, sub-second) — unlike the
embedding case, reusing it would be a runtime nicety with no cost or latency-honesty
motivation strong enough to justify changing Day 6's `pipeline.py` during this
cycle. Flagged here as a possible future `bm25_index=` parameter, not assumed or
built.

**Confirmation gate**: `scripts/run_eval.py` prints the number of configurations,
questions, and the real (uncached) estimated embedding-request count — ~40 for a
single configuration, ~120 for all three — and requires an explicit `y` confirmation
(skippable via `--yes`) before the **first** real, paid run in both Day 10 and Day
11 — same convention as every prior day's manual acceptance check.

## 4. Exact file scope

Create:
- `ripple/evaluation/__init__.py`
- `ripple/evaluation/dataset.py`
- `ripple/evaluation/metrics.py`
- `ripple/evaluation/runner.py`
- `data/benchmark.json` (20 entries after Day 8, 40 after Day 9)
- `scripts/run_eval.py`
- `tests/test_dataset.py`
- `tests/test_metrics.py`
- `tests/test_runner.py`

Modify:
- `ripple/db.py` — add **two** new, narrow functions:
  - `fetch_resource_addresses(repo_id) -> list[str]` (`SELECT address FROM resources
    WHERE repo_id = %s`). Justification: the existing `fetch_resource_bodies(repo_id)`
    returns `(id, address, body)` and *would* technically work for the validator's
    existence check, but it always pulls every block's full `body` text along with
    it — wasteful for a function whose only job is "does this address exist," and
    it's called potentially many times (once per `scripts/run_eval.py` invocation,
    plus every test run). A single-purpose, minimal read is the more honest fit,
    consistent with this project's existing pattern of narrow, purpose-built `db.py`
    functions (`fetch_bm25_documents` vs. `fetch_resource_bodies` is the same kind of
    split, already precedented).
  - `fetch_repo(repo_id) -> tuple[str, str | None, str] | None` — returns
    `(name, source_url, local_path)` for that repo, or `None` if it doesn't exist.
    Justification (finding 7): there is currently **no** function that reads a
    repo's own identity back by id — `insert_repo` only writes one. The
    reproducibility-provenance report (section 3.9) needs the corpus's name and
    `source_url` for identity, and its `local_path` to derive the corpus's Git
    revision at runtime. Without a narrow, purpose-built read function,
    `scripts/run_eval.py` would have no supported way to get this and would be
    tempted to hand-write raw SQL outside `ripple/db.py`, breaking this project's
    established pattern of keeping all database access inside that one module.
- `tests/test_db.py` — this file is no longer untouched (see the corrected
  Do-not-modify note below). It gains: a `fetch_resource_addresses` integration test
  against a **temporary repo/resources setup** created and torn down inside the test
  (finding 3, section 3.3 — never a lookup by a real repo's name); a `fetch_repo`
  round-trip test (`insert_repo` then `fetch_repo` returns the same tuple back, and
  `fetch_repo` on a nonexistent id returns `None`); both DB-dependent, both
  skip-if-unreachable, same convention as every prior day.

Do not modify: `SPEC.md`, `sql/schema.sql`, `docker-compose.yml`, `.env`/`.env.example`,
`requirements.txt` (no new dependency required — `json`, `dataclasses`,
`statistics.mean`, and `hashlib` are all standard library, and `GitPython` — the
single selected implementation for section 3.9's Git-revision lookup, finding 3 —
is already a dependency since Day 1's `scripts/index_repo.py`), `ripple/config.py`,
`ripple/ingest/*`, `ripple/llm/*`, `ripple/retrieval/*` (including `pipeline.py` —
section 3.7 explains why its BM25 injection gap is flagged, not fixed, this cycle),
`scripts/index_repo.py`, `scripts/ask.py`, `AGENTS.md`, `CLAUDE.md`, `README.md`.
**Every existing test file is untouched except `tests/test_db.py`** (see Modify
above) — this replaces the prior, now-inaccurate claim that every existing test file
was left alone.

## 5. Interfaces and data structures

```python
# ripple/evaluation/dataset.py
@dataclass
class BenchmarkEntry:
    id: str
    question: str
    expected: list[str]
    category: str   # one of "lookup" | "relational" | "blast_radius" | "attribute"

def load_benchmark(path: Path) -> list[BenchmarkEntry]: ...            # structural only
def validate_addresses_exist(entries: list[BenchmarkEntry], repo_id: int) -> None: ...  # DB
```

```python
# ripple/evaluation/metrics.py
def recall_at_k(expected: list[str], retrieved: list[str], k: int) -> float: ...
def precision_at_k(expected: list[str], retrieved: list[str], k: int) -> float: ...
def reciprocal_rank(expected: list[str], retrieved: list[str]) -> float: ...

@dataclass
class QuestionResult:
    entry_id: str
    category: str
    expected: list[str]
    retrieved: list[str]
    recall_at_5: float
    recall_at_10: float
    reciprocal_rank_value: float
    precision_at_5: float
    latency: dict[str, float]
    # Full PipelineResult.latency_json for this question, e.g.
    # {"vector_query_ms": .., "total_ms": ..} -- keys present depend on which
    # stages executed for this config (section 3.6). Never reduced to a single
    # float here.

@dataclass
class AggregateMetrics:
    question_count: int
    recall_at_5: float
    recall_at_10: float
    mrr: float
    precision_at_5: float
    mean_latency_ms: float
    mean_latency_by_stage: dict[str, float]
    # Mean of every latency key, computed across ALL QuestionResults in this
    # aggregate. Every QuestionResult passed to aggregate()/aggregate_by_category()
    # (within one category) must have an identical set of `latency` keys -- raises
    # ValueError if they don't (finding 4, section 3.6). A key absent from every
    # question's latency dict (because that config never ran that stage) is simply
    # absent here too -- never a synthesized 0.0. Different ConfigResults may
    # legitimately have different key sets here, since each is aggregated
    # independently.

@dataclass
class CategoryMetrics(AggregateMetrics):
    category: str

def score_question(entry: BenchmarkEntry, retrieved: list[str], latency: dict[str, float]) -> QuestionResult: ...
def aggregate(results: list[QuestionResult]) -> AggregateMetrics: ...
def aggregate_by_category(results: list[QuestionResult]) -> list[CategoryMetrics]: ...
```

```python
# ripple/evaluation/runner.py
@dataclass
class ConfigResult:
    config_name: str
    config: RetrievalConfig
    per_question: list[QuestionResult]
    aggregate: AggregateMetrics
    by_category: list[CategoryMetrics]

def run_benchmark(
    repo_id: int,
    entries: list[BenchmarkEntry],
    config: RetrievalConfig,
    config_name: str,
) -> ConfigResult: ...
# No embedder parameter: run_benchmark calls pipeline.run_pipeline(repo_id,
# entry.question, config) with no embedder override, so every call uses
# run_pipeline's own default OpenAIEmbeddingProvider() -- the same uncached path
# production ask() already uses. Tests mock pipeline.run_pipeline itself (Day 6's
# established pattern), so run_benchmark never needs its own injection point.

# Explicit, deterministic config names, matching section 10.3's table row labels
# character-for-character. use_rerank/use_graph/use_rewrite are set False on every
# row here even though RetrievalConfig defaults them True -- pipeline.py doesn't
# read them yet (Day 12/13/15), but setting them explicitly now means these three
# configs stay "vector/BM25/RRF only" even after those stages are wired in later,
# rather than silently picking up reranking/graph/rewrite the day pipeline.py starts
# reading them. final_k=10 is set explicitly on every row (finding 1 / section 3.6):
# RetrievalConfig's production default is final_k=8, which would make Recall@10
# structurally invalid (PipelineResult.blocks could never hold 10 items). This is an
# evaluation-only override -- ripple/config.py's default is unchanged. vector_k=30
# and bm25_k=30 are also unchanged from RetrievalConfig's own defaults.
ABLATION_CONFIGS: list[tuple[str, RetrievalConfig]] = [
    ("Vector only", RetrievalConfig(
        use_vector=True, use_bm25=False, use_rrf=False,
        use_rerank=False, use_graph=False, use_rewrite=False,
        final_k=10,
    )),
    ("Vector + BM25", RetrievalConfig(
        use_vector=True, use_bm25=True, use_rrf=False,
        use_rerank=False, use_graph=False, use_rewrite=False,
        final_k=10,
    )),
    ("Vector + BM25 + RRF", RetrievalConfig(
        use_vector=True, use_bm25=True, use_rrf=True,
        use_rerank=False, use_graph=False, use_rewrite=False,
        final_k=10,
    )),
]

GIT_REVISION_UNAVAILABLE = "unavailable"

def _corpus_git_revision(local_path: str) -> str:
    # Single selected implementation: GitPython, with search_parent_directories=True
    # (finding 3) -- not left as an "either GitPython or subprocess" choice. This
    # matters concretely: the indexed repo's local_path is frequently a *nested*
    # subdirectory of the actual git checkout (e.g. this project's own corpus,
    # .repos/terraform-aws-vpc/examples/complete, sits inside the
    # .repos/terraform-aws-vpc clone) -- without search_parent_directories=True,
    # GitPython only looks for a .git directory directly inside local_path itself
    # and would incorrectly report "unavailable" for every nested corpus path this
    # project actually uses. Best-effort only -- provenance metadata must never
    # crash a real run: wrapped broadly so a missing directory, a directory with no
    # enclosing git repository at all, a repository with zero commits, or GitPython
    # not being importable all fall back to GIT_REVISION_UNAVAILABLE rather than
    # raising out of build_report.
    try:
        import git
        return git.Repo(local_path, search_parent_directories=True).head.commit.hexsha
    except Exception:
        return GIT_REVISION_UNAVAILABLE

def _indexed_corpus_fingerprint(repo_id: int) -> tuple[str, int]:
    # Returns (indexed_corpus_sha256, resource_count) computed from the *database
    # rows actually indexed* for repo_id -- not the working tree, and not row IDs
    # (finding 2 / section 3.9). This proves what data these specific numbers were
    # actually computed against, which corpus.git_revision alone cannot: git_revision
    # only describes what commit local_path has checked out on disk right now: it
    # says nothing about whether the database was ever indexed from that revision,
    # or from an earlier one, or only partially.
    rows = db.fetch_resource_bodies(repo_id)                     # (id, address, body)
    pairs = sorted((address, body) for _id, address, body in rows)
    # `id` is deliberately excluded -- an insertion-order artifact, not part of
    # corpus identity. `resources` has a UNIQUE (repo_id, address) constraint, so
    # sorting by address alone is already a total order (no ties possible) --
    # sorting is what makes fetch order irrelevant to the resulting hash.
    canonical = json.dumps(pairs, separators=(",", ":"))
    # Fixed separators (no incidental whitespace) make the serialization
    # byte-for-byte reproducible; sorting the input, not the JSON text, is what
    # actually guarantees a stable hash independent of database fetch order.
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return digest, len(pairs)

def build_report(
    repo_id: int,
    benchmark_path: str,
    benchmark_sha256: str,
    results: list[ConfigResult],
) -> dict:
    # Assembles the full provenance-carrying report (section 3.9). Calls the new
    # db.fetch_repo(repo_id) for corpus identity/local_path, _corpus_git_revision
    # (local_path) for the Git revision, and _indexed_corpus_fingerprint(repo_id)
    # for the indexed-corpus hash/count (finding 2). Never reads os.environ directly
    # and never includes anything from it in the returned dict -- the only per-run
    # facts in the report are repo_id, benchmark_path/hash, corpus identity,
    # embedding_model, and the ConfigResults themselves (finding 7's secrets-exclusion
    # requirement).
    ...
```

`PipelineResult.blocks` is consumed only as `[b.address for b in result.blocks]`; the
**full** `latency_json` mapping is passed through to `score_question` and stored on
`QuestionResult.latency` unchanged (section 3.6) — never reduced to `total_ms` before
that point. `result.config_json`/`stages_json` are not consumed by the runner at all —
they exist for `query_logs` (Day 6), not for benchmark scoring, and `run_benchmark`
does not call `db.insert_query_log` either (that would write 40–120 log rows per run
for no benefit; evaluation and logged production queries are different concerns).
`build_report` (above) is the only place provenance assembly (`db.fetch_repo`, Git
revision, benchmark hashing) happens — `run_benchmark` itself stays focused purely on
scoring one config and knows nothing about provenance.

```python
# ripple/db.py addition (finding 7)
def fetch_repo(repo_id: int) -> tuple[str, str | None, str] | None:
    # Returns (name, source_url, local_path) for repo_id, or None if it doesn't
    # exist. SELECT name, source_url, local_path FROM repos WHERE id = %s.
    ...
```

```python
# scripts/run_eval.py (sketch)
def main(argv=None):
    args = parse_args(argv)   # --repo-id (required), --benchmark (default data/benchmark.json),
                               # --config NAME (optional; omit = all three), --yes
    benchmark_path = Path(args.benchmark)   # argparse gives a str; normalize to Path
                                             # exactly once and reuse it everywhere below --
                                             # load_benchmark's signature takes a Path,
                                             # never a bare string (finding 5)
    benchmark_bytes = benchmark_path.read_bytes()
    benchmark_sha256 = hashlib.sha256(benchmark_bytes).hexdigest()       # finding 7
    entries = load_benchmark(benchmark_path)
    validate_addresses_exist(entries, args.repo_id)      # fail fast, before spending anything
    configs = [(args.config, dict(ABLATION_CONFIGS)[args.config])] if args.config else ABLATION_CONFIGS
    confirm_cost(len(entries), len(configs), skip=args.yes)   # section 3.7 -- real, uncached counts
    results = [run_benchmark(args.repo_id, entries, cfg, name) for name, cfg in configs]
    print(render_markdown_table(results))
    report = build_report(                                # finding 7 -- provenance + results
        repo_id=args.repo_id,
        benchmark_path=str(benchmark_path),   # build_report's field is a JSON string,
                                               # not a Path -- converted once, here
        benchmark_sha256=benchmark_sha256,
        results=results,
    )
    path = timestamped_path()             # UTC, microsecond precision -- section 3.8
    path.parent.mkdir(parents=True, exist_ok=True)  # data/eval_results/ does not exist
                                                     # yet -- git doesn't track empty
                                                     # directories -- create it, idempotently,
                                                     # before the exclusive-create open below
    with path.open("x") as f:             # exclusive create -- raises FileExistsError
        json.dump(report, f, indent=2, default=dataclasses.asdict)  # rather than overwriting (finding 5)
    print(f"Wrote {path}")
    # Staging/committing this file into git is a separate, deliberate, manual review
    # step (section 3.8) -- never done automatically here.
```

### 3.8 Output behavior

- **Markdown**: always printed to stdout, in section 10.3's exact column order
  (`Configuration | Recall@5 | Recall@10 | MRR | P@5 | Latency (ms)`), plus a
  per-category breakdown table beneath it. `Latency (ms)` is the mean **total**
  latency only — section 3.6's richer per-stage detail lives in JSON, not the table,
  since SPEC 10.3's table has exactly one latency column. Not auto-written into
  `README.md` — that stays a manual, deliberate copy-paste step for whichever day
  writes the README (Day 19), never silently automated here.
- **JSON: exactly one report file per `scripts/run_eval.py` invocation, whatever
  number of configs it ran (finding 5).** There is no "one file or three" choice —
  a single `--config NAME` run writes one file whose `results` list holds **one**
  `ConfigResult`; a run with no `--config` (all three `ABLATION_CONFIGS`) writes one
  file whose `results` list holds all **three**. The report is always the single
  schema in section 3.9, regardless of how many entries its `results` list holds — no
  caller ever has to guess whether a given file holds one row or three.
- **Filenames are collision-resistant, and creation is exclusive, so nothing is ever
  silently overwritten (finding 5):** `data/eval_results/
  <UTC-timestamp-with-microseconds>.json` (e.g.
  `data/eval_results/2026-08-28T14-30-00-123456Z.json`) — **microsecond** precision,
  not second precision, because two runs started in quick succession (e.g. a Day 11
  re-run immediately after fixing a bug, per section 6's Step 3) could otherwise
  collide on the same second-precision filename. **`data/eval_results/` does not
  exist in the repository yet** — git doesn't track empty directories, and nothing
  has been committed into it before — so `scripts/run_eval.py` must create it before
  its first write: `path.parent.mkdir(parents=True, exist_ok=True)`, called every
  run, immediately before opening the file (idempotent — a no-op on every run after
  the first, once the directory and any committed results already exist). The file
  itself is then opened with Python's **exclusive-create mode** (`path.open("x")`) —
  which *raises* `FileExistsError` rather than silently truncating/overwriting an
  existing file — so even a genuine timestamp collision fails loudly instead of
  quietly destroying a prior run's evidence. Once it exists, `data/eval_results/` is
  **not** gitignored — it's a normal, tracked directory — but writing the file and
  committing it are two separate, deliberate actions, not one:
  - `scripts/run_eval.py` only ever **writes the file to disk**. It never runs `git
    add`/`git commit` itself, and every run produces a file whether the results turn
    out to be exactly what was expected or not.
  - **Only inspected, accepted results get committed**: after a run, look at the
    numbers (Day 11, Step 2's "investigate anything surprising" applies here
    directly), and only once they're judged correct and final for that milestone do
    you `git add data/eval_results/<that specific timestamped file>.json` and commit
    it as part of that day's commit (section 7). A failed run, a run interrupted by a
    bug fix partway through, or an exploratory run made while debugging something
    unrelated is left as an uncommitted (or manually deleted) local file — never
    swept in with a blanket `git add data/eval_results/`.
  - Concretely, across this plan's own milestones: the accepted Day 10 run writes one
    file with one `ConfigResult`, committed with Day 10's commit; the accepted Day 11
    run writes one file with three `ConfigResult`s, committed with Day 11's commit.
    Any earlier attempts at either that didn't make the cut are not part of either
    commit.
  - This is a documented **process** step (section 6/7), not something
    `scripts/run_eval.py`'s code enforces beyond refusing to silently overwrite — the
    script cannot know which run you've decided to accept; that judgment is exactly
    what "investigate anything surprising before proceeding" (SPEC.md, Day 11)
    requires a human for.

### 3.9 Reproducibility provenance

Every accepted JSON report (section 3.8) is not just numbers — it's evidence that a
specific run of specific code, against a specific corpus state, produced those
numbers. `build_report` (section 5) assembles one top-level JSON object per
invocation:

```json
{
  "schema_version": 1,
  "generated_at": "2026-08-28T14:30:00.123456Z",
  "repo_id": 13,
  "benchmark_path": "data/benchmark.json",
  "benchmark_sha256": "<sha256 of the exact benchmark file bytes used for this run>",
  "corpus": {
    "repo_name": "vpc-complete",
    "source_url": null,
    "local_path": ".repos/terraform-aws-vpc/examples/complete",
    "git_revision": "<HEAD commit hash, or \"unavailable\">",
    "indexed_corpus_sha256": "<sha256 of the sorted (address, body) pairs actually stored under repo_id>",
    "resource_count": 114
  },
  "embedding_model": "text-embedding-3-small",
  "question_count": 40,
  "results": [
    {
      "config_name": "Vector only",
      "config": { "...": "dataclasses.asdict(RetrievalConfig) -- every field" },
      "aggregate": { "...": "AggregateMetrics, including mean_latency_by_stage" },
      "by_category": [ "...": "CategoryMetrics per category" ],
      "per_question": [ "...": "QuestionResult per entry, including the full latency dict" ]
    }
  ]
}
```

- `repo_id`, `benchmark_path`, and `benchmark_sha256` are **never hardcoded anywhere
  in application code** — `repo_id` comes from the CLI argument (section 3.1),
  `benchmark_path` from the CLI's `--benchmark` default or override, and
  `benchmark_sha256` is computed fresh, at runtime, from the exact bytes read for
  that invocation.
- **`corpus.git_revision` is derived at runtime, never hardcoded.** `db.fetch_repo`
  (new function, section 4) returns the repo's own `local_path` column; `runner.py`'s
  `_corpus_git_revision(local_path)` (section 5) resolves that path's current `HEAD`
  commit via `GitPython` — `git.Repo(local_path, search_parent_directories=True)
  .head.commit.hexsha` — the single selected implementation (finding 3), not left as
  a choice between `GitPython` and a `subprocess` call. `search_parent_directories=
  True` is required, not optional: `local_path` is frequently a nested subdirectory
  of the actual git checkout (this project's own corpus, `.repos/terraform-aws-vpc/
  examples/complete`, sits inside the `.repos/terraform-aws-vpc` clone), and without
  it GitPython would only look for a `.git` directory directly inside `local_path`
  itself, incorrectly reporting `"unavailable"` for every nested corpus this project
  actually uses. `GitPython` is already a project dependency since Day 1's
  `scripts/index_repo.py` — no new dependency is introduced. If `local_path` no
  longer exists, has no enclosing Git repository at all, has zero commits, or
  `GitPython` isn't importable, this reports the literal string `"unavailable"`
  rather than crashing the run — provenance metadata failing to resolve is not a
  reason to lose an otherwise-valid evaluation result.
  `EMBEDDING_MODEL` is imported from `ripple.llm.embeddings` (currently
  `"text-embedding-3-small"`), never re-typed as a string literal in `runner.py`, so
  it can't silently drift out of sync if the constant ever changes.
- **`indexed_corpus_sha256`/`resource_count` are a separate fingerprint from
  `git_revision`, and both are kept because they prove different things (finding
  2).** `git_revision` describes what commit `local_path` has **checked out on
  disk** right now — a fact about the filesystem. `indexed_corpus_sha256` and
  `resource_count` (`_indexed_corpus_fingerprint`, section 5) describe what rows are
  **actually stored in the database** under `repo_id` right now — a fact about the
  database. These can genuinely diverge: `local_path` could be re-checked-out to a
  different commit after indexing finished, the database could hold a stale or
  partial index left over from an earlier, interrupted run, or someone could
  re-index only part of the corpus by hand. Recording both closes that gap: a reader
  of the report can tell not just "what commit exists on disk" but "what data these
  specific numbers were actually computed against." The fingerprint is computed at
  evaluation runtime, every run, from `db.fetch_resource_bodies(repo_id)`'s
  `(address, body)` pairs only — never database row IDs, which are an
  insertion-order artifact, not part of corpus identity — sorted by address (a total
  order, since `resources` has a `UNIQUE (repo_id, address)` constraint) before
  canonical-JSON serialization (`separators=(",", ":")`, no incidental whitespace)
  and SHA-256 hashing. Sorting before serializing is what makes the hash identical
  regardless of the order the database happens to return rows in; hashing `(address,
  body)` and nothing else is what makes it change if either an address or a body
  changes, and stay unchanged for anything else (row ID, insertion order, unrelated
  columns).
- **Every result row carries its own complete, serialized `RetrievalConfig`**
  (`dataclasses.asdict(result.config)`) — so a reader of the JSON file never has to
  cross-reference `ABLATION_CONFIGS`' current source code to know exactly what
  configuration produced a given row, even if that source later changes.
- **No secrets appear anywhere in this structure.** `build_report` only ever reads
  from its explicit parameters, `db.fetch_repo`'s return value, and
  `ripple.llm.embeddings.EMBEDDING_MODEL` — it never touches `os.environ` and never
  serializes anything sourced from `.env` (`DATABASE_URL`, `OPENAI_API_KEY`). This is
  verified by a dedicated test (section 6, Day 10) that sets a fake secret in the
  environment and asserts it doesn't appear anywhere in a report built from fake data.
- Tests (section 6, Day 10) also cover: deterministic hashing of the benchmark file
  (same file bytes hashed twice match; different content doesn't); correct
  `RetrievalConfig` serialization; `_corpus_git_revision` against a real temporary
  Git repo (including from a nested subdirectory inside it, finding 3), and a
  non-Git directory; `_indexed_corpus_fingerprint`'s row-order independence,
  address-change and body-change sensitivity, and correct `resource_count` (finding
  2); the collision-safe write behavior and the first-write directory-creation
  behavior (finding 5/6); and that a three-config run's report has exactly one file
  with three `results` entries.

## 6. Day-by-day plan

### Day 8 — first 20 questions, `dataset.py`

**Step 1** — `ripple/evaluation/dataset.py`: `BenchmarkEntry`, `load_benchmark`
(structural validation, section 3.3), `validate_addresses_exist` (DB validation,
section 3.3), plus `db.fetch_resource_addresses` (section 4).

**Step 2** — Author 20 questions against the real corpus, using section 3.4's
workflow, following section 3.2's labeling policy exactly. Rough mix (SPEC's target
is 15/10/8/7 across all 40 — Day 8's 20 don't need to hit that ratio individually,
but avoid making Day 8 all-`lookup`, since Day 9 is explicitly meant to weight toward
`relational`/`blast_radius` on top of whatever Day 8 already has). For each entry:
resolve the real address(es) via the inventory workflow, read the actual source
(semantic verification, section 3.3), then write the entry.

**Step 3** — Run `load_benchmark` + `validate_addresses_exist` against the real
`repo_id` for all 20; fix anything that fails.

**Tests**: `tests/test_dataset.py` — structural validation (pure, offline): valid
minimal entry accepted; missing/wrong-typed field rejected per field; duplicate `id`
rejected; invalid `category` rejected; empty `expected` rejected; duplicate address
*within* one entry's `expected` rejected; non-array top-level JSON rejected — each as
its own small, targeted test using an inline JSON string or a `tmp_path`-written
file, not the real 20-entry file (keeps these tests fast, offline, and independent of
how many real entries currently exist). `validate_addresses_exist` logic test
(offline, **monkeypatching `db.fetch_resource_addresses`** to a fixed fake address
set — no real database, section 3.3): a known-bad address is reported missing; and
**multiple** missing `(entry_id, address)` pairs across multiple entries are **all**
reported in one failure, not just the first. `tests/test_db.py` addition:
`fetch_resource_addresses` **integration** test against a temporary repo/resources
setup created and torn down inside the test (DB-dependent, skip-if-unreachable, same
convention as every prior day — never a lookup by a real repo's name like
`vpc-complete`, per section 3.3).

**Manual acceptance command** (not a `pytest` test — section 3.3): after authoring all
20 entries, verify them against the real, indexed corpus, run via `"$PYTHON_BIN" -c
"..."` or a scratch file (section 0):
```python
from pathlib import Path

from ripple.evaluation.dataset import load_benchmark, validate_addresses_exist

REPO_ID = ...  # resolved per section 3.1, never hardcoded
entries = load_benchmark(Path("data/benchmark.json"))  # load_benchmark takes a Path,
                                                         # never a bare string
validate_addresses_exist(entries, REPO_ID)  # raises if anything is missing
print(f"{len(entries)} entries, all addresses verified against repo_id={REPO_ID}")
```
This is the actual, required check that today's 20 real addresses exist in the real
database — it just isn't automated, for the portability reason section 3.3 explains.

**Acceptance**: 20 entries in `data/benchmark.json`; `load_benchmark` passes (pytest);
the manual acceptance command above passes against the real corpus; every entry has
had its source manually read and confirmed (section 3.3) — this is the "semantically
verified" half of Day 8's "Done when," and it's a checklist a test suite cannot
certify for you.

### Day 9 — 20 more, to 40 total

**Step 1** — Author 20 more questions, this time deliberately weighted toward
`relational`/`blast_radius` so the running total approaches SPEC's 15/10/8/7 target.
Same process as Day 8 (section 3.4 workflow, section 3.2 policy, manual source
verification per entry).

**Step 2** — Re-run the Day 8 manual acceptance command against all 40 entries.

**Tests**: no new *test code* is required beyond Day 8's (the validator logic doesn't
change, and the manual acceptance command is re-run, not re-written). It's worth
adding one small, offline, pure structural test in `tests/test_dataset.py` that reads
the real `data/benchmark.json` directly (no database) and asserts the final category
distribution stays within a loose bound (e.g. `assert counts["relational"] >= 8`), so
a future accidental edit to `benchmark.json` that skews the mix back toward
all-`lookup` gets caught by `pytest` without needing a database connection.

**Acceptance**: 40 entries total; validator passes; every entry semantically
verified; category counts close to 15/10/8/7 (exact SPEC wording is "aim for
roughly," not an exact requirement — don't force it if reality lands at, say,
14/11/8/7).

### Day 10 — metrics, runner, first real row

**Step 1** — `ripple/evaluation/metrics.py`: the three SPEC 10.2 functions plus
section 3.6's edge-case guards and per-stage latency aggregation policy (finding 2),
`QuestionResult`/`AggregateMetrics`/`CategoryMetrics` (now carrying per-question
latency dicts and per-stage aggregate means — section 5), `score_question`/
`aggregate`/`aggregate_by_category`.

**Step 2** — `ripple/db.py`: add `fetch_repo(repo_id)` (finding 7, section 4).

**Step 3** — `ripple/evaluation/runner.py`: `ConfigResult`, `run_benchmark`,
`ABLATION_CONFIGS` (section 5, each entry now with `final_k=10` explicit per finding
1/section 3.6), `_corpus_git_revision` (finding 3 — GitPython,
`search_parent_directories=True`), `_indexed_corpus_fingerprint` (finding 2),
`build_report` (section 3.9 — assembles the provenance-carrying report, calling the
new `db.fetch_repo`). `run_benchmark` calls
`pipeline.run_pipeline` with no `embedder` override — every call uses the pipeline's
own default, uncached `OpenAIEmbeddingProvider()` (section 3.7) — and passes the
**full** `result.latency_json` mapping through to `score_question` unchanged
(section 3.6).

**Step 4** — `scripts/run_eval.py`: CLI, cost confirmation gate, benchmark hashing,
markdown output, and the single exclusively-created, microsecond-timestamped JSON
report (sections 3.8, 3.9).

**Step 5** — First real, paid run: **confirm before this step** (section 3.7). One
config only — `"Vector + BM25 + RRF"` is the natural choice (it's the pipeline's
current full capability). ~40 embedding requests, zero generation calls.

**Tests** (offline unless noted):
- `metrics.py`: hand-computed values for `recall_at_k`/`precision_at_k`/
  `reciprocal_rank` against small constructed `expected`/`retrieved` lists (including
  a case where `retrieved` has fewer than `k` items, and a no-match case); `k <= 0`
  raises for both `recall_at_k` and `precision_at_k`; empty `expected` raises for
  `recall_at_k`; `aggregate([])` raises; `aggregate_by_category` returns categories in
  sorted order regardless of input order; a mixed-category input produces correct
  per-category means (hand-computed); **per-stage latency aggregation** (finding 4):
  hand-computed `mean_latency_by_stage` across a set of `QuestionResult`s whose
  `latency` dicts all share the **same** key set (e.g. all having
  `{"vector_query_ms", "bm25_ms", "fusion_ms", "total_ms"}`), confirming the mean is
  correct for every key and that a key absent from that whole set (e.g.
  `rerank_ms`) never appears in the aggregate, never backfilled with `0.0`;
  `aggregate` **raises `ValueError`** when the input `QuestionResult`s have
  **mismatched** `latency` key sets (e.g. one question's dict has `bm25_ms`, another
  in the same call doesn't); two separate `aggregate()` calls, each internally
  consistent but with **different** key sets from each other (simulating two
  different `ABLATION_CONFIGS` entries), both succeed independently — proving the
  consistency rule applies within one aggregate call, never across configs;
  `aggregate_by_category` applies the same consistency check independently within
  each category's own group of `QuestionResult`s.
- `runner.py`: `run_benchmark` with `pipeline.run_pipeline` **monkeypatched** (module
  level, matching Day 6's established pattern for `test_pipeline.py`) to return
  canned `PipelineResult`s keyed by question — never a real database, never a real
  `OPENAI_API_KEY`. Assert: `retrieved` passed into `score_question` matches
  `[b.address for b in canned_result.blocks]` in the same order; `QuestionResult.latency`
  equals `canned_result.latency_json` **unchanged** (finding 2 — not reduced to
  `total_ms`); **a dedicated test asserting `answer_question` is never
  imported/called** by monkeypatching `ripple.llm.generate.answer_question` to raise
  if invoked, then running `run_benchmark` end to end and confirming it never fires
  (the direct test for section 3.5's independence requirement); **a config test**
  (finding 1) asserting every entry in `ABLATION_CONFIGS` has `final_k >= 10`; **a
  10-block preservation test** (finding 1) using a canned `PipelineResult` with 10
  `RetrievedBlock`s, confirming all 10 addresses survive into
  `QuestionResult.retrieved` with nothing along the way silently truncating below 10.
- `runner.py` — `build_report` (finding 7): benchmark hashing is deterministic (the
  same file bytes hashed twice match; different content produces a different hash);
  `RetrievalConfig` serializes correctly via `dataclasses.asdict` (spot-check a few
  fields); the assembled report contains no secret values — a test sets a fake
  `OPENAI_API_KEY`/`DATABASE_URL` in the environment, builds a report from fake data,
  and asserts neither fake value appears anywhere in the serialized JSON string;
  `_corpus_git_revision` returns the real `HEAD` commit hash for a temporary
  directory that **is** a real Git repo — created inside the test via `git init` in
  `tmp_path`, followed by **repository-local** identity configuration (`git config
  user.name "Ripple Tests"` and `git config user.email "ripple-tests@example.invalid"`
  run inside that temp repo, never relying on the developer machine's global Git
  config, which may not be set at all in CI) and one commit; a **second test**
  creates a subdirectory *inside* that same git-initialized `tmp_path` (e.g.
  `tmp_path / "nested" / "subdir"`) and passes that nested path to
  `_corpus_git_revision`, asserting it returns the **same** commit hash — the direct
  test that `search_parent_directories=True` actually finds the enclosing repository
  from a nested working path, matching this project's own corpus layout; and a
  **third test** asserts `_corpus_git_revision` returns `GIT_REVISION_UNAVAILABLE`
  for a `tmp_path` that is **not** a Git repo at all.
- `runner.py` — `_indexed_corpus_fingerprint` (finding 2, offline: monkeypatches
  `db.fetch_resource_bodies` to fixed fake `(id, address, body)` rows, no real
  database): the same set of rows returned in **two different orders** produces the
  **same** `indexed_corpus_sha256` (row-order independence); changing one row's
  `body` (same address, same id) changes the hash; changing one row's `address`
  (same body, same id) changes the hash; changing only a row's `id` (same address,
  same body) does **not** change the hash (proving row IDs are correctly excluded);
  `resource_count` equals the number of rows given; `build_report`'s assembled
  report contains both `corpus.indexed_corpus_sha256` and `corpus.resource_count`.
- `scripts/run_eval.py` — argument-parsing test (monkeypatch `run_benchmark` to a
  stub, assert the right `repo_id`/config selection reaches it); confirmation-gate
  test (declining the `y` prompt makes no calls to `run_benchmark` at all); **output
  test** (finding 5): a single-config run writes exactly one file whose `results`
  list has one entry, an all-three-configs run writes exactly one file whose
  `results` list has three; **first-write directory-creation test** (finding 6):
  pointing the output path at a `tmp_path` subdirectory that does **not** exist yet,
  running the write path, and asserting the directory is created, exactly one JSON
  file is written inside it, and that file's contents parse back into the expected
  top-level report keys (section 3.9); **collision-safe write test** (finding 5):
  monkeypatching the timestamp source to return the same value twice and asserting
  the second write attempt raises `FileExistsError`, leaving the first file's
  content untouched — all offline, no real API/DB.
- `tests/test_db.py` additions (finding 4): `fetch_resource_addresses` **integration**
  test against a temporary repo/resources setup created and torn down inside the test
  (DB-dependent, skip-if-unreachable, section 3.3); `fetch_repo` round-trip test —
  `insert_repo` followed by `fetch_repo` returns the same `(name, source_url,
  local_path)`, and `fetch_repo` on a nonexistent id returns `None` (DB-dependent,
  skip-if-unreachable).

**Acceptance**: `"$PYTHON_BIN" -m pytest` (section 0) passes (existing 123 plus this
cycle's new tests — see section 10 for why an exact new total isn't quoted); one
real, confirmed run of `"$PYTHON_BIN" scripts/run_eval.py --repo-id <resolved>
--config "Vector + BM25 + RRF"` produces a
real Recall@5/MRR row with full per-stage latency and provenance metadata — including
the indexed-corpus fingerprint (`indexed_corpus_sha256`/`resource_count`) and the
Git revision, section 3.9 — printed and saved to exactly one exclusively-created,
microsecond-timestamped JSON file (section 3.8, its parent directory created on
first write per finding 6) containing that one `ConfigResult`, using the real,
resolved `repo_id` (never `13` hardcoded anywhere in the command's own script — only
ever passed as a CLI argument at invocation time).

### Day 11 — first three ablation rows

**Step 1** — Run `"$PYTHON_BIN" scripts/run_eval.py --repo-id <resolved>` (section 0)
**without** `--config` (runs all three `ABLATION_CONFIGS` rows). **Confirm before
this step** — ~120
embedding requests total (3 configs × 40 questions, uncached — section 3.7), zero
generation calls.

**Step 2** — Read the three rows. **Investigate anything surprising before treating
it as a finding** — SPEC's own words: "a suspicious number now is a bug, not a
finding." Concretely: if "Vector + BM25" scores *worse* than "Vector only," or if
`use_rrf=False`'s concat/dedup path produces identical results to plain vector-only,
that's worth checking against `fusion.concat_dedup`'s actual behavior (Day 6) before
writing it down as a real result.

**Step 3** — Do not hand-edit any number. If a bug is found, fix it, re-run the whole
three-row set from scratch (not just the one row that looked wrong — a bug found in
one row may have silently affected the others too), and only then treat the numbers
as final for this cycle.

**Tests**: none new — Day 11 exercises Day 10's already-tested machinery across three
configs; there's no new *code* to unit test, only a new *run* to produce and read
correctly.

**Acceptance**: three real rows exist, all inside the single JSON report file that
invocation produced (section 3.8 — a `--config`-less run always writes exactly one
file containing all three `ConfigResult`s; there is no ambiguity about file count),
each with full per-stage latency and provenance metadata — including the
indexed-corpus fingerprint and Git revision, section 3.9 — each number traceable to
an actual run, and you can explain each row (section 11's own "Done when": "three
rows exist and you can explain each one").

## 7. Practical execution order (the actual collaboration loop)

For each step listed under Days 8–11 above:
1. This plan (or a short follow-up message) explains the step.
2. You decide: you implement it, or Codex does.
3. Implement and run the relevant tests for just that step.
4. Review the diff before moving to the next step — small, reviewable changes, not
   one accumulated diff across a whole day.
5. Commit at the **end of each completed day** (Day 8's commit, Day 9's commit, Day
   10's commit, Day 11's commit) — four commits for this whole plan, not one.

## 8. Explicit non-goals

- **Reranking (`rerank.py`, `use_rerank`) — Day 12.** Not implemented, not enabled;
  every `ABLATION_CONFIGS` entry explicitly sets `use_rerank=False`.
- **Graph expansion wired into the pipeline (`use_graph`) — Day 13.** `graph.py`
  itself (Day 4) is used only as an *authoring aid* (section 3.4) — it is not called
  from `pipeline.py` or `runner.py`. Every config sets `use_graph=False`.
- **Query rewriting (`use_rewrite`) — Day 15.** Every config sets `use_rewrite=False`.
- **Rows 4–5 of the ablation table** (cross-encoder rerank, graph expansion) — not
  produced this cycle; section 10.3's table only gets its first three rows here.
- **Calling `answer_question` anywhere in evaluation** — section 3.5, with a
  dedicated test.
- **Writing generated answers to `query_logs` during evaluation** — evaluation and
  production query logging are different concerns; `run_benchmark` never calls
  `db.insert_query_log`.
- **Auto-updating `README.md`** — markdown output is printed for a human to use later
  (Day 19), not written into the README automatically.
- **A BM25-index-reuse interface change to `pipeline.py`** — flagged as a deferred
  decision in section 3.7, not made here.
- **Modifying `SPEC.md`, `sql/schema.sql`, or `docker-compose.yml`.**
- **Hardcoding any `repo_id`** in `data/benchmark.json`, application code, or
  `scripts/run_eval.py` itself — always a runtime parameter (section 3.1).

## 9. Security and process

- `.env` **may be read** — `ripple/db.py`, `ripple/llm/embeddings.py`, and
  `ripple/llm/generate.py` all call `load_dotenv()` at import time (established since
  Day 1), so any evaluation code that imports `ripple.db`/`ripple.retrieval.pipeline`
  transitively reads `.env` through this project's normal, already-established
  pattern — that is expected, and not a violation of anything. What this plan holds
  to exactly, like every prior day: `.env` is never **modified**, never **printed**,
  never **staged**, and never **committed** by anything in this plan, and no secret it
  contains (API keys, database credentials) ever appears in a result JSON file, a log
  line, a terminal summary, or a commit (section 3.9 covers the specific,
  test-verified guarantee for evaluation report JSON).
- No secrets (API keys, database credentials) appear in any committed file, log, or
  test output — the existing `python-dotenv` + environment-variable pattern is
  unchanged.
- Terraform repository content encountered while authoring questions or running
  retrieval is treated as data, never as instructions — unchanged from every prior
  day's posture, and newly relevant here only because question-authoring involves
  reading a lot of real `.tf` content by hand.
- No benchmark label or metric value is ever invented, smoothed, or hand-edited —
  section 6, Day 11, Step 3 is explicit about this.
- Any SPEC.md ambiguity or apparent bug encountered is flagged (section 11), never
  silently resolved by editing `SPEC.md`.

## 10. On test counts

Per the request, this plan does **not** guess an exact "before/after" test-count
number the way Days 5–7's plans did. Those cycles added a small, fully-enumerated set
of parametrized cases to *existing* test files, so the arithmetic was mechanical and
verifiable in advance. This cycle adds **three new test files** (`test_dataset.py`,
`test_metrics.py`, `test_runner.py`) plus targeted additions to one existing file
(`tests/test_db.py`, finding 4) whose exact test count depends on authoring choices
made during implementation (how many distinct edge cases `test_dataset.py` ends up
covering, exactly how `test_runner.py`'s per-stage-latency and provenance tests are
structured, etc.) — stating a precise number now would be a guess dressed up as a
fact. What's verifiable in advance: `"$PYTHON_BIN" -m pytest` (section 0) must show
**123 + (every new test this cycle adds)**, all passing, with zero regressions to the
existing 123.

## 11. Risks, ambiguities, and things flagged for your review

- **The `relational` vs. `blast_radius` subject-inclusion asymmetry (section 3.2) is
  this plan's judgment call, not a SPEC.md requirement.** SPEC gives a worked example
  for `blast_radius` only; `relational`'s exclusion-of-subject policy is inferred by
  analogy to natural language ("depends on" vs. "what breaks"), not quoted from
  SPEC.md. **Approved** — relational excludes the subject, blast_radius includes it —
  documented in `ripple/evaluation/dataset.py`, not in `data/benchmark.json` (JSON
  has no comment syntax, so nothing is proposed there).
- **`attribute` questions are the highest-effort category to get right**, because
  `expected` must be *exhaustive* over the whole corpus, not just complete for the
  examples someone happened to notice — section 3.2 and 3.4 both call this out, but
  it bears repeating: an incomplete `expected` set for an `attribute` question can
  make recall look better than it is, not worse.
- **Embeddings are deliberately left uncached across configurations (section 3.7).**
  A shared cache would make whichever configuration ran first pay real embedding
  latency while later configurations saw artificial cache-hit speed, making the
  latency column depend on run order rather than reflecting each configuration's
  real cost. `run_benchmark` uses `pipeline.run_pipeline`'s own default,
  uncached `OpenAIEmbeddingProvider()` — real counts: **~40 embedding requests for
  Day 10's one configuration, ~120 for Day 11's three.** Recorded as future work
  (section 3.7): reuse would need a separate `embedding_ms` measurement or a
  deliberate precompute-before-timing redesign, not a quiet cache.
- **The BM25 rebuild-per-call inefficiency (section 3.7) is real but deliberately not
  fixed this cycle**, for a different reason than the embedding case — it costs no
  API money and is fast, so there's no cost or latency-honesty motivation to change
  `pipeline.py` for it here. Flagged as a decision for you, not assumed away.
- **`data/eval_results/` is committed, not gitignored — but not automatically.**
  Every run writes a timestamped file locally; only a run whose numbers have been
  inspected and accepted gets `git add`ed and committed, as part of that milestone's
  commit (section 3.8, section 7). Failed, partial, debug, and exploratory runs stay
  local and uncommitted.
- **Exact category counts may not land exactly on 15/10/8/7** — SPEC's own wording is
  "aim for roughly," treated literally; forcing an exact ratio by padding with
  artificial questions would be worse than a close-but-imperfect real mix.
- **This plan assumes `examples/complete` remains the benchmark corpus** (matching
  every prior day's corpus choice) — SPEC section 5 does mention eventually adding
  the module root as a second, harder corpus, but that is explicitly out of scope
  for Days 8–11 and not part of this plan.
- **`RetrievalConfig.final_k` defaults to `8` in `ripple/config.py`, but SPEC 10.3
  requires a `Recall@10` column (section 3.6, finding 1).** This plan's own three
  ablation rows resolve this by setting `final_k=10` explicitly in
  `ABLATION_CONFIGS` — an evaluation-only override, not a change to the production
  default. The underlying tension is real and broader than this plan: any other
  caller of `pipeline.run_pipeline` that doesn't override `final_k` (e.g.
  `scripts/ask.py`'s own default) would produce a different, worse-looking Recall@10
  if measured against it. Flagged for your awareness; not resolved in `SPEC.md` or
  `ripple/config.py` by this plan, since that would be a broader specification
  decision, not a bug in Days 8–11's own code.
- **`corpus.git_revision` in the provenance report (section 3.9, finding 7) can
  legitimately be `"unavailable"`** — if the indexed repo's `local_path` no longer
  exists on disk, isn't a Git repository, or Git isn't installed in the environment
  running `scripts/run_eval.py`. This is a deliberate, honest fallback, not an error
  that blocks the run: provenance metadata failing to resolve is not a reason to
  discard an otherwise-valid evaluation result. If this happens on an accepted,
  committed report, it's worth noting in that day's commit message so the gap in
  reproducibility metadata is visible later, not silently buried in a JSON field.

**All three decisions previously flagged for sign-off remain resolved**, per your
explicit decisions from that round:
1. Labeling policy (section 3.2) — approved as written.
2. No `CachingEmbeddingProvider` — absent from this plan entirely (interfaces,
   pseudocode, tests, cost estimates, runner construction, acceptance criteria); cost
   estimates state the real, uncached request counts (**~40 embedding requests for
   Day 10's one configuration, ~120 for Day 11's three**).
3. `data/eval_results/` is committed, with a deliberate review-then-stage workflow
   rather than blanket auto-commit (section 3.8).

**This revision additionally resolves Codex's 8 final review findings**: Recall@10
validity via explicit `final_k=10` (finding 1, section 3.6); full per-stage latency
preservation instead of total-only (finding 2, sections 3.6/5); removal of the
real-corpus-named `pytest` coupling in favor of monkeypatched/temp-repo tests plus a
manual acceptance command (finding 3, sections 3.3/6); `tests/test_db.py` correctly
listed in file scope (finding 4, section 4); collision-safe, exclusively-created,
one-report-per-invocation output (finding 5, section 3.8); an accurate `.env`
statement (finding 6, section 9); a full reproducibility-provenance schema with
runtime Git-revision derivation and a new, justified `db.fetch_repo` helper (finding
7, sections 3.9/4/5); and a full audit removing the stale references those findings
identified while preserving every previously-approved decision (finding 8).

**This revision additionally resolves Codex's 6 follow-up findings** on top of the
data/eval_results/ directory-creation fix that preceded them: consistent
`"$PYTHON_BIN"`-based Python invocation everywhere in the plan, replacing every bare
`python`/`python3` reference and the false claim that bare `python -m pytest` is
portable (finding 1, section 0 and throughout); an `indexed_corpus_sha256`/
`resource_count` fingerprint of the database rows actually indexed under `repo_id`,
distinct from and complementary to `corpus.git_revision`, computed via
`_indexed_corpus_fingerprint` from `db.fetch_resource_bodies` with row-order-
independent, address/body-sensitive canonical hashing (finding 2, section 3.9/5);
`_corpus_git_revision` changed to the single selected `GitPython` implementation
with `search_parent_directories=True` so nested `local_path` corpora (this project's
own included) resolve correctly, plus a repo-local Git identity and a nested-
subdirectory test (finding 3, sections 3.9/5/6); `aggregate`/`aggregate_by_category`
changed from silently averaging over whichever questions happened to share a latency
key to **raising `ValueError`** on any latency-key mismatch within one aggregate,
computing every stage's mean over all questions once consistency is confirmed
(finding 4, section 3.6/5); the manual acceptance command and `run_eval.py` sketch
fixed to pass `load_benchmark` a `Path`, never a bare string (finding 5, sections
3.4/6); and an explicit offline test proving the output parent directory is created
on first write and exactly one correctly-structured report file results (finding 6,
section 6).

Everything else in this plan follows directly from SPEC.md's literal text or from
this project's own established conventions (Days 1–7). Day 8 can begin.
