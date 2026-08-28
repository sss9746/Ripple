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
illustrative only; resolve the real value independently in your own environment:
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
  categories is deliberate and should be stated explicitly wherever the labeling
  policy is documented** (a code comment at the top of `data/benchmark.json`'s
  authoring notes, or `dataset.py`'s docstring) so it's never "fixed" into
  inconsistency later by someone assuming both categories work the same way.
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
(plus the one new `db.py` function in section 4). Run these directly in a `python3`
REPL or scratch file while authoring, substituting the real `repo_id` from section
3.1:

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
block in result.blocks]` against `entry.expected`) and `result.latency_json[
"total_ms"]`. **It never calls `ripple.llm.generate.answer_question` anywhere.** This
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
request if not for the caching decision in section 3.7 — which is precisely why that
section exists.

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
| Per-category breakdown | grouped by `category`, categories emitted in **sorted order** | deterministic output — two runs over the same data always print categories in the same order. |

`retrieved` must always be the address list **in pipeline rank order** —
`[block.address for block in result.blocks]`, never re-sorted — since both
`reciprocal_rank` and every `top-k` slice depend on order being preserved exactly as
the pipeline produced it.

### 3.7 Cost and runtime — inspected, not assumed

**Simple version:** asking the AI "what does this text mean as a vector of numbers"
(an embedding) costs a little money each time. If we ran all 40 questions through
all 3 configurations in Day 11 without thinking about it, that's 120 separate
"what does this mean" calls for the *same 40 questions* asked 3 times over — wasteful,
since the question text doesn't change between configurations. So the same
embedding is computed once per question and reused for all 3 configurations.

**Technical version, and the actual inspection this section is asking for:**
`pipeline.run_pipeline(repo_id, question, config, embedder=None)` already accepts an
injectable `embedder` parameter (built in Day 3, unchanged since) — this plan uses
that **existing** injection point rather than changing `pipeline.py` at all.
`ripple/evaluation/runner.py` defines a small `CachingEmbeddingProvider` (wraps any
`EmbeddingProvider`, memoizes `.embed()` by exact input text) and constructs **one**
instance per `run_eval` invocation, passed into every `run_pipeline` call across every
config for the same benchmark run. Since embeddings only depend on question text —
never on which `RetrievalConfig` subsequently uses them — this is a pure cost
optimization with **zero change to retrieval semantics**. Effect: Day 11's naive cost
would be 3 configs × 40 questions = 120 embedding requests; with caching, it's 40 (one
per unique question, computed once, reused 3×).

**What this plan explicitly does *not* do, flagged as a deferred decision rather than
silently assumed:** `pipeline.run_pipeline` calls `build_index(repo_id)` (BM25)
**internally**, with no equivalent injection point — unlike the embedder, there is no
way to pass in an already-built `BM25Index` from outside. Reusing it across the ~80
calls that need BM25 in Day 11 (2 of 3 configs × 40 questions) **would require an
interface change to `pipeline.py`** (an optional `bm25_index=` parameter). This plan
does **not** make that change: rebuilding a 114-block BM25 index costs no API money
and is fast (pure local CPU, sub-second) — a real but minor runtime inefficiency, not
a cost concern, and not worth touching Day 6's `pipeline.py` for during this cycle.
If a future day wants that optimization, it's a one-parameter addition — surfaced
here as a decision for you to make, not assumed.

**Confirmation gate**: `scripts/run_eval.py` prints the number of configs, questions,
and estimated embedding requests, and requires an explicit `y` confirmation
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
- `ripple/db.py` — add **one** new function, `fetch_resource_addresses(repo_id) ->
  list[str]` (`SELECT address FROM resources WHERE repo_id = %s`). Justification: the
  existing `fetch_resource_bodies(repo_id)` returns `(id, address, body)` and *would*
  technically work for the validator's existence check, but it always pulls every
  block's full `body` text along with it — wasteful for a function whose only job is
  "does this address exist," and it's called potentially many times (once per
  `scripts/run_eval.py` invocation, plus every test run). A single-purpose, minimal
  read is the more honest fit, consistent with this project's existing pattern of
  narrow, purpose-built `db.py` functions (`fetch_bm25_documents` vs.
  `fetch_resource_bodies` is the same kind of split, already precedented).

Do not modify: `SPEC.md`, `sql/schema.sql`, `docker-compose.yml`, `.env`/`.env.example`,
`requirements.txt` (no new dependency — `json`, `dataclasses`, `statistics.mean` are
all standard library), `ripple/config.py`, `ripple/ingest/*`, `ripple/llm/*`,
`ripple/retrieval/*` (including `pipeline.py` — section 3.7 explains why its BM25
injection gap is flagged, not fixed, this cycle), `scripts/index_repo.py`,
`scripts/ask.py`, `AGENTS.md`, `CLAUDE.md`, `README.md`, and every existing test file.

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
    latency_ms: float

@dataclass
class AggregateMetrics:
    question_count: int
    recall_at_5: float
    recall_at_10: float
    mrr: float
    precision_at_5: float
    mean_latency_ms: float

@dataclass
class CategoryMetrics(AggregateMetrics):
    category: str

def score_question(entry: BenchmarkEntry, retrieved: list[str], latency_ms: float) -> QuestionResult: ...
def aggregate(results: list[QuestionResult]) -> AggregateMetrics: ...
def aggregate_by_category(results: list[QuestionResult]) -> list[CategoryMetrics]: ...
```

```python
# ripple/evaluation/runner.py
class CachingEmbeddingProvider:
    def __init__(self, inner: EmbeddingProvider) -> None: ...
    def embed(self, texts: list[str]) -> list[list[float]]: ...   # memoized by text

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
    embedder: EmbeddingProvider | None = None,
) -> ConfigResult: ...

# Explicit, deterministic config names, matching section 10.3's table row labels
# character-for-character. use_rerank/use_graph/use_rewrite are set False on every
# row here even though RetrievalConfig defaults them True -- pipeline.py doesn't
# read them yet (Day 12/13/15), but setting them explicitly now means these three
# configs stay "vector/BM25/RRF only" even after those stages are wired in later,
# rather than silently picking up reranking/graph/rewrite the day pipeline.py starts
# reading them.
ABLATION_CONFIGS: list[tuple[str, RetrievalConfig]] = [
    ("Vector only", RetrievalConfig(
        use_vector=True, use_bm25=False, use_rrf=False,
        use_rerank=False, use_graph=False, use_rewrite=False,
    )),
    ("Vector + BM25", RetrievalConfig(
        use_vector=True, use_bm25=True, use_rrf=False,
        use_rerank=False, use_graph=False, use_rewrite=False,
    )),
    ("Vector + BM25 + RRF", RetrievalConfig(
        use_vector=True, use_bm25=True, use_rrf=True,
        use_rerank=False, use_graph=False, use_rewrite=False,
    )),
]
```

`PipelineResult.blocks` is consumed only as `[b.address for b in result.blocks]`;
`latency_json` only as `result.latency_json["total_ms"]`. `result.config_json`/
`stages_json` are not consumed by the runner at all — they exist for `query_logs`
(Day 6), not for benchmark scoring, and `run_benchmark` does not call
`db.insert_query_log` either (that would write 40–120 log rows per run for no
benefit; evaluation and logged production queries are different concerns).

```python
# scripts/run_eval.py (sketch)
def main(argv=None):
    args = parse_args(argv)   # --repo-id (required), --benchmark (default data/benchmark.json),
                               # --config NAME (optional; omit = all three), --yes
    entries = load_benchmark(args.benchmark)
    validate_addresses_exist(entries, args.repo_id)      # fail fast, before spending anything
    configs = [(args.config, dict(ABLATION_CONFIGS)[args.config])] if args.config else ABLATION_CONFIGS
    confirm_cost(len(entries), len(configs), skip=args.yes)   # section 3.7
    embedder = CachingEmbeddingProvider(OpenAIEmbeddingProvider())
    results = [run_benchmark(args.repo_id, entries, cfg, name, embedder=embedder) for name, cfg in configs]
    print(render_markdown_table(results))
    save_json(results, path=timestamped_path())          # section 3.8
```

### 3.8 Output behavior

- **Markdown**: always printed to stdout, in section 10.3's exact column order
  (`Configuration | Recall@5 | Recall@10 | MRR | P@5 | Latency (ms)`), plus a
  per-category breakdown table beneath it. Not auto-written into `README.md` — that
  stays a manual, deliberate copy-paste step for whichever day writes the README
  (Day 19), never silently automated here.
- **JSON**: always written to `data/eval_results/<UTC-timestamp>.json` (e.g.
  `data/eval_results/2026-08-28T14-30-00Z.json`) — **a new file every run, nothing is
  ever silently overwritten.** Contains every `ConfigResult` (aggregate, per-category,
  and full per-question detail — the actual raw material "for reruns" SPEC.md asks
  for). These files are meant to be committed (they *are* the honest record SPEC's
  "never fabricate a metric" constraint exists to protect) — no `.gitignore` change is
  proposed either way; that's a call for you, not silently decided here.

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
how many real entries currently exist). DB validation test (skip-if-unreachable,
same convention as every prior day): a fake `repo_id`/known-bad address correctly
reported as missing; the real 20-entry file passes against the real corpus's
`repo_id` (this one specific test legitimately needs the real environment-resolved
`repo_id` — resolve it the same way section 3.1 shows, never hardcoded).

**Acceptance**: 20 entries in `data/benchmark.json`; `load_benchmark` and
`validate_addresses_exist` both pass; every entry has had its source manually read
and confirmed (section 3.3) — this is the "semantically verified" half of Day 8's
"Done when," and it's a checklist a test suite cannot certify for you.

### Day 9 — 20 more, to 40 total

**Step 1** — Author 20 more questions, this time deliberately weighted toward
`relational`/`blast_radius` so the running total approaches SPEC's 15/10/8/7 target.
Same process as Day 8 (section 3.4 workflow, section 3.2 policy, manual source
verification per entry).

**Step 2** — Re-run `load_benchmark` + `validate_addresses_exist` against all 40.

**Tests**: no new *test code* is required beyond Day 8's (the validator logic doesn't
change) — but the existing DB-validation test that runs against the real file must
now pass against 40 entries, and it's worth a light assertion on the final category
distribution (e.g. `assert counts["relational"] >= 8` or similar loose bound) so a
future accidental edit to `benchmark.json` that skews the mix back toward all-`lookup`
gets caught.

**Acceptance**: 40 entries total; validator passes; every entry semantically
verified; category counts close to 15/10/8/7 (exact SPEC wording is "aim for
roughly," not an exact requirement — don't force it if reality lands at, say,
14/11/8/7).

### Day 10 — metrics, runner, first real row

**Step 1** — `ripple/evaluation/metrics.py`: the three SPEC 10.2 functions plus
section 3.6's edge-case guards, `QuestionResult`/`AggregateMetrics`/`CategoryMetrics`,
`score_question`/`aggregate`/`aggregate_by_category`.

**Step 2** — `ripple/evaluation/runner.py`: `CachingEmbeddingProvider`,
`ConfigResult`, `run_benchmark`, `ABLATION_CONFIGS` (section 5).

**Step 3** — `scripts/run_eval.py`: CLI, cost confirmation gate, markdown + timestamped
JSON output (section 3.8).

**Step 4** — First real, paid run: **confirm before this step** (section 3.7). One
config only — `"Vector + BM25 + RRF"` is the natural choice (it's the pipeline's
current full capability). ~40 embedding requests, zero generation calls.

**Tests** (offline unless noted):
- `metrics.py`: hand-computed values for `recall_at_k`/`precision_at_k`/
  `reciprocal_rank` against small constructed `expected`/`retrieved` lists (including
  a case where `retrieved` has fewer than `k` items, and a no-match case); `k <= 0`
  raises for both `recall_at_k` and `precision_at_k`; empty `expected` raises for
  `recall_at_k`; `aggregate([])` raises; `aggregate_by_category` returns categories in
  sorted order regardless of input order; a mixed-category input produces correct
  per-category means (hand-computed).
- `runner.py`: `run_benchmark` with `pipeline.run_pipeline` **monkeypatched** (module
  level, matching Day 6's established pattern for `test_pipeline.py`) to return
  canned `PipelineResult`s keyed by question — never a real database, never a real
  `OPENAI_API_KEY`. Assert: `retrieved` passed into `score_question` matches
  `[b.address for b in canned_result.blocks]` in the same order; `latency_ms` comes
  from `canned_result.latency_json["total_ms"]`; **a dedicated test asserting
  `answer_question` is never imported/called** by monkeypatching
  `ripple.llm.generate.answer_question` to raise if invoked, then running
  `run_benchmark` end to end and confirming it never fires (the direct test for
  section 3.5's independence requirement). `CachingEmbeddingProvider`: wraps a fake
  inner provider that raises on a repeated input; call `.embed(["q1"])` twice and
  once with `["q1", "q2"]`, assert the inner provider is called only for genuinely
  new text and the cached result is still returned correctly for repeats.
- `scripts/run_eval.py` — at minimum, an argument-parsing test (monkeypatch
  `run_benchmark` to a stub, assert the right `repo_id`/config selection reaches it)
  and a confirmation-gate test (declining the `y` prompt makes no calls to
  `run_benchmark` at all) — both offline, no real API/DB.
- `tests/test_db.py` addition: `fetch_resource_addresses` round-trip (DB-dependent,
  skip-if-unreachable, same convention as every prior day).

**Acceptance**: `python -m pytest` passes (existing 123 plus this cycle's new tests —
see section 10 for why an exact new total isn't quoted); one real, confirmed run of
`scripts/run_eval.py --repo-id <resolved> --config "Vector + BM25 + RRF"` produces a
real Recall@5/MRR row, printed and saved to a timestamped JSON file, using the real,
resolved `repo_id` (never `13` hardcoded anywhere in the command's own script — only
ever passed as a CLI argument at invocation time).

### Day 11 — first three ablation rows

**Step 1** — Run `scripts/run_eval.py --repo-id <resolved>` **without** `--config`
(runs all three `ABLATION_CONFIGS` rows). **Confirm before this step** — ~40
embedding requests total (cached across all three configs per section 3.7), zero
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

**Acceptance**: three real rows exist (in one timestamped JSON file, or three — see
implementation-time choice, either is fine as long as nothing is overwritten
silently), each number traceable to an actual run, and you can explain each row
(section 11's own "Done when": "three rows exist and you can explain each one").

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

- `.env` is never read, printed, or edited by anything in this plan.
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
verifiable in advance. This cycle adds **three new test files** whose exact test
count depends on authoring choices made during implementation (how many distinct
edge cases `test_dataset.py` ends up covering, how many scenarios
`CachingEmbeddingProvider` gets tested against, etc.) — stating a precise number now
would be a guess dressed up as a fact. What's verifiable in advance: `python -m
pytest` must show **123 + (every new test this cycle adds)**, all passing, with zero
regressions to the existing 123.

## 11. Risks, ambiguities, and things flagged for your review

- **The `relational` vs. `blast_radius` subject-inclusion asymmetry (section 3.2) is
  this plan's judgment call, not a SPEC.md requirement.** SPEC gives a worked example
  for `blast_radius` only; `relational`'s exclusion-of-subject policy is inferred by
  analogy to natural language ("depends on" vs. "what breaks"), not quoted from
  SPEC.md. If you read `q002` differently, this needs to be revisited *before* any
  `relational` questions are authored — changing it after 10 `relational` entries
  already exist means re-labeling all of them.
- **`attribute` questions are the highest-effort category to get right**, because
  `expected` must be *exhaustive* over the whole corpus, not just complete for the
  examples someone happened to notice — section 3.2 and 3.4 both call this out, but
  it bears repeating: an incomplete `expected` set for an `attribute` question can
  make recall look better than it is, not worse.
- **`CachingEmbeddingProvider` assumes identical question text always deserves an
  identical embedding within one run** — true for this project (no per-config
  question rewriting exists yet; that's Day 15), but this caching approach would need
  reconsidering the day query rewriting turns "one question" into "N different
  strings" per config.
- **The BM25 rebuild-per-call inefficiency (section 3.7) is real but deliberately not
  fixed this cycle** — flagged as a decision for you, not assumed away.
- **`data/eval_results/` being committed vs. gitignored is not decided here** —
  flagged in section 3.8, no `.gitignore` change is proposed either way.
- **Exact category counts may not land exactly on 15/10/8/7** — SPEC's own wording is
  "aim for roughly," treated literally; forcing an exact ratio by padding with
  artificial questions would be worse than a close-but-imperfect real mix.
- **This plan assumes `examples/complete` remains the benchmark corpus** (matching
  every prior day's corpus choice) — SPEC section 5 does mention eventually adding
  the module root as a second, harder corpus, but that is explicitly out of scope
  for Days 8–11 and not part of this plan.

**Before Day 8 begins, this plan needs your explicit sign-off on:**
1. The `relational`/`blast_radius` subject-inclusion policy (section 3.2) — the one
   genuine judgment call with no SPEC.md worked example behind it.
2. The `CachingEmbeddingProvider` cost-reduction design (section 3.7) — an additive,
   low-risk change, but flagged per your own instruction to surface it as a decision.
3. Whether `data/eval_results/` should be committed or gitignored (section 3.8).

Everything else in this plan follows directly from SPEC.md's literal text or from
this project's own established conventions (Days 1–7), and doesn't need a separate
decision before starting.
