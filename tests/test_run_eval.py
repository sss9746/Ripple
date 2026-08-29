import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ripple.evaluation.dataset import BenchmarkEntry
from ripple.evaluation.metrics import AggregateMetrics, CategoryMetrics
from ripple.evaluation.runner import ConfigResult
from scripts import run_eval


def test_benchmark_sha256_hashes_exact_file_bytes(tmp_path: Path) -> None:
    benchmark_path = tmp_path / "benchmark.json"
    contents = b'[{"id":"q001"}]\n'
    benchmark_path.write_bytes(contents)

    assert run_eval.benchmark_sha256(benchmark_path) == (
        hashlib.sha256(contents).hexdigest()
    )


def test_benchmark_sha256_changes_when_file_changes(tmp_path: Path) -> None:
    benchmark_path = tmp_path / "benchmark.json"
    benchmark_path.write_text('[]\n')
    first = run_eval.benchmark_sha256(benchmark_path)

    benchmark_path.write_text('[ ]\n')
    second = run_eval.benchmark_sha256(benchmark_path)

    assert first != second


def test_confirm_cost_prints_estimate_and_accepts_y(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "y")

    run_eval.confirm_cost(40, 3)

    output = capsys.readouterr().out
    assert "40 questions" in output
    assert "3 configurations" in output
    assert "approximately 120 embedding requests" in output


def test_confirm_cost_accepts_uppercase_y(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "Y")

    run_eval.confirm_cost(40, 1)


def test_confirm_cost_decline_stops_before_paid_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("builtins.input", lambda prompt: "n")

    with pytest.raises(SystemExit, match="cancelled before making API requests"):
        run_eval.confirm_cost(40, 3)


