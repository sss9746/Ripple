import re
from dataclasses import dataclass

from rank_bm25 import BM25Okapi

from ripple import db
from ripple.retrieval.vector_store import RetrievedBlock

TOKEN_RE = re.compile(r"[A-Za-z0-9_.\-]+")
SPLIT_RE = re.compile(r"[._\-]")
DELIMITER_CHARS = "._-"


def tokenize(text: str) -> list[str]:
    """Emit full tokens and useful Terraform identifier parts."""
    raw_tokens = TOKEN_RE.findall(text.lower())
    output: list[str] = []

    for token in raw_tokens:
        output.append(token)

        if any(character in token for character in DELIMITER_CHARS):
            parts = SPLIT_RE.split(token)
            output.extend(part for part in parts if len(part) > 1)

    return output


@dataclass
class BM25Document:
    id: int
    address: str
    file_path: str
    start_line: int
    end_line: int
    body: str
    embed_text: str
    tokens: frozenset[str]


class BM25Index:
    """An in-memory BM25 index for one repository."""

    def __init__(
        self,
        documents: list[BM25Document],
        model: BM25Okapi | None,
    ) -> None:
        self._documents = documents
        self._model = model

    def query(
        self,
        question: str,
        k: int,
    ) -> list[RetrievedBlock]:
        if self._model is None or k <= 0:
            return []

        query_tokens = tokenize(question)
        query_token_set = set(query_tokens)

        if not query_token_set:
            return []

        candidate_indexes = [
            index
            for index, document in enumerate(self._documents)
            if document.tokens & query_token_set
        ]

        if not candidate_indexes:
            return []

        scores = self._model.get_scores(query_tokens)

        ranked_indexes = sorted(
            candidate_indexes,
            key=lambda index: (
                -scores[index],
                self._documents[index].address,
            ),
        )

        return [
            RetrievedBlock(
                id=self._documents[index].id,
                address=self._documents[index].address,
                file_path=self._documents[index].file_path,
                start_line=self._documents[index].start_line,
                end_line=self._documents[index].end_line,
                body=self._documents[index].body,
                embed_text=self._documents[index].embed_text,
                score=float(scores[index]),
            )
            for index in ranked_indexes[:k]
        ]


def build_index(repo_id: int) -> BM25Index:
    """Build an in-memory BM25 index from one repository's saved resources."""
    rows = db.fetch_bm25_documents(repo_id)

    if not rows:
        return BM25Index(documents=[], model=None)

    tokenized_corpus = [tokenize(row[6]) for row in rows]

    documents = [
        BM25Document(
            id=row[0],
            address=row[1],
            file_path=row[2],
            start_line=row[3],
            end_line=row[4],
            body=row[5],
            embed_text=row[6],
            tokens=frozenset(tokenized_corpus[index]),
        )
        for index, row in enumerate(rows)
    ]

    model = BM25Okapi(tokenized_corpus)

    return BM25Index(
        documents=documents,
        model=model,
    )
