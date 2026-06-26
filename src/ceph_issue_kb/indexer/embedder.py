"""Generate dense vector embeddings for NormalizedIssues using fastembed.

Builds a FAISS index for efficient approximate nearest-neighbour retrieval.
Falls back gracefully when fastembed or faiss-cpu are not installed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np

from ceph_issue_kb.models import NormalizedIssue

if TYPE_CHECKING:
    import faiss as _faiss

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"


class Embedder:
    """Thin wrapper around fastembed for producing ONNX embeddings."""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        self.model_name = model_name
        self._model = None

    def _load_model(self):
        if self._model is not None:
            return
        try:
            from fastembed import TextEmbedding  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "fastembed is required for embeddings. "
                "Install with: pip install 'ceph-issue-kb[search]'"
            ) from exc
        self._model = TextEmbedding(model_name=self.model_name)
        logger.info("Loaded embedding model: %s", self.model_name)

    def _issue_text(self, issue: NormalizedIssue) -> str:
        """Build the text to embed for a single issue."""
        parts = [issue.title]
        if issue.description:
            parts.append(issue.description[:2000])
        if issue.components:
            parts.append("components: " + ", ".join(issue.components))
        return "\n".join(parts)

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        """Embed a list of raw text strings, returning an (N, dim) array."""
        self._load_model()
        embeddings = list(self._model.embed(texts))
        return np.array(embeddings, dtype=np.float32)

    def embed_issues(
        self, issues: list[NormalizedIssue]
    ) -> tuple[np.ndarray, list[str]]:
        """Embed issues and return (vectors, entity_ids).

        Returns:
            vectors: (N, dim) float32 numpy array
            entity_ids: list of entity_id strings, same order as vectors
        """
        texts = [self._issue_text(issue) for issue in issues]
        entity_ids = [issue.entity_id for issue in issues]
        if not texts:
            return np.empty((0, 0), dtype=np.float32), entity_ids
        vectors = self.embed_texts(texts)
        logger.info("Embedded %d issues (%d dimensions)", len(issues), vectors.shape[1])
        return vectors, entity_ids

    @staticmethod
    def build_faiss_index(vectors: np.ndarray) -> "_faiss.Index":
        """Build a flat L2 FAISS index from *vectors*.

        For small-to-medium collections (< 100K), IndexFlatIP with
        normalised vectors gives exact cosine similarity.
        """
        try:
            import faiss  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "faiss-cpu is required for vector search. "
                "Install with: pip install 'ceph-issue-kb[search]'"
            ) from exc
        dim = vectors.shape[1]
        faiss.normalize_L2(vectors)
        index = faiss.IndexFlatIP(dim)
        index.add(vectors)
        logger.info("Built FAISS index: %d vectors, %d dims", index.ntotal, dim)
        return index
