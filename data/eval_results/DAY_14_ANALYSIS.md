# Day 14 full-table analysis

## Run identity

- Report: [`2026-09-02T20-06-30-596170Z.json`](2026-09-02T20-06-30-596170Z.json)
- Implementation commit: `0c7aa09`
- Generated at: `2026-09-02T20:06:30.448696Z`
- Repository ID: 13 (`vpc-complete`)
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

The benchmark, indexed corpus, corpus revision, embedding model, and reranker
revision match the accepted baseline artifacts. The report passed every Day 14
provenance, configuration, ordering, quality, latency, and accounting check.

## Main finding

The final intent-routed graph configuration is the strongest measured system.
It achieved `0.979` Recall@5, perfect `1.000` Recall@10, `0.818` MRR, and
`0.260` Precision@5. It improved every overall quality metric over the
cross-encoder row and added only 197.42 ms of mean graph-stage work.

The ablation path was not monotonic. Adding BM25 to vector retrieval improved
recall, but applying RRF with the current fixed settings made the result worse.
The cross-encoder recovered those losses and exceeded the first two rows on most
quality metrics. Intent-routed graph expansion then supplied the structural
evidence that semantic and lexical ranking alone still missed.

## Overall results

| Configuration | Recall@5 | Recall@10 | MRR | P@5 | Mean latency (ms) |
|---|---:|---:|---:|---:|---:|
| Vector only | 0.746 | 0.821 | 0.696 | 0.190 | **1,795.42** |
| Vector + BM25 | 0.804 | 0.835 | 0.696 | 0.210 | 3,628.04 |
| Vector + BM25 + RRF | 0.702 | 0.821 | 0.658 | 0.180 | 3,520.00 |
| + Cross-encoder rerank | 0.854 | 0.900 | 0.746 | 0.220 | 5,266.77 |
| + Graph expansion | **0.979** | **1.000** | **0.818** | **0.260** | 5,541.02 |

The graph row improved on the cross-encoder row by `0.125` Recall@5, `0.100`
Recall@10, `0.073` MRR, and `0.040` Precision@5. Mean total latency increased
by 274.25 ms, or 5.2%, within this uniformly pre-warmed run.

Vector only remains the fastest row, but it leaves substantial evidence out of
the top 10. The final graph row costs about 3.09 times as much total query
latency as vector only while eliminating all top-10 misses and materially
improving early ranking.

## What each stage contributed

| Added stage | Change R@5 | Change R@10 | Change MRR | Change P@5 | Change latency |
|---|---:|---:|---:|---:|---:|
| BM25 | +0.058 | +0.015 | +0.000 | +0.020 | +1,832.62 ms |
| RRF | **-0.102** | **-0.015** | **-0.038** | **-0.030** | -108.04 ms |
| Cross-encoder | +0.152 | +0.079 | +0.088 | +0.040 | +1,746.77 ms |
| Routed graph | +0.125 | +0.100 | +0.073 | +0.040 | +274.25 ms |

### BM25

BM25 added useful exact-token evidence. Relative to vector only, it improved
Recall@5 on five questions and reduced it on one. Overall Recall@5 rose from
0.746 to 0.804, and Precision@5 rose from 0.190 to 0.210. Its benefit was
strongest for attribute and blast-radius retrieval, but it did not solve the
relational category: relational Recall@10 actually fell from 0.625 to 0.525.

### RRF

RRF is the clearest regression in the table. Relative to the unfused vector and
BM25 row, it improved Recall@5 on only one question and reduced it on seven;
Recall@10 improved on two and declined on four. Every aggregate quality metric
fell. The result is honest evidence that the current `rrf_k=60` fusion policy
does not improve this benchmark, even though RRF is a reasonable general
technique.

This run does not isolate why. Plausible causes include equal treatment of two
rankers with different category strengths and the fixed depth/constant, but no
parameter tuning was performed on Day 14. The measured regression should be
reported rather than hidden or optimized away after seeing the final benchmark.

### Cross-encoder

The cross-encoder more than recovered the RRF loss. It improved Recall@5 on
eight questions with no declines, although one question lost Recall@10 and four
had lower reciprocal rank. Overall, it produced the best non-graph row:
`0.854` Recall@5, `0.900` Recall@10, and `0.746` MRR.

The shared reranker was prepared once in 11,536.06 ms and reused by both final
rows. That one-time preparation cost is excluded from every per-question
latency measurement.

### Intent-routed graph expansion

Graph expansion closed the remaining structural gap. Against the cross-encoder
row, it improved Recall@5 on five questions and Recall@10 on four, with no
declines in either recall metric. Reciprocal rank improved on eight questions
and declined on one (`q009`). The four top-10 recoveries were `q011`, `q012`,
`q028`, and `q029`; all are relational questions.

The final row's ordered address lists exactly match the accepted Session C
intent-routed row for all 40 questions. This confirms that the fresh five-row
run reproduced the frozen graph policy rather than accidentally evaluating a
different configuration.

## Category results

| Configuration | Attribute R@5 / R@10 | Blast radius R@5 / R@10 | Lookup R@5 / R@10 | Relational R@5 / R@10 |
|---|---:|---:|---:|---:|
| Vector only | 0.476 / 0.548 | 0.906 / 0.969 | 1.000 / 1.000 | 0.425 / 0.625 |
| Vector + BM25 | 0.738 / 0.738 | 1.000 / 1.000 | 1.000 / 1.000 | 0.400 / 0.525 |
| Vector + BM25 + RRF | 0.405 / 0.690 | 0.906 / 1.000 | 1.000 / 1.000 | 0.300 / 0.500 |
| + Cross-encoder rerank | 0.881 / 1.000 | 1.000 / 1.000 | 1.000 / 1.000 | 0.500 / 0.600 |
| + Graph expansion | **0.881 / 1.000** | **1.000 / 1.000** | **1.000 / 1.000** | **1.000 / 1.000** |

