# Day 11 retrieval comparison analysis

## Run identity

- Report: [`2026-08-29T17-09-51-035170Z.json`](2026-08-29T17-09-51-035170Z.json)
- Benchmark questions: 40
- Indexed resources: 114
- Benchmark SHA-256: `d4bf21f8de84a99a389198ba6bdf1dea99f0e259ad4add52fc542514d06e0834`
- Indexed corpus SHA-256: `330178649d690e88812bcfae99fed420f9e176b4b1aaf470574e2935aa284743`
- Corpus Git revision: `0a36bd54069c64be2da788b2afb5df0a8e8e7398`

The benchmark hash, indexed-corpus hash, and corpus Git revision match the Day 10
baseline report. Each configuration therefore ran against the same questions and
the same indexed Terraform content.

## Overall results

| Configuration | Recall@5 | Recall@10 | MRR | P@5 | Mean latency (ms) |
|---|---:|---:|---:|---:|---:|
| Vector only | 0.746 | 0.821 | 0.696 | 0.190 | 2341.32 |
| Vector + BM25 | **0.804** | **0.835** | **0.696** | **0.210** | 4831.00 |
| Vector + BM25 + RRF | 0.702 | 0.821 | 0.658 | 0.180 | 4093.96 |

Vector + BM25 without RRF produced the best recall and precision. The surprising
result is that adding equal-weight reciprocal rank fusion (RRF) reduced Recall@5
from `0.804` to `0.702` and MRR from `0.696` to `0.658`.

## RRF anomaly investigation

The reduction is reproducible algorithm behavior, not a benchmark, metric, or
reporting error.

The current RRF implementation gives every result a contribution of
`1 / (60 + rank)` from each retriever. This strongly rewards candidates that appear
in both the vector and BM25 lists. A highly relevant candidate ranked near the top
by only one retriever can therefore be displaced by less relevant candidates that
appear at moderate ranks in both lists. The final `top 10` truncation then removes
some of those strong single-retriever candidates.

Observed Recall@5 changes from Vector + BM25 to Vector + BM25 + RRF include:

| Question | Category | Vector + BM25 | With RRF | Observation |
|---|---|---:|---:|---|
| `q014` | blast_radius | 1.000 | 0.500 | Complete evidence became partial. |
| `q016` | blast_radius | 1.000 | 0.750 | One expected block was demoted. |
| `q020` | attribute | 1.000 | 0.000 | The correct BM25 rank-1 block fell outside the final top 10. |
| `q031` | relational | 1.000 | 0.000 | The correct block moved below rank 5. |
| `q037` | attribute | 0.667 | 0.333 | One additional exact-reference match was lost. |
| `q038` | attribute | 1.000 | 0.500 | A correct BM25 rank-2 block was demoted. |
| `q039` | attribute | 1.000 | 0.000 | The correct block moved below rank 5. |

RRF was not uniformly harmful. For example, `q018` improved from `0.500` to `1.000`
at Recall@5, and `q019` improved from `0.000` to `1.000` at Recall@10. Those gains
were smaller than the losses above, producing a negative aggregate result.

The Day 10 single-configuration RRF run and the Day 11 RRF row produced identical
quality metrics (`Recall@5 = 0.702`, `Recall@10 = 0.821`, `MRR = 0.658`, and
`P@5 = 0.180`). This repeatability further rules out transient API or database
latency as the cause of the ranking difference. Latency varied between runs, but
fusion itself remained sub-millisecond; remote embedding and database timings
account for nearly all runtime variation.

## Other findings

- Vector only was fastest and achieved perfect lookup MRR, but missed exact
  attribute evidence that BM25 recovered.
- Vector + BM25 improved attribute Recall@5 from `0.476` to `0.738` and
  blast-radius Recall@5 from `0.906` to `1.000`.
- Relational Recall@5 remained weak for every configuration: `0.425` for Vector
  only, `0.400` for Vector + BM25, and `0.300` with RRF. Retrieval often found the
  named subject but could not follow its stored Terraform dependency edges.
- The benchmark labels for the investigated misses match the Terraform source. No
  benchmark edits or metric corrections are justified by this run.

## Conclusion and follow-up

The Day 11 rows are accepted as measured. No rerun is required.

The current evidence supports these next steps:

1. Add cross-encoder reranking over a larger candidate pool so exact BM25 matches
   are not discarded solely because vector search ranks them poorly.
2. Integrate graph expansion in the retrieval pipeline to improve relational and
   blast-radius questions.
3. Treat the current RRF row as a baseline. Later experiments may evaluate weighted
   RRF, a smaller RRF constant, exact-match boosts, or score-based fusion rather
   than assuming equal-weight RRF is beneficial.
4. Improve the non-RRF merge tie-breaking, which currently uses address order when
   candidates have the same best rank.

Before this report was accepted, the complete project suite ran with Supabase
integration access: **194 tests passed, with zero failures and zero skips**.
