import os
from typing import Protocol

from dotenv import load_dotenv
from openai import OpenAI


load_dotenv()


EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM = 1536
EMBEDDING_BATCH_SIZE = 100


class EmbeddingProvider(Protocol):
    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class OpenAIEmbeddingProvider:
    def __init__(self, client: OpenAI | None = None) -> None:
        if client is not None:
            self._client = client
            return

        api_key = os.environ.get("OPENAI_API_KEY")

        if not api_key:
            raise RuntimeError(
                "OPENAI_API_KEY environment variable is not set"
            )

        self._client = OpenAI(api_key=api_key)

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        embeddings: list[list[float] | None] = [None] * len(texts)

        for start in range(0, len(texts), EMBEDDING_BATCH_SIZE):
            batch = texts[start : start + EMBEDDING_BATCH_SIZE]

            response = self._client.embeddings.create(
                model=EMBEDDING_MODEL,
                input=batch,
            )

            if len(response.data) != len(batch):
                raise ValueError(
                    f"Embedding provider returned {len(response.data)} "
                    f"embeddings for {len(batch)} inputs"
                )

            for item in response.data:
                if not 0 <= item.index < len(batch):
                    raise ValueError(
                        f"Embedding provider returned invalid index {item.index}"
                    )

                position = start + item.index

                if embeddings[position] is not None:
                    raise ValueError(
                        f"Embedding provider returned duplicate index {item.index}"
                    )

                if len(item.embedding) != EMBEDDING_DIM:
                    raise ValueError(
                        f"Embedding provider returned a "
                        f"{len(item.embedding)}-dimension vector, "
                        f"expected {EMBEDDING_DIM}"
                    )

                embeddings[position] = item.embedding

        if any(embedding is None for embedding in embeddings):
            raise ValueError(
                "Embedding provider response did not cover every input"
            )

        return [
            embedding
            for embedding in embeddings
            if embedding is not None
        ]


class CachingEmbeddingProvider:
    """Cache embeddings by exact input text for one evaluation process."""

    def __init__(self, provider: EmbeddingProvider) -> None:
        self._provider = provider
        self._cache: dict[str, list[float]] = {}
        self.request_count = 0
        self.cache_hit_count = 0

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []

        missing = list(dict.fromkeys(
            text for text in texts if text not in self._cache
        ))

        if missing:
            embeddings = self._provider.embed(missing)
            if len(embeddings) != len(missing):
                raise ValueError(
                    "Embedding provider returned the wrong result count"
                )

            self.request_count += 1
            self._cache.update(zip(missing, embeddings, strict=True))

        self.cache_hit_count += sum(
            text in self._cache and text not in missing
            for text in texts
        )
        return [self._cache[text] for text in texts]
