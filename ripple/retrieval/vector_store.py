from dataclasses import dataclass
from typing import Protocol


@dataclass
class RetrievedBlock:
    id: int
    address: str
    file_path: str
    start_line: int
    end_line: int
    body: str
    embed_text: str
    score: float | None
    graph_relationship: str | None = None
    graph_origin_address: str | None = None
    graph_ref_text: str | None = None
    graph_score_status: str | None = None


class VectorStore(Protocol):
    def upsert(self, repo_id: int, rows) -> None:
        ...

    def query(
        self,
        repo_id: int,
        embedding: list[float],
        k: int,
    ) -> list[RetrievedBlock]:
        ...

    def delete_namespace(self, repo_id: int) -> None:
        ...