def test_confirm_cost_skip_does_not_prompt(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_if_prompted(prompt: str) -> str:
        raise AssertionError("--yes must skip the confirmation prompt")

    monkeypatch.setattr("builtins.input", fail_if_prompted)

    run_eval.confirm_cost(40, 1, skip=True)

    assert "approximately 40 embedding requests" in capsys.readouterr().out


def test_load_validated_benchmark_checks_selected_repo(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    benchmark_path = tmp_path / "benchmark.json"
    entries = [
        BenchmarkEntry(
            id="q001",
            question="What creates the VPC?",
            expected=["module.vpc"],
            category="lookup",
        )
    ]
    validation_calls: list[tuple[list[BenchmarkEntry], int]] = []

    monkeypatch.setattr(
        run_eval,
        "load_benchmark",
        lambda path: entries if path == benchmark_path else [],
    )
    monkeypatch.setattr(
        run_eval,
        "validate_addresses_exist",
        lambda loaded, repo_id: validation_calls.append((loaded, repo_id)),
    )

    result = run_eval.load_validated_benchmark(benchmark_path, repo_id=13)

    assert result is entries
    assert validation_calls == [(entries, 13)]


def test_load_validated_benchmark_propagates_missing_address_error(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    benchmark_path = tmp_path / "benchmark.json"
    entries = [
        BenchmarkEntry(
            id="q001",
            question="What creates the VPC?",
            expected=["missing.block"],
            category="lookup",
        )
    ]
    monkeypatch.setattr(run_eval, "load_benchmark", lambda path: entries)

    def reject_missing_addresses(
        loaded: list[BenchmarkEntry],
        repo_id: int,
    ) -> None:
        raise ValueError("missing.block is not indexed")

    monkeypatch.setattr(
        run_eval,
        "validate_addresses_exist",
        reject_missing_addresses,
    )

    with pytest.raises(ValueError, match="missing.block is not indexed"):
        run_eval.load_validated_benchmark(benchmark_path, repo_id=13)


def test_select_configs_returns_all_when_name_is_omitted() -> None:
    assert run_eval.select_configs(None) == run_eval.ABLATION_CONFIGS


def test_select_configs_returns_only_requested_config() -> None:
    selected = run_eval.select_configs("Vector + BM25 + RRF")

    assert len(selected) == 1
    assert selected[0] == run_eval.ABLATION_CONFIGS[2]


def test_select_configs_rejects_unknown_name() -> None:
    with pytest.raises(ValueError, match="unknown evaluation config"):
        run_eval.select_configs("Unknown")


def test_render_markdown_table_includes_overall_and_categories() -> None:
    result = ConfigResult(
        config_name="Vector + BM25 + RRF",
        config=run_eval.ABLATION_CONFIGS[2][1],
        per_question=[],
        aggregate=AggregateMetrics(
            question_count=40,
            recall_at_5=0.81234,
            recall_at_10=0.92345,
            mrr=0.73456,
            precision_at_5=0.24567,
            mean_latency_ms=1234.567,
            mean_latency_by_stage={"total_ms": 1234.567},
        ),
        by_category=[
            CategoryMetrics(
                question_count=7,
                recall_at_5=0.7,
                recall_at_10=0.9,
                mrr=0.6,
                precision_at_5=0.2,
                mean_latency_ms=1300.125,
                mean_latency_by_stage={"total_ms": 1300.125},
                category="attribute",
            ),
            CategoryMetrics(
                question_count=15,
                recall_at_5=1.0,
                recall_at_10=1.0,
                mrr=0.9,
                precision_at_5=0.3,
                mean_latency_ms=1100.0,
                mean_latency_by_stage={"total_ms": 1100.0},
                category="lookup",
            ),
        ],
    )

    markdown = run_eval.render_markdown_table([result])

    assert "| Configuration | Recall@5 | Recall@10 | MRR | P@5 | Latency (ms) |" in markdown
    assert (
        "| Vector + BM25 + RRF | 0.812 | 0.923 | 0.735 | 0.246 | 1234.57 |"
        in markdown
    )
    assert "## By category" in markdown
    assert (
        "| Vector + BM25 + RRF | attribute | 7 | 0.700 | 0.900 "
        "| 0.600 | 0.200 | 1300.12 |"
        in markdown
    )
    assert markdown.index("| attribute |") < markdown.index("| lookup |")


def test_timestamped_path_uses_utc_microseconds(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        run_eval,
        "_utc_now",
        lambda: datetime(
            2026,
            8,
            29,
            10,
            58,
            12,
            345678,
            tzinfo=timezone.utc,
        ),
    )

    assert run_eval.timestamped_path(tmp_path) == (
        tmp_path / "2026-08-29T10-58-12-345678Z.json"
    )


def test_write_report_creates_directory_and_valid_json(tmp_path: Path) -> None:
    path = tmp_path / "missing" / "results" / "report.json"
    report = {
        "schema_version": 1,
        "repo_id": 13,
        "results": [{"config_name": "Vector only"}],
    }

    run_eval.write_report(report, path)

    assert path.is_file()
    assert json.loads(path.read_text()) == report


def test_write_report_refuses_to_overwrite_existing_file(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixed_time = datetime(2026, 8, 29, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(run_eval, "_utc_now", lambda: fixed_time)
    first_path = run_eval.timestamped_path(tmp_path)
    run_eval.write_report({"existing": True}, first_path)
    original = first_path.read_text()

    second_path = run_eval.timestamped_path(tmp_path)
    assert second_path == first_path

    with pytest.raises(FileExistsError):
        run_eval.write_report({"existing": False}, second_path)

    assert first_path.read_text() == original


def test_main_runs_only_requested_config_and_writes_one_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    benchmark_path = tmp_path / "benchmark.json"
    benchmark_path.write_text("[]")
    entries = [
        BenchmarkEntry(
            id="q001",
            question="What creates the VPC?",
            expected=["module.vpc"],
            category="lookup",
        )
    ]
    calls: list[tuple[int, list[BenchmarkEntry], object, str]] = []
    selected_result = object()
    output_path = tmp_path / "results" / "result.json"
    written: list[tuple[dict, Path]] = []

    monkeypatch.setattr(
        run_eval,
        "load_validated_benchmark",
        lambda path, repo_id: entries,
    )
    monkeypatch.setattr(run_eval, "confirm_cost", lambda **kwargs: None)

    def fake_run_benchmark(
        repo_id: int,
        entries: list[BenchmarkEntry],
        config: object,
        config_name: str,
    ) -> object:
        calls.append((repo_id, entries, config, config_name))
        return selected_result

    monkeypatch.setattr(run_eval, "run_benchmark", fake_run_benchmark)
    monkeypatch.setattr(
        run_eval,
        "render_markdown_table",
        lambda results: "MARKDOWN RESULTS",
    )
    monkeypatch.setattr(
        run_eval,
        "build_report",
        lambda **kwargs: {"results": kwargs["results"]},
    )
    monkeypatch.setattr(run_eval, "timestamped_path", lambda: output_path)
    monkeypatch.setattr(
        run_eval,
        "write_report",
        lambda report, path: written.append((report, path)),
    )

    run_eval.main(
        [
            "--repo-id",
            "13",
            "--benchmark",
            str(benchmark_path),
            "--config",
            "Vector + BM25 + RRF",
            "--yes",
        ]
    )

    assert len(calls) == 1
    assert calls[0] == (
        13,
        entries,
        run_eval.ABLATION_CONFIGS[2][1],
        "Vector + BM25 + RRF",
    )
    assert written == [({"results": [selected_result]}, output_path)]


def test_main_decline_stops_before_running_benchmark(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    benchmark_path = tmp_path / "benchmark.json"
    benchmark_path.write_text("[]")
    entry = BenchmarkEntry(
        id="q001",
        question="What creates the VPC?",
        expected=["module.vpc"],
        category="lookup",
    )
    monkeypatch.setattr(
        run_eval,
        "load_validated_benchmark",
        lambda path, repo_id: [entry],
    )
    monkeypatch.setattr("builtins.input", lambda prompt: "n")

    def fail_if_run(*args: object, **kwargs: object) -> None:
        raise AssertionError("benchmark must not run after declined confirmation")

    monkeypatch.setattr(run_eval, "run_benchmark", fail_if_run)

    with pytest.raises(SystemExit, match="cancelled before making API requests"):
        run_eval.main(
            [
                "--repo-id",
                "13",
                "--benchmark",
                str(benchmark_path),
            ]
        )


def test_main_runs_all_three_configs_when_config_is_omitted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    benchmark_path = tmp_path / "benchmark.json"
    benchmark_path.write_text("[]")
    entry = BenchmarkEntry(
        id="q001",
        question="What creates the VPC?",
        expected=["module.vpc"],
        category="lookup",
    )
    config_names: list[str] = []
    output_path = tmp_path / "report.json"
    written: list[tuple[dict, Path]] = []

    monkeypatch.setattr(
        run_eval,
        "load_validated_benchmark",
        lambda path, repo_id: [entry],
    )
    monkeypatch.setattr(run_eval, "confirm_cost", lambda **kwargs: None)

    def fake_run_benchmark(**kwargs: object) -> str:
        config_name = str(kwargs["config_name"])
        config_names.append(config_name)
        return config_name

    monkeypatch.setattr(run_eval, "run_benchmark", fake_run_benchmark)
    monkeypatch.setattr(run_eval, "render_markdown_table", lambda results: "table")
    monkeypatch.setattr(
        run_eval,
        "build_report",
        lambda **kwargs: {"results": kwargs["results"]},
    )
    monkeypatch.setattr(run_eval, "timestamped_path", lambda: output_path)
    monkeypatch.setattr(
        run_eval,
        "write_report",
        lambda report, path: written.append((report, path)),
    )

    run_eval.main(
        ["--repo-id", "13", "--benchmark", str(benchmark_path), "--yes"]
    )

    assert config_names == [name for name, _config in run_eval.ABLATION_CONFIGS]
    assert written == [({"results": config_names}, output_path)]
