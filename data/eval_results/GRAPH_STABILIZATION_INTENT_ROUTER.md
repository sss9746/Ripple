# Graph stabilization — intent router

Date: 2026-09-01

## Scope

This step added deterministic, local query-intent routing after Session B's
graph batching and pooling work. It does not rewrite the question, call an
LLM, tune RRF, rescore candidates, or change graph ranking.

Routing is opt-in through `RetrievalConfig.graph_route_by_intent`, which
defaults to `False` so existing callers retain the accepted Day 13 behavior.

## Runtime behavior

The router reads only the question string and returns one of five intents:

| Intent | Graph directions |
|---|---|
| lookup | none |
| attribute | none |
| dependency | dependencies only |
| blast radius | dependents only |
| ambiguous relationship | dependents, then dependencies |

Runtime code never reads benchmark IDs, categories, expected addresses, or
the benchmark file. The category-to-intent mapping exists only in tests and
the evaluation script.

When routing is enabled, `stages_json["graph_intent"]` records the selected
intent and directions for observability.

## Gold-label evaluation

The benchmark-only mapping was predeclared as:

- lookup -> lookup;
- attribute -> attribute;
- relational -> dependency;
- blast_radius -> blast_radius.

`ambiguous_relationship` has no positive gold examples in the current
benchmark and would count as an incorrect fallback if predicted.

The evaluation over all 40 questions produced:

```text
Router accuracy: 40/40 (100.0%)

gold\\pred lookup attribute dependency blast_radius ambiguous_relationship
lookup          15      0          0            0                      0
attribute        0      7          0            0                      0
dependency       0      0         10            0                      0
blast_radius     0      0          0            8                      0

Ambiguous fallbacks: 0
Misclassifications: 0
```

Precision and recall were 1.000 for each of the four gold intents.

This result proves correctness for the declared benchmark phrasing, not broad
English-language generalization. The classifier is deliberately documented as
a deterministic first-pass heuristic.

## Real pipeline smoke test

A no-OpenAI BM25-plus-graph run against repo 13 produced:

| Question type | Selected directions | Graph actions | `graph_ms` |
|---|---|---:|---:|
| lookup | none | 0 | 0.08 ms |
| dependency, cold pool | dependency | 1 | 1228.37 ms |
| dependency, reused pool | dependency | 1 | 155.89 ms |

The lookup path proves routing can skip the graph database call completely.
The dependency path proves only the requested direction is queried.

## Tests

Focused database-enabled result:

```text
67 passed in 39.98s
```

Complete database-enabled project result:

```text
254 passed in 138.75s
```

No test was skipped.

## Remaining decision

Router accuracy is not retrieval accuracy. Session C must still compare the
accepted cross-encoder, original graph, batched graph, and batched-plus-routed
graph results and apply the predeclared quality and latency gates before the
routed graph configuration can become the default.
