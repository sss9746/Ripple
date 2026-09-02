from pathlib import Path

import pytest

from ripple.evaluation.dataset import BenchmarkEntry
from ripple.evaluation.metrics import AggregateMetrics, CategoryMetrics
from ripple.evaluation.runner import ConfigResult, EvaluationRun
from scripts import run_day14_eval


def _result(config_name: str, config_index: int) -> ConfigResult:
    aggregate = AggregateMetrics(
        question_count=1,
        recall_at_5=1.0,
        recall_at_10=1.0,
        mrr=1.0,
        precision_at_5=0.2,
        mean_latency_ms=10.0,
        mean_latency_by_stage={"total_ms": 10.0, "graph_ms": 2.0},
    )
    relational = CategoryMetrics(
        question_count=1,
        recall_at_5=1.0,
        recall_at_10=1.0,
        mrr=1.0,
        precision_at_5=0.2,
        mean_latency_ms=10.0,
        mean_latency_by_stage={"total_ms": 10.0, "graph_ms": 2.0},
        category="relational",
    )
    return ConfigResult(
        config_name=config_name,
        config=run_day14_eval.ABLATION_CONFIGS[config_index][1],
        per_question=[],
        aggregate=aggregate,
        by_category=[relational],
    )


def _install_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> tuple[list[str], list[tuple[dict, Path]]]:
    entry = BenchmarkEntry(
        id="q001",
        question="What creates the VPC?",
        expected=["module.vpc"],
        category="relational",
    )
    events: list[str] = []
    written: list[tuple[dict, Path]] = []
    output_path = tmp_path / "day14.json"
    results = [
        _result(name, index)
        for index, (name, _config) in enumerate(
            run_day14_eval.ABLATION_CONFIGS
        )
    ]

    monkeypatch.setattr(
        run_day14_eval,
        "benchmark_sha256",
        lambda path: "benchmark-hash",
    )
    monkeypatch.setattr(
        run_day14_eval,
        "load_session_c_reference",
        lambda: {"session": "c"},
    )
    monkeypatch.setattr(
        run_day14_eval,
        "validate_repo_matches_session_c",
        lambda repo_id, report: events.append("repo"),
    )
    monkeypatch.setattr(
        run_day14_eval,
        "load_validated_benchmark",
        lambda path, repo_id: events.append("benchmark-load") or [entry],
    )
    monkeypatch.setattr(
        run_day14_eval,
        "validate_benchmark_matches_session_c",
        lambda digest, report: events.append("benchmark"),
    )
    monkeypatch.setattr(
        run_day14_eval,
        "validate_corpus_matches_session_c",
        lambda repo_id, report: events.append("corpus"),
    )
    monkeypatch.setattr(
        run_day14_eval,
        "validate_embedding_model_matches_session_c",
        lambda report: events.append("model"),
    )
    monkeypatch.setattr(
        run_day14_eval,
        "validate_approved_five_row_configuration",
        lambda configs: events.append("configs"),
    )
    monkeypatch.setattr(
        run_day14_eval,
        "confirm_cost",
        lambda **kwargs: events.append("confirm"),
    )

    def fake_execute(*args: object) -> EvaluationRun:
        events.append("execute")
        return EvaluationRun(
            results=results,
            embedding_cache={
                "provider_calls": 1,
                "cache_hits": 5,
                "unique_questions": 1,
            },
            embedding_precomputation={"provider_calls": 1},
            latency_methodology={"valid": True},
        )

    monkeypatch.setattr(
        run_day14_eval,
        "execute_evaluation_run",
        fake_execute,
    )
    monkeypatch.setattr(
        run_day14_eval,
        "render_markdown_table",
        lambda results: "DAY 14 TABLE",
    )
    monkeypatch.setattr(
        run_day14_eval,
        "extract_session_c_routed_row",
        lambda report: {"per_question": []},
    )
    monkeypatch.setattr(
        run_day14_eval,
        "compare_ordered_results",
        lambda baseline, result: {
            "equal": True,
            "differences": [],
        },
    )
    monkeypatch.setattr(
        run_day14_eval,
        "load_day13_accepted_graph_row",
        lambda: {"aggregate": {}, "by_category": []},
    )
    monkeypatch.setattr(
        run_day14_eval,
        "evaluate_gates",
        lambda **kwargs: {"accepted": True},
    )
    monkeypatch.setattr(
        run_day14_eval,
        "build_report",
        lambda **kwargs: {
            "schema_version": 3,
            "generic_arguments": kwargs,
        },
    )
    monkeypatch.setattr(
        run_day14_eval,
        "timestamped_path",
        lambda: output_path,
    )
    monkeypatch.setattr(
        run_day14_eval,
        "write_report",
        lambda report, path: written.append((report, path)),
    )
    return events, written


