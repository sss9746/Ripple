import argparse
import hashlib
import json
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ripple.config import RetrievalConfig
from ripple.evaluation.dataset import (
    BenchmarkEntry,
    load_benchmark,
    validate_addresses_exist,
)
from ripple.evaluation.runner import (
    ABLATION_CONFIGS,
    ConfigResult,
    build_report,
    run_benchmark,
)


CONFIG_NAMES = tuple(name for name, _config in ABLATION_CONFIGS)
DEFAULT_RESULTS_DIR = Path("data/eval_results")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def benchmark_sha256(benchmark_path: Path) -> str:
    """Return the SHA-256 fingerprint of the exact benchmark file bytes."""
    return hashlib.sha256(benchmark_path.read_bytes()).hexdigest()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse evaluation CLI options without running a benchmark."""
    parser = argparse.ArgumentParser(
        description="Evaluate Ripple retrieval against a benchmark dataset"
    )
    parser.add_argument(
        "--repo-id",
        type=int,
        required=True,
        help="repos.id of the indexed repository to evaluate",
    )
    parser.add_argument(
        "--benchmark",
        type=Path,
        default=Path("data/benchmark.json"),
        help="benchmark JSON path (default: data/benchmark.json)",
    )
    parser.add_argument(
        "--config",
        choices=CONFIG_NAMES,
        help="run one retrieval configuration (default: run all three)",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="skip the paid-run confirmation prompt",
    )
    return parser.parse_args(argv)


def confirm_cost(
    question_count: int,
    config_count: int,
    *,
    skip: bool = False,
) -> None:
    """Display the paid-work estimate and require explicit confirmation."""
    estimated_requests = question_count * config_count
    print(
        "Evaluation plan: "
        f"{question_count} questions × {config_count} configurations "
        f"= approximately {estimated_requests} embedding requests."
    )

    if skip:
        return

    response = input("Continue with the paid evaluation? [y/N]: ")
    if response.strip().lower() != "y":
        raise SystemExit("Evaluation cancelled before making API requests.")


def load_validated_benchmark(
    benchmark_path: Path,
    repo_id: int,
) -> list[BenchmarkEntry]:
    """Load benchmark entries and verify their labels against one repository."""
    entries = load_benchmark(benchmark_path)
    validate_addresses_exist(entries, repo_id)
    return entries


def select_configs(
    config_name: str | None,
) -> list[tuple[str, RetrievalConfig]]:
    """Return the requested ablation config, or all configs when omitted."""
    if config_name is None:
        return list(ABLATION_CONFIGS)

    configs_by_name = dict(ABLATION_CONFIGS)
    try:
        return [(config_name, configs_by_name[config_name])]
    except KeyError as exc:
        raise ValueError(f"unknown evaluation config: {config_name}") from exc


def render_markdown_table(results: list[ConfigResult]) -> str:
    """Render overall and per-category evaluation metrics as Markdown."""
    lines = [
        "## Overall",
        "",
        "| Configuration | Recall@5 | Recall@10 | MRR | P@5 | Latency (ms) |",
        "|---|---:|---:|---:|---:|---:|",
    ]

    for result in results:
        metrics = result.aggregate
        lines.append(
            f"| {result.config_name} "
            f"| {metrics.recall_at_5:.3f} "
            f"| {metrics.recall_at_10:.3f} "
            f"| {metrics.mrr:.3f} "
            f"| {metrics.precision_at_5:.3f} "
            f"| {metrics.mean_latency_ms:.2f} |"
        )

    lines.extend(
        [
            "",
            "## By category",
            "",
            "| Configuration | Category | Questions | Recall@5 | Recall@10 | MRR | P@5 | Latency (ms) |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )

    for result in results:
        for metrics in result.by_category:
            lines.append(
                f"| {result.config_name} "
                f"| {metrics.category} "
                f"| {metrics.question_count} "
                f"| {metrics.recall_at_5:.3f} "
                f"| {metrics.recall_at_10:.3f} "
                f"| {metrics.mrr:.3f} "
                f"| {metrics.precision_at_5:.3f} "
                f"| {metrics.mean_latency_ms:.2f} |"
            )

    return "\n".join(lines)


def timestamped_path(
    output_dir: Path = DEFAULT_RESULTS_DIR,
) -> Path:
    """Build a collision-resistant UTC path for one evaluation report."""
    timestamp = _utc_now().astimezone(timezone.utc).strftime(
        "%Y-%m-%dT%H-%M-%S-%fZ"
    )
    return output_dir / f"{timestamp}.json"


def write_report(report: dict[str, Any], path: Path) -> None:
    """Create one JSON report without overwriting an existing result."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as file:
        json.dump(report, file, indent=2, default=asdict)
        file.write("\n")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    benchmark_path = args.benchmark
    digest = benchmark_sha256(benchmark_path)
    entries = load_validated_benchmark(benchmark_path, args.repo_id)
    configs = select_configs(args.config)

    confirm_cost(
        question_count=len(entries),
        config_count=len(configs),
        skip=args.yes,
    )

    results = [
        run_benchmark(
            repo_id=args.repo_id,
            entries=entries,
            config=config,
            config_name=config_name,
        )
        for config_name, config in configs
    ]

    print(render_markdown_table(results))
    report = build_report(
        repo_id=args.repo_id,
        benchmark_path=str(benchmark_path),
        benchmark_sha256=digest,
        results=results,
    )
    output_path = timestamped_path()
    write_report(report, output_path)
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