The routing policy explains the clean final result:

- Lookup and attribute questions skip graph expansion, preserving the
  cross-encoder's already-strong ordering.
- Blast-radius questions traverse dependents only, preserving perfect recall
  and MRR without irrelevant dependency insertions.
- Relational questions traverse the needed structural direction. Their
  Recall@5 doubled from 0.500 to 1.000, Recall@10 rose from 0.600 to 1.000,
  MRR rose from 0.163 to 0.453, and Precision@5 rose from 0.100 to 0.260.

The final row achieved perfect Recall@10 in all four categories. Attribute,
blast-radius, and lookup quality was unchanged from the cross-encoder row;
all aggregate quality gains came from the relational category.

## Latency by stage

| Configuration | Vector (ms) | BM25 (ms) | Fusion (ms) | Rerank (ms) | Graph (ms) | Total (ms) |
|---|---:|---:|---:|---:|---:|---:|
| Vector only | 1,795.38 | — | — | — | — | 1,795.42 |
| Vector + BM25 | 1,780.32 | 1,847.64 | 0.04 | — | — | 3,628.04 |
| Vector + BM25 + RRF | 1,811.43 | 1,708.32 | 0.20 | — | — | 3,520.00 |
| + Cross-encoder rerank | 1,758.13 | 1,870.66 | 0.21 | 1,637.70 | — | 5,266.77 |
| + Graph expansion | 1,836.64 | 1,900.52 | 0.21 | 1,606.16 | **197.42** | 5,541.02 |

Fusion itself is computationally negligible; the RRF row's slightly lower total
than the preceding row is ordinary remote-query timing variation, not a speed
benefit caused by fusion. Vector and BM25 database calls dominate the first
three rows. Reranking adds about 1.6 seconds per question in the final rows.

Intent routing makes graph cost category-specific. Lookup and attribute
questions, which skip graph SQL, measured about 0.05 ms of graph overhead.
Relational questions averaged 395.73 ms, while blast-radius questions averaged
492.31 ms. The overall graph mean of 197.42 ms passed the declared sub-500-ms
gate.

The graph stage is 35.7% faster than Session C's accepted routed measurement of
307.19 ms and 97.8% faster than Day 13's original 8,916.67 ms graph stage.
These figures show the batching and routing optimization remains effective.
Hosted database timing varies, so they should not be presented as guaranteed
production latency.

## Embedding and latency methodology

The runner precomputed all 40 unique question embeddings before any row began.
Precomputation took 14,442.12 ms in total, or 361.05 ms per question. The shared
cache then recorded exactly 200 timed cache hits: 40 questions across five
vector-enabled configurations. Provider accounting was exact:

```text
provider calls before timed rows: 40
provider calls during timed rows: 0
cache hits during timed rows: 200
latency methodology valid: true
```

Therefore, every Day 14 row is comparable with every other Day 14 row: none
contains live OpenAI embedding network latency. The 14.44-second embedding
precomputation cost is real, but it is reported separately instead of being
charged only to whichever row happened to execute first.

Direct total-latency comparisons with the Day 11, Day 12, and Day 13 reports are
not controlled. Those reports did not uniformly pre-warm one shared cache, and
remote vector/BM25 timing also varies between runs. Graph-stage latency remains
directly comparable because embedding generation was never included in
`graph_ms`.

## Session C reproduction and acceptance

The final graph row reproduced Session C's accepted quality exactly:

| Metric | Session C routed graph | Day 14 graph |
|---|---:|---:|
| Recall@5 | 0.979 | 0.979 |
| Recall@10 | 1.000 | 1.000 |
| MRR | 0.818 | 0.818 |
| P@5 | 0.260 | 0.260 |
| Ordered result differences | 0 of 40 | 0 of 40 |

All automated acceptance signals passed:

```text
strict_quality_path: true
tradeoff_quality_path: true
graph_latency_under_500ms: true
relational_recall_at_10_preserved: true
embedding_accounting.valid: true
latency_methodology.valid: true
day14_accepted: true
```

## Limitations

- The benchmark has 40 questions over one 114-block Terraform example. The
  results demonstrate behavior on this declared corpus, not universal retrieval
  quality.
- The deterministic intent router scored perfectly on benchmark wording that
  informed its design. Unseen phrasings may be routed differently.
- Perfect Recall@10 does not mean every relevant block is ranked first, nor does
  it measure generated-answer correctness. This run evaluates retrieval.
- RRF was tested with one fixed configuration. The result proves that this
  configuration regressed, not that every possible fusion policy will regress.
- Mean latency is based on hosted database and local model timing from one run;
  it is useful for stage comparison but is not a service-level guarantee.
- Embedding precomputation and reranker preparation are excluded from row
  latency and disclosed separately. A cold application request would experience
  additional startup or embedding cost unless these resources were already
  warm.

## Conclusion

Day 14 is accepted. The benchmark supports three concrete conclusions:

1. Hybrid candidate generation is useful, but the current RRF settings are a
   measurable weak point rather than an automatic improvement.
2. Cross-encoder reranking is the strongest non-structural configuration and
   repairs most of the fusion regression.
3. Intent-routed graph expansion is the decisive final stage: it preserves the
   strong lookup, attribute, and blast-radius results while taking relational
   Recall@5 and Recall@10 to 1.000 at a controlled mean graph cost.

The full implementation test suite passed before the accepted run with 267
tests passed and 19 skipped. The report and this analysis are the final Day 14
evaluation artifacts.
