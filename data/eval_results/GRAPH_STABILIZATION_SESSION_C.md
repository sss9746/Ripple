# Graph stabilization — Session C analysis

Date: 2026-09-01

Accepted comparison artifact:
`data/eval_results/2026-09-01T20-48-26-006545Z.json`

Day 13 baseline artifact:
`data/eval_results/2026-08-31T22-17-19-477902Z.json`

## Scope

Session C performed the predeclared controlled comparison after Session A's
latency diagnosis, Session B's batched graph query and connection pool, and
the deterministic intent router. It did not tune RRF, change the cross-encoder
candidate pool, score newly discovered graph blocks, rewrite queries, or alter
the graph seed and expansion limits.

The comparison reused the two accepted Day 13 rows and ran only the two new
graph configurations. An in-memory embedding cache reduced the paid work to
40 provider calls for 40 unique questions; the second configuration recorded
40 cache hits. The cross-encoder was loaded and prepared once for both new
rows.

## Overall results

| Configuration | Recall@5 | Recall@10 | MRR | P@5 | Mean total latency |
|---|---:|---:|---:|---:|---:|
| Cross-encoder, no graph (Day 13) | 0.854 | 0.900 | 0.746 | 0.220 | 4,980.73 ms |
| Original graph (Day 13) | 0.821 | 1.000 | 0.789 | 0.210 | 14,745.72 ms |
| Batched graph | 0.821 | 1.000 | 0.789 | 0.210 | 9,191.77 ms |
| Batched and intent-routed graph | **0.979** | **1.000** | **0.818** | **0.260** | 8,826.13 ms |

The routed result passes both predeclared quality paths:

- strict path: it exceeds the cross-encoder baseline on Recall@5, Recall@10,
  and MRR;
- tradeoff path: Recall@5 is above 0.845 while Recall@10 remains 1.000 and MRR
  exceeds the original graph result.

This is not a tradeoff result in practice: routed graph improved every overall
quality metric relative to both accepted baselines except Recall@10 versus the
old graph row, where it remained at the maximum value of 1.000.

## Batching correctness gate

The automated comparison checked the ordered final address list for all 40
questions. Batched graph was exactly equal to the accepted Day 13 graph row:

```text
questions checked: 40
differences: 0
equal: true
```

Latency was deliberately excluded from equality. This establishes that
batching changed the database access pattern without changing retrieval
behavior.

The accepted Day 13 report did not persist graph provenance per question, so
that artifact cannot support a retrospective provenance comparison. Session
B covers this separately with an exact legacy-helper-versus-batched-neighbor
database test, while pipeline tests verify promotion, deduplication, caps,
ordering, and provenance fields.

## Graph latency

| Configuration | Mean `graph_ms` | Change from original graph |
|---|---:|---:|
| Original graph (Day 13) | 8,916.67 ms | baseline |
| Batched graph | 609.64 ms | 93.2% lower |
| Batched and intent-routed graph | **307.19 ms** | **96.6% lower** |

The routed row passes the declared warm mean target of less than 500 ms. The
batched-only row missed that threshold in this full run despite earlier warm
smoke measurements near 145–155 ms. This is consistent with variable hosted
database and connection-pool timing; it does not affect the correctness
comparison. Routing halves the measured graph stage again by skipping graph
SQL for lookup and attribute questions and querying only the necessary
direction for relationship questions.

Mean total latency fell by 40.1% versus the accepted original graph row. It
remains above the accepted cross-encoder-only total. Total latency across
different runs is not a controlled stage-level comparison because vector and
BM25 database timing also varied substantially; `graph_ms` is the relevant
isolated measurement for the graph optimization.

## Category results

| Category | Cross-encoder R@5 / R@10 | Original graph R@5 / R@10 | Routed graph R@5 / R@10 |
|---|---:|---:|---:|
| Attribute | 0.881 / 1.000 | 0.690 / 1.000 | **0.881 / 1.000** |
| Blast radius | 1.000 / 1.000 | 0.906 / 1.000 | **1.000 / 1.000** |
| Lookup | 1.000 / 1.000 | 0.867 / 1.000 | **1.000 / 1.000** |
| Relational | 0.500 / 0.600 | 0.775 / 1.000 | **1.000 / 1.000** |

Routing explains the pattern:

- lookup and attribute questions skip graph expansion, preserving the strong
  cross-encoder ordering instead of inserting unnecessary neighbors;
- dependency questions follow only dependency edges, preventing dependents
  from consuming the expansion budget or being inserted ahead of the needed
  evidence;
- blast-radius questions follow only dependent edges, avoiding unrelated
  dependency insertions.

The largest gain is relational Recall@5, which rises from 0.500 without graph
and 0.775 with unrestricted graph to 1.000 with directed graph traversal.
Relational Recall@10 remains 1.000, satisfying its explicit preservation gate.

## Acceptance decision

All automated gates passed:

```text
strict_quality_path: true
tradeoff_quality_path: true
graph_latency_under_500ms: true
relational_recall_at_10_preserved: true
accepted: true
```

The batched and intent-routed graph policy is accepted as the graph-enabled
configuration for the project. No additional graph scoring, ranking-policy
sweep, RRF tuning, seed tuning, or query rewriting is justified before moving
to Day 14.

## Limitations

- The intent router scored 40/40 on the current benchmark, whose wording was
  used to design its deterministic rules. This is strong evidence for this
  declared dataset, not proof that the heuristic generalizes to every way a
  user may phrase a Terraform question.
- The benchmark contains 40 questions over one 114-block Terraform corpus.
  Results should be presented with that scope rather than as universal model
  performance.
- Routed graph still adds latency relative to no graph. A product can keep a
  no-graph mode for latency-sensitive requests, while using routed graph when
  relationship completeness matters.

## Next step

The three-session stabilization time-box is complete and successful. Freeze
the retrieval policy, make the accepted routed graph setting explicit in the
evaluation/default configuration, and proceed to Day 14's final results table
and README presentation.
