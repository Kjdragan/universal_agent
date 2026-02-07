"""
Embedding providers for vector memory.

Supports OpenAI and local Sentence Transformers.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Optional

# Lazy imports to avoid heavy dependencies at module load
_openai_client: Optional["OpenAI"] = None  # type: ignore
_st_model: Optional["SentenceTransformer"] = None  # type: ignore


class EmbeddingProvider(ABC):
    """Abstract base class for embedding providers."""

    @property
    @abstractmethod
    def dimensions(self) -> int:
        """Return the embedding dimension for this provider."""
        ...

    @abstractmethod
    def embed(self, text: str) -> list[float]:
        """Generate embedding vector for text."""
        ...

    def embed_query(self, text: str) -> list[float]:
        """Generate embedding for a search query."""
        return self.embed(text)

    def embed_document(self, text: str) -> list[float]:
        """Generate embedding for a memory/document payload."""
        return self.embed(text)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts. Override for efficiency."""
        return [self.embed(t) for t in texts]

    def embed_document_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate document embeddings for multiple payloads."""
        return self.embed_batch(texts)


class OpenAIEmbeddings(EmbeddingProvider):
    """OpenAI embeddings using text-embedding-3-small (or configurable)."""

    # Dimension lookup for common models
    MODEL_DIMS = {
        "text-embedding-3-small": 1536,
        "text-embedding-3-large": 3072,
        "text-embedding-ada-002": 1536,
    }

    def __init__(self, model: str = "text-embedding-3-small", api_key: Optional[str] = None):
        self.model = model
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
        self._client: Optional["OpenAI"] = None  # type: ignore

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(api_key=self._api_key)
        return self._client

    @property
    def dimensions(self) -> int:
        return self.MODEL_DIMS.get(self.model, 1536)

    def embed(self, text: str) -> list[float]:
        client = self._get_client()
        response = client.embeddings.create(model=self.model, input=text)
        return response.data[0].embedding

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        client = self._get_client()
        response = client.embeddings.create(model=self.model, input=texts)
        return [item.embedding for item in response.data]


class SentenceTransformerEmbeddings(EmbeddingProvider):
    """Local embeddings using Sentence Transformers (no API required)."""

    def __init__(self, model: str = "all-MiniLM-L6-v2", device: str = "cpu"):
        self.model_name = model
        self.device = device
        self._model: Optional["SentenceTransformer"] = None  # type: ignore
        self._dims: Optional[int] = None

    def _get_model(self):
        if self._model is None:
            from sentence_transformers import SentenceTransformer

            self._model = SentenceTransformer(self.model_name, device=self.device)
            self._dims = self._model.get_sentence_embedding_dimension()
        return self._model

    @property
    def dimensions(self) -> int:
        if self._dims is None:
            self._get_model()
        return self._dims or 384  # Default for MiniLM

    def embed(self, text: str) -> list[float]:
        model = self._get_model()
        embedding = model.encode(text, convert_to_numpy=True)
        return embedding.tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._get_model()
        embeddings = model.encode(texts, convert_to_numpy=True)
        return [e.tolist() for e in embeddings]


def get_embedding_provider(
    provider: Optional[str] = None,
    model: Optional[str] = None,
) -> EmbeddingProvider:
    """
    Factory function to get an embedding provider.

    Args:
        provider: "openai" or "sentence-transformers" (default from env or "sentence-transformers")
        model: Model name (provider-specific defaults if not specified)

    Returns:
        EmbeddingProvider instance
    """
    provider = provider or os.getenv("UA_EMBEDDING_PROVIDER", "sentence-transformers")

    if provider == "openai":
        model = model or os.getenv("UA_EMBEDDING_MODEL", "text-embedding-3-small")
        return OpenAIEmbeddings(model=model)
    else:
        model = model or os.getenv("UA_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
        device = os.getenv("UA_EMBEDDING_DEVICE", "cpu")
        return SentenceTransformerEmbeddings(model=model, device=device)
