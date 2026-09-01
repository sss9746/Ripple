# Graph stabilization — Session A diagnosis

Date: 2026-08-31

## Purpose

Session A measured the existing graph stage before changing its SQL or ranking
behavior. The goal was to determine whether the accepted Day 13 mean
`graph_ms` of 8,916.67 ms came from timer misattribution, slow SQL, missing
indexes, network round trips, or database connection setup.

All measurements used the configured hosted Supabase database and repository
ID 13. No credentials were printed or recorded.

## Timer-boundary audit

`ripple/retrieval/pipeline.py` starts `graph_ms` after reranking has completed
and stops it after graph neighbor retrieval and candidate augmentation. It does
not include vector hydration, BM25, fusion, or cross-encoder reranking.

The accepted graph latency is therefore genuinely attributable to the graph
stage. Graph-action JSON serialization happens after the timer stops and is not
the source of the measured delay.

## Current call shape

For the default `graph_seed_n=3`, the current implementation can call both
`dependents()` and `dependencies()` for each seed:

- up to 6 graph helper calls;
- 1 new database connection per helper call;
- 1 SQL execution per helper call.

The action cap can stop this early for some questions, so six is the maximum
rather than an unconditional count. The representative three-seed diagnostic
below executed all six calls.

## Connection and round-trip measurement

Six fresh connections, each followed by `SELECT 1`, produced:

| Measurement | Values (ms) |
|---|---|
| Connection setup | 1340.53, 1042.77, 1110.86, 1057.46, 1039.82, 1048.03 |
| `SELECT 1` round trip | 99.94, 228.57, 93.43, 99.94, 177.09, 98.68 |
| Total wall time | 7440.34 |

Connection setup alone was approximately 1.0–1.34 seconds per call and closely
explains the multi-second graph-stage measurements.

## Existing helper measurement

The current helpers were run for three real resources from repo 13:

| Seed | Direction | Rows | Wall time (ms) |
|---|---|---:|---:|
| `module.vpc_endpoints` | dependents | 3 | 2870.63 |
| `module.vpc_endpoints` | dependencies | 4 | 1259.10 |
| `module.vpc` | dependents | 107 | 1324.55 |
| `module.vpc` | dependencies | 0 | 1298.22 |
| `aws_security_group.rds` | dependents | 1 | 3087.52 |
| `aws_security_group.rds` | dependencies | 1 | 1683.26 |

The six calls took 11,523.28 ms in total. The variance comes primarily from
remote connection and network behavior, not from the number of returned rows.

## Shared-connection measurement

Opening one shared connection took 1,147.91 ms. Reusing it for the same six
queries produced:

| Seed | Direction | Rows | Execute + fetch (ms) |
|---|---|---:|---:|
| `module.vpc_endpoints` | dependents | 3 | 51.27 |
| `module.vpc_endpoints` | dependencies | 4 | 53.12 |
| `module.vpc` | dependents | 107 | 150.65 |
| `module.vpc` | dependencies | 0 | 50.84 |
| `aws_security_group.rds` | dependents | 1 | 52.25 |
| `aws_security_group.rds` | dependencies | 1 | 114.88 |

The six executions and row fetches totaled 473.01 ms after the connection was
available. This proves that connection reuse is necessary. It also suggests
that six separate round trips should still be replaced by one batched graph
operation.

## PostgreSQL execution plans

`EXPLAIN (ANALYZE, BUFFERS)` showed:

- the largest dependents query returned 107 rows in 0.528 ms;
- the representative dependencies query returned 4 rows in 0.115 ms;
- planning time was approximately 0.3 ms for each;
- the dependencies query used `edges_source_id_idx`;
- PostgreSQL chose sequential scans for the tiny 114-edge/120-resource tables
  where scanning was cheaper than using an index.

The sequential scans are expected at this corpus size and are not evidence of
a missing index. Both `edges(source_id)` and `edges(target_id)` indexes already
exist in `sql/schema.sql`.

## Diagnosis

The accepted graph latency is not caused by reranker work, timer
misattribution, expensive joins, or missing indexes. It is dominated by opening
a fresh hosted PostgreSQL connection inside every graph helper call, with
additional cost from six network round trips.

## Session B decision

Session B should:

1. replace per-seed/per-direction graph calls with one deterministic batched
   graph query using one connection;
2. preserve the current ordered addresses, graph actions, relationship
   direction, origin address, reference text, and score status exactly;
3. add an automated equivalence assertion between the existing and batched
   implementations;
4. introduce safe connection reuse or pooling if the one-connection cold cost
   keeps `graph_ms` above the 500 ms acceptance gate.

Batching alone is expected to reduce the graph stage substantially, but the
measured 1,147.91 ms cold connection cost means it cannot honestly guarantee a
sub-500-ms graph stage without connection reuse.
