import argparse
import json
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ripple.evaluation.graph_stabilization import (
    BATCHED_GRAPH_NAME,
    CROSS_ENCODER_NAME,
    DAY13_GRAPH_NAME,
    ROUTED_GRAPH_NAME,
    baseline_result,
    build_stabilization_report,
    compare_ordered_results,
    evaluate_gates,
    load_accepted_report,
    run_stabilization_config,
    stabilization_configs,
)
from ripple.evaluation.runner import _indexed_corpus_fingerprint
from ripple.llm.embeddings import (
    CachingEmbeddingProvider,
    OpenAIEmbeddingProvider,
)
from ripple.retrieval.rerank import CrossEncoderReranker
from scripts.run_eval import (
    benchmark_sha256,
    load_validated_benchmark,
    render_markdown_table,
    timestamped_path,
    write_report,
)


DEFAULT_ACCEPTED_REPORT = Path(
    "data/eval_results/2026-08-31T22-17-19-477902Z.json"
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the controlled Session C graph comparison"
    )
    parser.add_argument("--repo-id", type=int, required=True)
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=Path("data/benchmark.json"),
    )
    parser.add_argument(
        "--accepted-report",
        type=Path,
        default=DEFAULT_ACCEPTED_REPORT,
    )
    parser.add_argument("--yes", action="store_true")
    return parser.parse_args(argv)


def validate_provenance(
    report: dict,
    *,
    repo_id: int,
    benchmark_path: Path,
) -> None:
    if report["repo_id"] != repo_id:
        raise ValueError("accepted report repo_id does not match --repo-id")
    if report["benchmark_sha256"] != benchmark_sha256(benchmark_path):
        raise ValueError("benchmark bytes differ from the accepted Day 13 run")

    fingerprint, count = _indexed_corpus_fingerprint(repo_id)
    corpus = report["corpus"]
    if corpus["indexed_corpus_sha256"] != fingerprint:
        raise ValueError("indexed corpus differs from the accepted Day 13 run")
    if corpus["resource_count"] != count:
        raise ValueError("indexed resource count differs from Day 13")


def confirm_paid_run(question_count: int, *, skip: bool) -> None:
    print(
        "Session C plan: "
        f"{question_count} unique questions × 2 configurations, "
        f"with a shared cache = approximately {question_count} "
        "paid embedding requests."
    )
    if skip:
        return
    response = input("Continue with the paid comparison? [y/N]: ")
    if response.strip().lower() != "y":
        raise SystemExit("Comparison cancelled before making API requests.")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    accepted = load_accepted_report(args.accepted_report)
    entries = load_validated_benchmark(args.benchmark, args.repo_id)
    validate_provenance(
        accepted,
        repo_id=args.repo_id,
        benchmark_path=args.benchmark,
    )
    confirm_paid_run(len(entries), skip=args.yes)

    embedder = CachingEmbeddingProvider(OpenAIEmbeddingProvider())
    reranker = CrossEncoderReranker()
    reranker.prepare()
    reranker_json = reranker.describe()
    print(
        f"Reranker prepared in {reranker.prepare_ms:.0f}ms "
        "(one-time; excluded from question latency)"
    )

    runs = {}
    for name, config in stabilization_configs():
        print(f"Running {name}...")
        runs[name] = run_stabilization_config(
            args.repo_id,
            entries,
            config,
            name,
            embedder=embedder,
            reranker=reranker,
            reranker_json=reranker_json,
        )

    batched = runs[BATCHED_GRAPH_NAME]
    routed = runs[ROUTED_GRAPH_NAME]
    cross = baseline_result(accepted, CROSS_ENCODER_NAME)
    day13_graph = baseline_result(accepted, DAY13_GRAPH_NAME)
    equality = compare_ordered_results(day13_graph, batched.result)
    gates = evaluate_gates(cross, day13_graph, routed.result)

    print(render_markdown_table([batched.result, routed.result]))
    print(
        "Day 13 vs batched ordered-address equality: ",
        equality["equal"],
        f"({equality['questions_checked']} questions)",
    )
    print("Acceptance gates:", gates)

    report = build_stabilization_report(
        args.accepted_report,
        accepted,
        batched,
        routed,
        equality,
        gates,
        {
            "provider_calls": embedder.request_count,
            "cache_hits": embedder.cache_hit_count,
            "unique_questions": len(entries),
        },
    )
    output_path = timestamped_path()
    write_report(report, output_path)
    print(f"Wrote {output_path}")

    if not equality["equal"]:
        raise SystemExit(
            "FAILED: batching changed an ordered Day 13 result; "
            "inspect day13_batched_equivalence.differences"
        )


if __name__ == "__main__":
    main()