def test_main_validates_confirms_then_executes_and_writes_accepted_report(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    events, written = _install_happy_path(monkeypatch, tmp_path)

    run_day14_eval.main(
        ["--repo-id", "13", "--benchmark", "benchmark.json", "--yes"]
    )

    assert events == [
        "repo",
        "benchmark-load",
        "benchmark",
        "corpus",
        "model",
        "configs",
        "confirm",
        "execute",
    ]
    assert len(written) == 1
    report, output_path = written[0]
    assert output_path == tmp_path / "day14.json"
    assert report["day14_accepted"] is True
    assert report["day14_vs_session_c_ordering"]["equal"] is True
    assert report["acceptance_gates"]["accepted"] is True
    assert report["embedding_accounting"]["valid"] is True
    assert report["generic_arguments"]["embedding_precomputation"] == {
        "provider_calls": 1
    }
    assert report["generic_arguments"]["latency_methodology"] == {
        "valid": True
    }
    output = capsys.readouterr().out
    assert "DAY 14 TABLE" in output
    assert "Day 14 validation passed" in output


def test_main_decline_never_executes_evaluation(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events, _written = _install_happy_path(monkeypatch, tmp_path)

    def decline(**kwargs: object) -> None:
        events.append("confirm")
        raise SystemExit("declined")

    monkeypatch.setattr(run_day14_eval, "confirm_cost", decline)

    with pytest.raises(SystemExit, match="declined"):
        run_day14_eval.main(["--repo-id", "13"])

    assert events[-1] == "confirm"
    assert "execute" not in events


@pytest.mark.parametrize(
    "validator_name",
    [
        "validate_repo_matches_session_c",
        "validate_benchmark_matches_session_c",
        "validate_corpus_matches_session_c",
        "validate_embedding_model_matches_session_c",
    ],
)
def test_main_provenance_mismatch_stops_before_confirmation_and_execution(
    validator_name: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    events, _written = _install_happy_path(monkeypatch, tmp_path)

    def reject(*args: object) -> None:
        raise ValueError("provenance drift")

    monkeypatch.setattr(run_day14_eval, validator_name, reject)

    with pytest.raises(ValueError, match="provenance drift"):
        run_day14_eval.main(["--repo-id", "13", "--yes"])

    assert "confirm" not in events
    assert "execute" not in events


@pytest.mark.parametrize(
    "failure",
    ["ordering", "gates", "accounting", "methodology"],
)
def test_main_writes_diagnostic_report_before_failed_acceptance_exit(
    failure: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _events, written = _install_happy_path(monkeypatch, tmp_path)

    if failure == "ordering":
        monkeypatch.setattr(
            run_day14_eval,
            "compare_ordered_results",
            lambda baseline, result: {
                "equal": False,
                "differences": [],
            },
        )
    elif failure == "gates":
        monkeypatch.setattr(
            run_day14_eval,
            "evaluate_gates",
            lambda **kwargs: {"accepted": False},
        )
    elif failure == "accounting":
        monkeypatch.setattr(
            run_day14_eval,
            "validate_embedding_accounting",
            lambda *args, **kwargs: {"valid": False},
        )
    else:
        original_execute = run_day14_eval.execute_evaluation_run

        def invalid_methodology(*args: object) -> EvaluationRun:
            run = original_execute(*args)
            run.latency_methodology = {"valid": False}
            return run

        monkeypatch.setattr(
            run_day14_eval,
            "execute_evaluation_run",
            invalid_methodology,
        )

    with pytest.raises(SystemExit) as error:
        run_day14_eval.main(["--repo-id", "13", "--yes"])

    assert error.value.code == 1
    assert len(written) == 1
    assert written[0][0]["day14_accepted"] is False
