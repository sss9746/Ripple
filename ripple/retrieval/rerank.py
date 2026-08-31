import dataclasses
import time
from typing import Protocol

from ripple.retrieval.vector_store import RetrievedBlock


class Reranker(Protocol):
    def rerank(
        self,
        question: str,
        candidates: list[RetrievedBlock],
    ) -> list[RetrievedBlock]:
        ...


class CrossEncoderReranker:
    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-base",
        max_length: int = 512,
        model: object | None = None,
    ) -> None:
        self._model_name = model_name
        self._max_length = max_length
        self._model = model
        self.prepare_ms: float | None = None

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import CrossEncoder

            self._model = CrossEncoder(
                self._model_name,
                max_length=self._max_length,
            )

        return self._model

    def rerank(
        self,
        question: str,
        candidates: list[RetrievedBlock],
    ) -> list[RetrievedBlock]:
        if not candidates:
            return []

        model = self._get_model()
        pairs = [
            (question, candidate.embed_text)
            for candidate in candidates
        ]

        raw_scores = model.predict(pairs)
        scores = [float(score) for score in raw_scores]

        if len(scores) != len(candidates):
            raise ValueError(
                f"reranker model returned {len(scores)} scores "
                f"for {len(candidates)} candidates"
            )

        reranked = [
            dataclasses.replace(candidate, score=score)
            for candidate, score in zip(candidates, scores)
        ]

        return sorted(
            reranked,
            key=lambda block: (-block.score, block.address),
        )

    def prepare(self) -> None:
        if self.prepare_ms is not None:
            return

        start = time.perf_counter()
        model = self._get_model()
        model.predict([("prepare", "prepare")])
        self.prepare_ms = (
            time.perf_counter() - start
        ) * 1000

    def _resolved_model_revision(self) -> str:
        for attribute_path in (
            ("model", "config", "_commit_hash"),
            ("config", "_commit_hash"),
        ):
            value = self._model
            for attribute in attribute_path:
                value = getattr(value, attribute, None)
                if value is None:
                    break

            if value:
                return str(value)

        return "unavailable"

    def describe(self) -> dict:
        from importlib.metadata import version as installed_version

        return {
            "model_name": self._model_name,
            "max_length": self._max_length,
            "sentence_transformers_version": installed_version(
                "sentence-transformers"
            ),
            "model_revision": self._resolved_model_revision(),
            "prepare_ms": self.prepare_ms,
            "enabled": True,
        }
