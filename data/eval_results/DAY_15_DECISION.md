# Day 15 decision — query rewriting deferred

## Decision

Day 15 query rewriting is deliberately skipped for the current Ripple scope.
`RetrievalConfig.use_rewrite` remains `False`, and the accepted five-row Day 14
pipeline remains the shipped retrieval policy.

This is a prioritization decision, not a measured claim that query rewriting is
ineffective. No sixth-row rewriting experiment was run.

## Evidence

The accepted Day 14 report
[`2026-09-02T20-06-30-596170Z.json`](2026-09-02T20-06-30-596170Z.json)
measured the final intent-routed graph configuration at:

| Metric | Result |
|---|---:|
| Recall@5 | 0.979 |
| Recall@10 | 1.000 |
| MRR | 0.818 |
| Precision@5 | 0.260 |
| Mean total latency | 5,541.02 ms |

All 40 questions had complete expected evidence within the top 10. The remaining
headroom is primarily early ranking, not missing top-10 evidence. Query rewriting
would add an LLM call and expand one question into four searches, increasing
latency, cost, and run-to-run variability for limited expected retrieval gain on
this benchmark. Terraform questions also frequently contain exact resource
identifiers whose lexical form should be preserved.

The Day 14 result therefore provides enough evidence to prioritize answer
quality, citation correctness, and prompt-injection safety next. It does not
prove rewriting would never help on different data.

## Future experiment

Revisit query rewriting as a robustness experiment when either of these changes:

- the benchmark includes unseen paraphrases, ambiguous terminology, or vocabulary
  that differs substantially from the Terraform source;
- evaluation expands to a larger or more heterogeneous corpus where one original
  query no longer retrieves complete top-10 evidence.

A future evaluation should add rewriting as one sixth row on top of the frozen
Day 14 pipeline. It should retain the original question among the rewritten
queries and measure Recall@5, Recall@10, MRR, Precision@5, rewrite latency, total
latency, provider calls, and failure/fallback frequency. The decision should be
based on that measured delta rather than an assumed benefit.

## Next step

Proceed to Day 16: structured grounded answers, resolvable citations, an explicit
insufficient-evidence path, and repository prompt-injection resistance.
