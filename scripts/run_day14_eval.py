import argparse
import sys
from dataclasses import asdict
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ripple.evaluation.day14_acceptance import (
    extract_session_c_routed_row,
    load_day13_accepted_graph_row,
    load_session_c_reference,
    relabel_ordering_comparison,
    validate_approved_five_row_configuration,
    validate_benchmark_matches_session_c,
    validate_corpus_matches_session_c,
    validate_embedding_accounting,
    validate_embedding_model_matches_session_c,
    validate_repo_matches_session_c,
)
from ripple.evaluation.graph_stabilization import (
    compare_ordered_results,
    evaluate_gates,
)
from ripple.evaluation.runner import (
    ABLATION_CONFIGS,
    build_report,
    execute_evaluation_run,
)
from scripts.run_eval import (
    benchmark_sha256,
    confirm_cost,
    load_validated_benchmark,
    render_markdown_table,
    timestamped_path,
    write_report,
)


def parse_day14_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the fixed five-row Day 14 evaluation options."""
    parser = argparse.ArgumentParser(
        description=(
            "Run and validate Ripple's approved five-row Day 14 evaluation"
        )
    )
    parser.add_argument(
        "--repo-id",
        type=int,
        required=True,
        help="repos.id of the indexed Session C repository",
    )
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=Path("data/benchmark.json"),
        help="benchmark JSON path (default: data/benchmark.json)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the paid-run confirmation prompt",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_day14_args(argv)
    benchmark_path = args.benchmark
    digest = benchmark_sha256(benchmark_path)

    session_c_report = load_session_c_reference()
    validate_repo_matches_session_c(args.repo_id, session_c_report)
    entries = load_validated_benchmark(benchmark_path, args.repo_id)
    validate_benchmark_matches_session_c(digest, session_c_report)
    validate_corpus_matches_session_c(args.repo_id, session_c_report)
    validate_embedding_model_matches_session_c(session_c_report)

    configs = list(ABLATION_CONFIGS)
    validate_approved_five_row_configuration(configs)

    uses_vector = any(
        config.use_vector and config.vector_k > 0
        for _name, config in configs
    )
    unique_questions = {entry.question for entry in entries}
    estimated_requests = len(unique_questions) if uses_vector else 0
    confirm_cost(
        question_count=len(entries),
        config_count=len(configs),
        estimated_requests=estimated_requests,
        skip=args.yes,
    )

    run = execute_evaluation_run(args.repo_id, entries, configs)
    print(render_markdown_table(run.results))

    day14_graph = next(
        result
        for result in run.results
        if result.config_name == "+ Graph expansion"
    )
    day14_cross_encoder = next(
        result
        for result in run.results
        if result.config_name == "+ Cross-encoder rerank"
    )
    routed_baseline = extract_session_c_routed_row(session_c_report)
    ordering = relabel_ordering_comparison(
        compare_ordered_results(routed_baseline, day14_graph)
    )
    gates = evaluate_gates(
        cross_encoder=asdict(day14_cross_encoder),
        day13_graph=load_day13_accepted_graph_row(),
        routed=day14_graph,
    )

    vector_config_count = sum(
        1
        for _name, config in configs
        if config.use_vector and config.vector_k > 0
    )
    embedding_accounting = validate_embedding_accounting(
        run.embedding_cache,
        unique_questions=len(unique_questions),
        entry_count=len(entries),
        vector_config_count=vector_config_count,
    )

    report = build_report(
        repo_id=args.repo_id,
        benchmark_path=str(benchmark_path),
        benchmark_sha256=digest,
        results=run.results,
        embedding_cache=run.embedding_cache,
        embedding_precomputation=run.embedding_precomputation,
        latency_methodology=run.latency_methodology,
    )
    report["day14_vs_session_c_ordering"] = ordering
    report["acceptance_gates"] = gates
    report["embedding_accounting"] = embedding_accounting
    report["day14_accepted"] = (
        ordering["equal"]
        and gates["accepted"]
        and embedding_accounting["valid"]
        and run.latency_methodology["valid"]
    )

    output_path = timestamped_path()
    write_report(report, output_path)
    print(f"Wrote {output_path}")

    if not report["day14_accepted"]:
        print(
            "Day 14 validation FAILED — see day14_vs_session_c_ordering, "
            "acceptance_gates, embedding_accounting, and "
            "latency_methodology in the written report. This is a "
            "DIAGNOSTIC artifact, not the accepted Day 14 report."
        )
        raise SystemExit(1)

    print(
        "Day 14 validation passed: ordering matches Session C, all "
        "acceptance gates hold, embedding accounting is exact, and "
        "latency methodology is valid."
    )


if __name__ == "__main__":
    main()
