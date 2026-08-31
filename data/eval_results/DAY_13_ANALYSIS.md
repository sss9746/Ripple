# Day 13 graph-expansion analysis

## Run identity

- Report: [`2026-08-31T22-17-19-477902Z.json`](2026-08-31T22-17-19-477902Z.json)
- Implementation commit: `620f292`
- Benchmark questions: 40
- Indexed resources: 114
- Benchmark SHA-256: `d4bf21f8de84a99a389198ba6bdf1dea99f0e259ad4add52fc542514d06e0834`
- Indexed corpus SHA-256: `330178649d690e88812bcfae99fed420f9e176b4b1aaf470574e2935aa284743`
- Corpus Git revision: `0a36bd54069c64be2da788b2afb5df0a8e8e7398`
- Embedding model: `text-embedding-3-small`
- Reranker: `BAAI/bge-reranker-base`
- Reranker revision: `2cfc18c9415c912f9d8155881c133215df768a70`
- `sentence-transformers`: `6.0.0`
- Maximum reranker input length: 512 tokens
- Reranker preparation time: 9,941.42 ms for the reranker row and 5,870.61 ms
  for the graph row
- Graph configuration: 3 immutable seeds, one-hop expansion, at most 10 total
  promotions or additions, and 10 final blocks

The benchmark hash, indexed-corpus hash, and corpus Git revision match the
accepted Day 11 and Day 12 reports. The comparison therefore uses the same 40
questions and the same indexed Terraform content. The first four configurations
also reproduced their previous quality metrics exactly; only their latency
changed with remote-service timing.

The two reranker preparation measurements are one-time model loading and dummy
prediction costs. They occur before the timed question loops and are not included
in per-question latency. The current evaluation runner prepares one reranker for
the cross-encoder row and a second for the graph row.

## Overall results

| Configuration | Recall@5 | Recall@10 | MRR | P@5 | Mean latency (ms) |
|---|---:|---:|---:|---:|---:|
| Vector only | 0.746 | 0.821 | 0.696 | 0.190 | 1,858.20 |
| Vector + BM25 | 0.804 | 0.835 | 0.696 | 0.210 | 3,425.15 |
| Vector + BM25 + RRF | 0.702 | 0.821 | 0.658 | 0.180 | 3,450.99 |
| + Cross-encoder rerank | **0.854** | 0.900 | 0.746 | **0.220** | 4,980.73 |
| + Graph expansion | 0.821 | **1.000** | **0.789** | 0.210 | 14,745.72 |

Graph expansion achieved perfect Recall@10 across the entire benchmark. Relative
to the cross-encoder row, Recall@10 increased by `0.100` and MRR increased by
`0.043`. This is the first evaluated configuration to place every expected block
inside the final top 10.

The result is not a uniform ranking improvement. Recall@5 decreased by `0.033`
and P@5 decreased by `0.010`. Graph neighbors inserted beside high-ranked seeds
sometimes displaced an already-correct lexical or semantic result from the top
five, even though no expected evidence was lost from the top 10.

## Per-category comparison

The table below compares the Day 12 cross-encoder baseline with graph expansion.

| Category | Metric | Reranker | + Graph | Change |
|---|---|---:|---:|---:|
| attribute | Recall@5 | 0.881 | 0.690 | -0.190 |
| attribute | Recall@10 | 1.000 | 1.000 | 0.000 |
| attribute | MRR | 0.886 | 0.881 | -0.005 |
| attribute | P@5 | 0.257 | 0.171 | -0.086 |
| blast_radius | Recall@5 | 1.000 | 0.906 | -0.094 |
| blast_radius | Recall@10 | 1.000 | 1.000 | 0.000 |
| blast_radius | MRR | 1.000 | 1.000 | 0.000 |
| blast_radius | P@5 | 0.375 | 0.325 | -0.050 |
| lookup | Recall@5 | 1.000 | 0.867 | -0.133 |
| lookup | Recall@10 | 1.000 | 1.000 | 0.000 |
| lookup | MRR | 0.933 | 0.885 | -0.049 |
| lookup | P@5 | 0.200 | 0.173 | -0.027 |
| relational | Recall@5 | 0.500 | **0.775** | **+0.275** |
| relational | Recall@10 | 0.600 | **1.000** | **+0.400** |
| relational | MRR | 0.163 | **0.412** | **+0.250** |
| relational | P@5 | 0.100 | **0.200** | **+0.100** |

Relational retrieval is the clear success. Graph expansion increased every
relational metric, doubled P@5, and recovered complete top-10 evidence for all 10
relational questions. This directly addresses Day 12's main remaining weakness:
the cross-encoder could reorder retrieved candidates but could not follow stored
Terraform relationships or add missing neighbors.

The other categories were already perfect at Recall@10 under reranking. For those
questions, graph expansion could not improve top-10 completeness and instead
sometimes reordered correct evidence below rank five. The blast-radius result is
therefore mixed: all expected evidence remained in the top 10 and the first
relevant result remained rank 1 on average, but multi-block top-five coverage
declined.

## Per-question gains and regressions

Compared with the cross-encoder row:

- Recall@10 improved for 4 questions, declined for 0, and stayed unchanged for
  36.
- Recall@5 improved for 4 questions, declined for 8, and stayed unchanged for
  28.
- Reciprocal rank improved for 8 questions, declined for 4, and stayed unchanged
  for 28.

The most important recoveries were relational:

