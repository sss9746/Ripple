import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ripple import db
from ripple.config import RetrievalConfig
from ripple.llm.generate import answer_question, render_answer
from ripple.retrieval import pipeline


NO_RESULTS_MESSAGE = (
    "No indexed resources found for this repo — nothing to answer from."
)


def ask(
    repo_id: int,
    question: str,
    config: RetrievalConfig | None = None,
) -> str:
    config = config or RetrievalConfig()

    result = pipeline.run_pipeline(
        repo_id,
        question,
        config,
    )

    if result.blocks:
        repo = db.fetch_repo(repo_id)
        repo_root = repo[2] if repo else None
        structured_answer = answer_question(
            question,
            result.blocks,
            repo_root,
        )
        answer = render_answer(structured_answer)
    else:
        answer = None

    db.insert_query_log(
        repo_id=repo_id,
        question=question,
        config_json=result.config_json,
        stages_json=result.stages_json,
        latency_json=result.latency_json,
        answer=answer,
    )

    if answer is None:
        return NO_RESULTS_MESSAGE

    return answer


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Ask a question about an indexed Terraform repo"
    )
    parser.add_argument(
        "repo_id",
        type=int,
        help="repos.id of the indexed repo to query",
    )
    parser.add_argument(
        "question",
        help="Natural-language question",
    )
    parser.add_argument(
        "--final-k",
        type=int,
        default=None,
        help=(
            "Maximum final blocks sent to answer generation "
            "(default: 8)"
        ),
    )
    args = parser.parse_args(argv)

    if args.final_k is None:
        config = RetrievalConfig()
    else:
        config = RetrievalConfig(final_k=args.final_k)

    print(ask(args.repo_id, args.question, config))


if __name__ == "__main__":
    main()
