# Day 12 cross-encoder reranking analysis

## Run identity

- Report: [`2026-08-31T00-52-40-886297Z.json`](2026-08-31T00-52-40-886297Z.json)
- Benchmark questions: 40
- Indexed resources: 114
- Benchmark SHA-256: `d4bf21f8de84a99a389198ba6bdf1dea99f0e259ad4add52fc542514d06e0834`
- Indexed corpus SHA-256: `330178649d690e88812bcfae99fed420f9e176b4b1aaf470574e2935aa284743`
- Corpus Git revision: `0a36bd54069c64be2da788b2afb5df0a8e8e7398`
- Reranker: `BAAI/bge-reranker-base`
- Reranker revision: `2cfc18c9415c912f9d8155881c133215df768a70`
- `sentence-transformers`: `6.0.0`
- Maximum input length: 512 tokens
- Measured preparation time: 12,261.13 ms

The benchmark hash, indexed-corpus hash, and corpus Git revision match the accepted
Day 11 report. The comparison therefore uses the same 40 questions and the same
indexed Terraform content. The preparation measurement combines model loading and
one dummy prediction; it is performed once before the timed question loop and is
not included in per-question latency.

## Overall results

| Configuration | Recall@5 | Recall@10 | MRR | P@5 | Mean latency (ms) |
|---|---:|---:|---:|---:|---:|
| Vector only | 0.746 | 0.821 | 0.696 | 0.190 | 2341.32 |
| Vector + BM25 | 0.804 | 0.835 | 0.696 | 0.210 | 4831.00 |
| Vector + BM25 + RRF | 0.702 | 0.821 | 0.658 | 0.180 | 4093.96 |
| + Cross-encoder rerank | **0.854** | **0.900** | **0.746** | **0.220** | 6644.35 |

The cross-encoder row produced the best value for every measured retrieval-quality
metric. Relative to the RRF row that supplies its candidates, Recall@5 increased
by `0.152`, Recall@10 by `0.079`, MRR by `0.088`, and P@5 by `0.040`.

The improvement costs latency. Mean total latency increased from 4,093.96 ms for
RRF to 6,644.35 ms with reranking, an increase of about 62%. The reranking stage
itself averaged 2,275.66 ms per question. Model preparation added a separate,
one-time 12,261.13 ms before evaluation.

## Named Day 11 regression checks

The following table compares the required questions against the accepted Day 11
`Vector + BM25 + RRF` row. Values are shown as Day 11 RRF to Day 12 reranker.

| Question | Category | Recall@5 | Recall@10 | MRR | Observation |
|---|---|---:|---:|---:|---|
| `q020` | attribute | 0.000 -> 1.000 | 0.000 -> 1.000 | 0.000 -> 1.000 | Fully recovered; `module.vpc_endpoints` moved to rank 1. |
| `q037` | attribute | 0.333 -> 0.667 | 0.333 -> 1.000 | 1.000 -> 1.000 | All expected evidence reached the top 10. |
| `q038` | attribute | 0.500 -> 0.500 | 0.500 -> 1.000 | 1.000 -> 0.200 | The missing evidence was recovered at rank 5, but top-five coverage did not increase. |
| `q039` | attribute | 0.000 -> 1.000 | 1.000 -> 1.000 | 0.167 -> 1.000 | Expected evidence moved from rank 6 to rank 1. |
| `q014` | blast_radius | 0.500 -> 1.000 | 1.000 -> 1.000 | 1.000 -> 1.000 | Complete expected evidence now appears in the top five. |
| `q016` | blast_radius | 0.750 -> 1.000 | 1.000 -> 1.000 | 0.500 -> 1.000 | Complete evidence moved into the top five with an expected block at rank 1. |

All six named questions improved on at least one required coverage metric, and all
six achieved Recall@10 of 1.0. The `q038` result is mixed: Recall@10 improved, but
one relevant block remains at rank 5, so MRR fell and Recall@5 stayed unchanged.

## Smoke-test evidence

Before the full evaluation, the real model was tested on `q020` using the exact
fourth ablation configuration. `module.vpc_endpoints` was rank 12 after RRF, which
was inside the configured 50-candidate rerank pool. The cross-encoder assigned it
a score of approximately `0.950` and moved it to rank 1. This confirms that the
candidate reached the real model and that reranking—not a candidate-pool change—
recovered it.

The offline tests establish that each rerank call constructs all query-candidate
pairs and sends them through one batched `predict()` call. The smoke test separately
establishes that the downloaded model loads and produces usable scores on real
Terraform candidates. Together, these cover the structural and real-inference
parts of the batching claim.

## Regressions and remaining weakness

Reranking was not uniformly beneficial. Compared with RRF, reciprocal rank
improved for 10 questions, stayed unchanged for 26, and declined for 4:

- `q005` remained fully recalled, but its expected block moved from rank 1 to 2.
- `q011` became a complete miss: `module.vpc` moved from rank 8 to outside the top
  10.
- `q032` remained fully recalled, but its expected block moved from rank 3 to 5.
- `q038` gained complete Recall@10, but its first relevant result moved from rank 1
  to 5.

Relational questions remain the main weakness. Their category Recall@5 was 0.500,
Recall@10 was 0.600, and MRR was 0.163, while every other category achieved perfect
Recall@10. A cross-encoder can reorder only candidates already retrieved; it does
not follow stored dependency edges or add missing neighboring resources.

## Conclusion and follow-up

The Day 12 result is accepted as measured. Cross-encoder reranking successfully
corrected the most damaging RRF ordering failures and established a new best
quality baseline, with a clear latency cost.

The evidence supports these next steps:

1. Integrate graph expansion so relational and blast-radius questions can add
   dependency neighbors rather than relying only on semantic and lexical matches.
2. Preserve this reranker row as the Day 12 baseline and measure graph expansion
   as a separate configuration.
3. Later evaluate latency options such as a smaller rerank pool, a faster model,
   or hardware acceleration, while checking that `q020`, `q037`, `q038`, and
   `q039` do not regress.
4. Investigate `q011` specifically after graph expansion because reranking pushed
   its only expected block outside the final top 10.

Before this report was accepted, the complete project suite finished with
**197 tests passed, 16 environment-dependent integration tests skipped, and zero
failures**. The run completed in 1.50 seconds and did not load the real reranker.