| Question | Recall@5 | Recall@10 | MRR | Observation |
|---|---:|---:|---:|---|
| `q011` | 0.000 -> 0.000 | 0.000 -> 1.000 | 0.000 -> 0.167 | `module.vpc` was promoted into rank 6 after Day 12 lost it outside the top 10. |
| `q012` | 0.000 -> 0.750 | 0.000 -> 1.000 | 0.000 -> 0.333 | All four expected dependencies reached the top 10; three reached the top five. |
| `q028` | 0.000 -> 1.000 | 0.000 -> 1.000 | 0.000 -> 0.500 | `module.vpc` moved to rank 2. |
| `q029` | 0.000 -> 1.000 | 0.000 -> 1.000 | 0.000 -> 0.500 | `module.vpc` moved to rank 2. |
| `q030` | 0.000 -> 1.000 | 1.000 -> 1.000 | 0.143 -> 0.500 | Existing evidence moved from rank 7 to rank 2. |
| `q013` | 1.000 -> 1.000 | 1.000 -> 1.000 | 0.200 -> 0.500 | The expected module moved from rank 5 to rank 2. |
| `q031` | 1.000 -> 1.000 | 1.000 -> 1.000 | 0.250 -> 0.500 | The expected module moved from rank 4 to rank 2. |
| `q032` | 1.000 -> 1.000 | 1.000 -> 1.000 | 0.200 -> 0.500 | The expected module moved from rank 5 to rank 2. |

The main top-five regressions were:

- `q001` (`lookup`): `module.vpc` moved from rank 2 to rank 8 after neighbors of
  `module.vpc_endpoints` were inserted ahead of it.
- `q005` (`lookup`): the DynamoDB endpoint policy moved from rank 2 to rank 7.
- `q009` (`relational`): `module.vpc` moved from rank 3 to rank 8; Recall@10
  remained complete, but Recall@5 and MRR declined.
- `q015` and `q016` (`blast_radius`): their first expected blocks stayed at rank
  1, but some additional expected blocks moved below rank 5.
- `q018`, `q037`, and `q038` (`attribute`): complete expected evidence remained
  inside the top 10, but graph insertions reduced top-five coverage.

These regressions are consistent with the current policy: every query expands
both graph directions around the top three seeds, and every discovered promotion
or addition is inserted immediately beside its seed without a second relevance
judgment.

## Smoke-test evidence

Two real smoke checks ran before the full benchmark with one shared, prepared
reranker instance.

For `q011`, `module.vpc` reached the final context at rank 6 with its own original
reranker score, `graph_score_status="promoted"`, and a real dependency reference
`module.vpc.vpc_id`. First-discovery-wins associated that promotion with
`module.vpc_endpoints`, which also references `module.vpc`, rather than with the
DynamoDB policy named in the question. The recovered address is correct, but the
alternate valid origin shows that graph provenance records the first traversal
path, not necessarily the question's most explanatory path.

For `q016`, `output.vpc_endpoints_security_group_arn` was promoted to rank 2 with
`graph_relationship="dependent"`, origin `module.vpc_endpoints`, and reference
`module.vpc_endpoints.security_group_arn`. This directly verifies the required
blast-radius direction against real indexed edges.

The smoke output also contained genuinely new graph-only blocks with
`score=None` and `graph_score_status="unscored"`, while promoted blocks retained
their own cross-encoder scores. This confirms that the pipeline does not
misattribute a seed's score to a block the reranker never evaluated.

## Latency analysis

| Stage | Graph-row mean (ms) |
|---|---:|
| Vector query | 2,123.06 |
| BM25 | 1,905.23 |
| Fusion | 0.21 |
| Rerank | 1,800.45 |
| Graph | **8,916.67** |
| Total | **14,745.72** |

Graph expansion increased mean total latency from 4,980.73 ms to 14,745.72 ms,
an increase of 9,764.99 ms and approximately 2.96 times the reranker baseline.
The graph stage alone accounts for most of that increase.

The current implementation performs up to two sequential Supabase reads for each
of three seeds: dependents first, then dependencies. Each helper opens its own
database connection. The result is correct but pays multiple remote round trips
per question. Relational and blast-radius questions were the slowest categories
at approximately 17.38 seconds each.

## Conclusion and follow-up

The Day 13 result is accepted as measured. Graph expansion provides substantial
retrieval value: it eliminates every top-10 miss and transforms relational
retrieval from the weakest category into complete Recall@10 with much stronger
top-five coverage and ranking.

The fifth row is not a universal replacement for the reranker row yet. It is
slower and slightly worse at top-five ranking for lookup, attribute, and
blast-radius questions. The evidence supports the following next experiments:

1. Gate graph expansion by query intent, prioritizing relational and
   blast-radius questions instead of expanding every lookup and attribute query.
2. Select only the needed direction when intent is clear: dependencies for
   "what does this use?" and dependents for "what breaks if this is removed?".
3. Tune `graph_seed_n` and `graph_max_added` to reduce the number of neighbors
   that can displace strong top-five results.
4. Evaluate expanding before reranking, or applying a second lightweight
   relevance pass, so newly discovered graph blocks compete by question
   relevance rather than being inserted unscored.
5. Batch both directions for all seed IDs into one database operation and reuse
   a connection to remove most graph-stage network round trips.
6. Preserve the Day 12 cross-encoder row as the fast quality baseline and use
   the Day 13 graph row when complete relational evidence is more important than
   latency.

Before the paid evaluation, the complete project suite ran with database access:
**229 tests passed, with zero failures and zero skips**, in 188.17 seconds. Both
required real smoke checks then passed before the 40-question run began.
