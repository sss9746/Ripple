import argparse
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ripple.llm.embeddings import OpenAIEmbeddingProvider
from ripple.llm.generate import answer_question
from ripple.retrieval.pgvector_store import PgVectorStore


DEFAULT_TOP_K = 8


def ask(repo_id: int, question: str, top_k: int = DEFAULT_TOP_K) -> str:
    embedder = OpenAIEmbeddingProvider()
    [question_embedding] = embedder.embed([question])

    store = PgVectorStore()
    blocks = store.query(repo_id, question_embedding, top_k)

    if not blocks:
        return "No indexed resources found for this repo — nothing to answer from."

    return answer_question(question, blocks)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Ask a question about an indexed Terraform repo"
    )
    parser.add_argument(
        "repo_id",
        type=int,
        help="repos.id of the indexed repo to query",
    )
    parser.add_argument("question", help="Natural-language question")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    args = parser.parse_args(argv)

    print(ask(args.repo_id, args.question, args.top_k))


if __name__ == "__main__":
    main()
