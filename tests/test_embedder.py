"""Tests for the embedder — fastembed + FAISS index construction.

Uses mock embeddings to avoid downloading real models in CI.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from ceph_issue_kb.models import NormalizedIssue, make_entity_id


def _make_issue(source_id: str, title: str, desc: str = "") -> NormalizedIssue:
    return NormalizedIssue(
        entity_id=make_entity_id("test", source_id),
        source="test",
        source_id=source_id,
        source_url=f"https://example.com/{source_id}",
        title=title,
        description=desc,
    )


class TestEmbedder:
    def _make_embedder(self):
        from ceph_issue_kb.indexer.embedder import Embedder

        embedder = Embedder(model_name="BAAI/bge-small-en-v1.5")

        mock_model = MagicMock()

        def fake_embed(texts):
            for _ in texts:
                yield np.random.default_rng(42).random(384).astype(np.float32)

        mock_model.embed = fake_embed
        embedder._model = mock_model
        return embedder

    def test_embed_issues_returns_vectors_and_ids(self):
        embedder = self._make_embedder()
        issues = [
            _make_issue("1", "OSD crash", "crash during scrub"),
            _make_issue("2", "RGW timeout", "timeout on multisite"),
        ]
        vectors, entity_ids = embedder.embed_issues(issues)

        assert vectors.shape == (2, 384)
        assert len(entity_ids) == 2
        assert entity_ids[0] == issues[0].entity_id
        assert entity_ids[1] == issues[1].entity_id

    def test_embed_texts(self):
        embedder = self._make_embedder()
        vectors = embedder.embed_texts(["hello world", "ceph osd crash"])
        assert vectors.shape == (2, 384)
        assert vectors.dtype == np.float32

    def test_build_faiss_index(self):
        faiss = pytest.importorskip("faiss", reason="faiss-cpu not installed")
        from ceph_issue_kb.indexer.embedder import Embedder

        rng = np.random.default_rng(42)
        vectors = rng.random((10, 384)).astype(np.float32)
        index = Embedder.build_faiss_index(vectors)

        assert index.ntotal == 10
        assert index.d == 384

    def test_build_faiss_index_search(self):
        faiss = pytest.importorskip("faiss", reason="faiss-cpu not installed")
        from ceph_issue_kb.indexer.embedder import Embedder

        rng = np.random.default_rng(42)
        vectors = rng.random((5, 64)).astype(np.float32)
        index = Embedder.build_faiss_index(vectors)

        query = rng.random((1, 64)).astype(np.float32)
        faiss.normalize_L2(query)
        distances, indices = index.search(query, 3)

        assert distances.shape == (1, 3)
        assert indices.shape == (1, 3)
        assert all(idx >= 0 for idx in indices[0])

    def test_issue_text_content(self):
        embedder = self._make_embedder()
        issue = _make_issue("1", "Title here", "Description content")
        issue.components = ["rgw", "multisite"]
        text = embedder._issue_text(issue)
        assert "Title here" in text
        assert "Description content" in text
        assert "rgw" in text

    def test_issue_embed_text_module_function(self):
        from ceph_issue_kb.indexer.embedder import issue_embed_text

        issue = _make_issue("1", "OSD crash", "segfault in bluestore")
        issue.components = ["osd"]
        text = issue_embed_text(issue)
        assert "OSD crash" in text
        assert "segfault in bluestore" in text
        assert "osd" in text

    def test_issue_text_hash_deterministic(self):
        from ceph_issue_kb.indexer.embedder import issue_text_hash

        issue = _make_issue("1", "OSD crash", "segfault in bluestore")
        h1 = issue_text_hash(issue)
        h2 = issue_text_hash(issue)
        assert h1 == h2
        assert len(h1) == 16

    def test_issue_text_hash_changes_on_content_change(self):
        from ceph_issue_kb.indexer.embedder import issue_text_hash

        issue = _make_issue("1", "OSD crash", "segfault in bluestore")
        h1 = issue_text_hash(issue)
        issue.description = "different description"
        h2 = issue_text_hash(issue)
        assert h1 != h2

    def test_empty_issues_list(self):
        embedder = self._make_embedder()
        vectors, entity_ids = embedder.embed_issues([])
        assert vectors.shape[0] == 0
        assert entity_ids == []

    def test_fastembed_import_error(self):
        """Verify a clear error when fastembed is missing."""
        from ceph_issue_kb.indexer.embedder import Embedder

        embedder = Embedder()
        embedder._model = None
        with patch.dict("sys.modules", {"fastembed": None}):
            with pytest.raises(ImportError, match="fastembed"):
                embedder._load_model()
