# Graph stabilization — Session B batching and pooling

Date: 2026-09-01

## Scope

Session B fixed the latency cause measured in Session A without changing RRF,
reranking, graph ranking, seed selection, graph caps, or provenance semantics.

## Implementation

- Added `graph.fetch_neighbors(repo_id, seed_ids, directions)`.
- The default both-direction path uses one parameterized `UNION ALL` SQL
  statement, one cursor execution, and one borrowed connection.
- The query filters both `edges.repo_id` and the returned resource's
  `repo_id`.
- Direction order remains dependents first, dependencies second through an
  explicit numeric `direction_rank`.
- Total ordering remains address, reference text, resource ID, then edge ID.
  The batch query selects `edges.id AS edge_id`, allowing the union-level
  `ORDER BY` to reference it legally.
- The pipeline's promote/add/leave-alone algorithm, global action cap,
  deduplication, scores, and graph provenance are unchanged.
- Added Psycopg's official pool extra through `psycopg[binary,pool]`.
- Added a lazy process-wide pool for the latency-sensitive graph read path.
  Existing `db.get_connection()` callers retain their original behavior.

## Correctness verification

The focused integration test compares the new batched result with the legacy
`dependents()` and `dependencies()` helpers for the same real seeds and
asserts exact `GraphNeighbor` list equality.

Additional tests prove:

- empty seed/direction inputs make no database call;
- both directions execute one union query;
- repository-isolation filters are present;
- ordering includes the selected `edge_id` alias;
- unsupported directions fail explicitly;
- nonpositive graph limits still skip graph retrieval;
- pipeline ordering, caps, deduplication, scores, and provenance remain
  unchanged.

Focused database-enabled result:

```text
55 passed in 78.53s
```

Complete database-enabled project result:

```text
235 passed in 135.58s
```

No test was skipped.

## Latency measurements

The representative batch returned the same 116 graph rows used in Session A.

Before pooling, one batched call still opened a fresh hosted connection:

| Attempt | Batch wall time (ms) |
|---:|---:|
| 1 | 1290.10 |
| 2 | 1326.87 |
| 3 | 1293.41 |

After pooling, three calls in one process produced:

| Attempt | State | Batch wall time (ms) |
|---:|---|---:|
| 1 | cold pool | 1374.46 |
| 2 | reused connection | 257.34 |
| 3 | reused connection | 273.27 |

A no-OpenAI BM25-plus-graph pipeline smoke test measured the actual
`graph_ms` boundary:

| Attempt | State | `graph_ms` | Total pipeline (ms) | Graph actions |
|---:|---|---:|---:|---:|
| 1 | cold pool | 1182.80 | 2548.55 | 1 |
| 2 | reused connection | 147.60 | 1537.23 | 1 |
| 3 | reused connection | 145.48 | 1522.99 | 1 |

The normal warm path is below the 500 ms acceptance target and approximately
98% lower than the accepted Day 13 mean `graph_ms` of 8,916.67 ms. The first
graph request in a fresh process still pays the hosted connection setup cost;
that cold cost remains visible rather than being moved outside the timer.

## Remaining acceptance work

Session C must still automate the full 40-question old-row-versus-batched-row
comparison. The accepted Day 13 report contains ordered final addresses but
does not contain graph provenance per question, so full-report address
equality can be checked against that artifact while provenance equivalence is
covered structurally by the exact legacy-helper comparison and pipeline tests
above.

Intent routing has not been implemented yet. No scoring, ranking-policy,
seed-tuning, RRF-tuning, or query-rewriting work was performed.
