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
    score: float


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
