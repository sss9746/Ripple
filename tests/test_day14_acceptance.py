from dataclasses import replace

import pytest

from ripple.evaluation import day14_acceptance, runner


def session_c_report() -> dict:
    return {
        "repo_id": 13,
        "benchmark_sha256": "benchmark-hash",
        "embedding_model": "text-embedding-3-small",
        "corpus": {
            "repo_name": "vpc-complete",
            "source_url": None,
            "local_path": "/tmp/vpc-complete",
            "git_revision": "git-hash",
            "indexed_corpus_sha256": "corpus-hash",
            "resource_count": 114,
        },
        "results": [
            {
                "config_name": (
                    "+ Batched and intent-routed graph expansion"
                ),
                "per_question": [],
            }
        ],
    }


def test_validate_approved_five_row_configuration_accepts_current_rows(
) -> None:
    day14_acceptance.validate_approved_five_row_configuration(
        runner.ABLATION_CONFIGS
    )


@pytest.mark.parametrize("change", ["rename", "reorder", "missing", "extra"])
def test_validate_approved_five_row_configuration_rejects_row_drift(
    change: str,
) -> None:
    configs = list(runner.ABLATION_CONFIGS)
    if change == "rename":
        configs[0] = ("Renamed", configs[0][1])
    elif change == "reorder":
        configs[0], configs[1] = configs[1], configs[0]
    elif change == "missing":
        configs.pop()
    else:
        configs.append(("Extra", configs[0][1]))

    with pytest.raises(ValueError, match="approved Day 14"):
        day14_acceptance.validate_approved_five_row_configuration(configs)


def test_validate_approved_five_row_configuration_rejects_field_drift(
) -> None:
    configs = list(runner.ABLATION_CONFIGS)
    name, config = configs[-1]
    configs[-1] = (name, replace(config, graph_route_by_intent=False))

    with pytest.raises(ValueError, match="approved Day 14"):
        day14_acceptance.validate_approved_five_row_configuration(configs)


def test_validate_repo_matches_session_c(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        day14_acceptance.db,
        "fetch_repo",
        lambda repo_id: (
            "vpc-complete",
            None,
            "/tmp/vpc-complete",
        ),
    )

    day14_acceptance.validate_repo_matches_session_c(
        13,
        session_c_report(),
    )

    with pytest.raises(ValueError, match="repo_id mismatch"):
        day14_acceptance.validate_repo_matches_session_c(
            99,
            session_c_report(),
        )


def test_validate_benchmark_and_embedding_model_reject_drift() -> None:
    report = session_c_report()

    with pytest.raises(ValueError, match="benchmark fingerprint"):
        day14_acceptance.validate_benchmark_matches_session_c(
            "different-hash",
            report,
        )

    report["embedding_model"] = "different-model"
    with pytest.raises(ValueError, match="embedding model"):
        day14_acceptance.validate_embedding_model_matches_session_c(report)


def test_validate_corpus_matches_session_c(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        day14_acceptance.runner,
        "indexed_corpus_fingerprint",
        lambda repo_id: ("corpus-hash", 114),
    )
    monkeypatch.setattr(
        day14_acceptance.db,
        "fetch_repo",
        lambda repo_id: ("vpc-complete", None, "/tmp/vpc-complete"),
    )
    monkeypatch.setattr(
        day14_acceptance.runner,
        "_corpus_git_revision",
        lambda local_path: "git-hash",
    )

    day14_acceptance.validate_corpus_matches_session_c(
        13,
        session_c_report(),
    )

    monkeypatch.setattr(
        day14_acceptance.runner,
        "indexed_corpus_fingerprint",
        lambda repo_id: ("changed", 114),
    )
    with pytest.raises(ValueError, match="indexed corpus"):
        day14_acceptance.validate_corpus_matches_session_c(
            13,
            session_c_report(),
        )


def test_relabel_ordering_comparison_replaces_generic_labels() -> None:
    result = day14_acceptance.relabel_ordering_comparison(
        {
            "equal": False,
            "questions_checked": 1,
            "differences": [
                {
                    "entry_id": "q001",
                    "accepted": ["old.block"],
                    "batched": ["new.block"],
                }
            ],
        }
    )

    assert result["differences"] == [
        {
            "entry_id": "q001",
            "session_c_routed": ["old.block"],
            "day14_row5": ["new.block"],
        }
    ]


def test_validate_embedding_accounting_reports_pass_and_failure() -> None:
    passing = day14_acceptance.validate_embedding_accounting(
        {
            "provider_calls": 40,
            "cache_hits": 200,
            "unique_questions": 40,
        },
        unique_questions=40,
        entry_count=40,
        vector_config_count=5,
    )
    failing = day14_acceptance.validate_embedding_accounting(
        {
            "provider_calls": 41,
            "cache_hits": 160,
            "unique_questions": 40,
        },
        unique_questions=40,
        entry_count=40,
        vector_config_count=5,
    )

    assert passing["valid"] is True
    assert passing["expected_cache_hits"] == 200
    assert failing["valid"] is False
