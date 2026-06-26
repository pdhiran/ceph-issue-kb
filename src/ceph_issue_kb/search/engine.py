"""Two-tier search engine: BM25 (keyword) + semantic (fastembed/FAISS).

BM25 operates on title + summary + extracted signals for exact error
message matching.  Semantic search uses dense embeddings for conceptual
similarity.  Results from both tiers are merged and re-ranked.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import numpy as np

from ceph_issue_kb.models import NormalizedIssue, SearchResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Ceph terminology synonym table for BM25 query expansion
# ---------------------------------------------------------------------------

CEPH_SYNONYMS: dict[str, list[str]] = {
    "pg": ["placement group", "placement groups"],
    "osd": ["object storage daemon"],
    "mon": ["monitor", "ceph monitor"],
    "mds": ["metadata server", "ceph mds"],
    "rgw": ["rados gateway", "radosgw", "s3", "swift"],
    "rbd": ["rados block device", "block device"],
    "mgr": ["manager", "ceph manager"],
    "rados": ["reliable autonomic distributed object store"],
    "cephfs": ["ceph file system", "ceph filesystem"],
    "crush": ["crush map", "crush rule"],
    "bluestore": ["blue store"],
    "nfs": ["nfs-ganesha", "ganesha"],
    "scrub": ["deep scrub", "deep-scrub"],
    "peering": ["pg peering"],
    "backfill": ["pg backfill"],
    "recovery": ["pg recovery"],
}


def _expand_query(query: str) -> str:
    """Expand Ceph abbreviations in *query* to include synonyms."""
    tokens = re.findall(r"\w+", query.lower())
    extra: list[str] = []
    for token in tokens:
        if token in CEPH_SYNONYMS:
            extra.extend(CEPH_SYNONYMS[token])
    if extra:
        return query + " " + " ".join(extra)
    return query


def _bm25_doc_text(issue: NormalizedIssue) -> str:
    """Build the indexable text for BM25 from an issue."""
    parts = [
        issue.title,
        issue.summary,
    ]
    parts.extend(issue.health_warnings)
    parts.extend(issue.assertions)
    parts.extend(issue.commands_mentioned)
    parts.extend(issue.configs_mentioned)
    if issue.components:
        parts.append(" ".join(issue.components))
    if issue.labels:
        parts.append(" ".join(issue.labels))
    return " ".join(parts)


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


class SearchEngine:
    """Two-tier search over a collection of NormalizedIssues.

    Use ``from_issues()`` to build indices in memory, or ``load()``
    to restore from a previously saved knowledge directory.
    """

    def __init__(self) -> None:
        self._issues: dict[str, NormalizedIssue] = {}
        self._bm25 = None
        self._bm25_entity_ids: list[str] = []
        self._faiss_index = None
        self._faiss_entity_ids: list[str] = []
        self._faiss_dim: int = 0

    @classmethod
    def from_issues(
        cls,
        issues: list[NormalizedIssue],
        vectors: np.ndarray | None = None,
        vector_entity_ids: list[str] | None = None,
    ) -> "SearchEngine":
        """Build a SearchEngine from a list of issues.

        If *vectors* and *vector_entity_ids* are provided, the semantic
        tier is enabled.  Otherwise only BM25 is available.
        """
        engine = cls()
        engine._issues = {issue.entity_id: issue for issue in issues}

        engine._build_bm25(issues)

        if vectors is not None and vector_entity_ids is not None:
            engine._set_faiss(vectors, vector_entity_ids)

        return engine

    def _build_bm25(self, issues: list[NormalizedIssue]) -> None:
        try:
            from rank_bm25 import BM25Okapi  # type: ignore[import-untyped]
        except ImportError as exc:
            raise ImportError(
                "rank-bm25 is required for keyword search. "
                "Install with: pip install 'ceph-issue-kb[search]'"
            ) from exc

        corpus: list[list[str]] = []
        entity_ids: list[str] = []
        for issue in issues:
            tokens = _tokenize(_bm25_doc_text(issue))
            corpus.append(tokens)
            entity_ids.append(issue.entity_id)

        self._bm25 = BM25Okapi(corpus)
        self._bm25_entity_ids = entity_ids

    def _set_faiss(self, vectors: np.ndarray, entity_ids: list[str]) -> None:
        try:
            import faiss  # type: ignore[import-untyped]
        except ImportError:
            logger.warning("faiss-cpu not installed; semantic search disabled")
            return

        if vectors.shape[0] != len(entity_ids):
            raise ValueError(
                f"Vector count ({vectors.shape[0]}) != entity_id count ({len(entity_ids)})"
            )

        self._faiss_dim = vectors.shape[1]
        vecs = vectors.copy().astype(np.float32)
        faiss.normalize_L2(vecs)
        index = faiss.IndexFlatIP(self._faiss_dim)
        index.add(vecs)
        self._faiss_index = index
        self._faiss_entity_ids = list(entity_ids)

    def search(
        self,
        query: str,
        *,
        source: str | None = None,
        component: str | None = None,
        status: str | None = None,
        limit: int = 10,
    ) -> list[SearchResult]:
        """Search issues across BM25 and semantic tiers.

        Filters by *source*, *component*, and *status* after retrieval.
        Returns up to *limit* merged results.
        """
        bm25_results = self._search_bm25(query, limit=limit * 2)
        semantic_results = self._search_semantic(query, limit=limit * 2)

        merged = self._merge_results(bm25_results, semantic_results)

        if source or component or status:
            merged = self._filter_results(merged, source=source, component=component, status=status)

        return merged[:limit]

    def _search_bm25(self, query: str, limit: int = 20) -> list[SearchResult]:
        if self._bm25 is None:
            return []

        expanded = _expand_query(query)
        tokens = _tokenize(expanded)
        scores = self._bm25.get_scores(tokens)

        scored_pairs = [
            (self._bm25_entity_ids[i], float(scores[i]))
            for i in range(len(scores))
            if scores[i] > 0
        ]
        scored_pairs.sort(key=lambda x: x[1], reverse=True)

        results: list[SearchResult] = []
        for eid, score in scored_pairs[:limit]:
            issue = self._issues.get(eid)
            if issue:
                results.append(SearchResult(issue=issue, score=score, search_source="bm25"))
        return results

    def _search_semantic(self, query: str, limit: int = 20) -> list[SearchResult]:
        if self._faiss_index is None or self._faiss_index.ntotal == 0:
            return []

        try:
            from fastembed import TextEmbedding  # type: ignore[import-untyped]
            import faiss  # type: ignore[import-untyped]
        except ImportError:
            return []

        model = TextEmbedding(model_name="BAAI/bge-small-en-v1.5")
        q_vec = np.array(list(model.embed([query])), dtype=np.float32)
        faiss.normalize_L2(q_vec)

        k = min(limit, self._faiss_index.ntotal)
        distances, indices = self._faiss_index.search(q_vec, k)

        results: list[SearchResult] = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx < 0:
                continue
            eid = self._faiss_entity_ids[idx]
            issue = self._issues.get(eid)
            if issue:
                results.append(
                    SearchResult(issue=issue, score=float(dist), search_source="semantic")
                )
        return results

    @staticmethod
    def _merge_results(
        bm25: list[SearchResult], semantic: list[SearchResult]
    ) -> list[SearchResult]:
        """Merge BM25 and semantic results with reciprocal rank fusion."""
        K = 60  # RRF constant
        scores: dict[str, float] = {}
        issue_map: dict[str, NormalizedIssue] = {}

        for rank, r in enumerate(bm25):
            eid = r.issue.entity_id
            scores[eid] = scores.get(eid, 0) + 1.0 / (K + rank + 1)
            issue_map[eid] = r.issue

        for rank, r in enumerate(semantic):
            eid = r.issue.entity_id
            scores[eid] = scores.get(eid, 0) + 1.0 / (K + rank + 1)
            issue_map[eid] = r.issue

        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
        return [
            SearchResult(issue=issue_map[eid], score=score, search_source="merged")
            for eid, score in ranked
        ]

    @staticmethod
    def _filter_results(
        results: list[SearchResult],
        *,
        source: str | None = None,
        component: str | None = None,
        status: str | None = None,
    ) -> list[SearchResult]:
        filtered: list[SearchResult] = []
        for r in results:
            if source and r.issue.source != source:
                continue
            if component and component.lower() not in r.issue.components:
                continue
            if status and r.issue.status != status.lower():
                continue
            filtered.append(r)
        return filtered

    def save(self, directory: Path) -> None:
        """Persist BM25 metadata and FAISS index to *directory*."""
        directory.mkdir(parents=True, exist_ok=True)

        issues_data = []
        for issue in self._issues.values():
            issues_data.append(_issue_to_dict(issue))
        (directory / "issues.json").write_text(
            json.dumps(issues_data, indent=2, default=str)
        )

        bm25_meta = {
            "entity_ids": self._bm25_entity_ids,
            "doc_count": len(self._bm25_entity_ids),
        }
        (directory / "bm25_meta.json").write_text(json.dumps(bm25_meta))

        if self._faiss_index is not None:
            try:
                import faiss  # type: ignore[import-untyped]

                faiss.write_index(self._faiss_index, str(directory / "faiss.index"))
                (directory / "faiss_ids.json").write_text(
                    json.dumps(self._faiss_entity_ids)
                )
            except ImportError:
                logger.warning("faiss-cpu not installed; skipping FAISS save")

    @classmethod
    def load(cls, directory: Path) -> "SearchEngine":
        """Restore a SearchEngine from a saved knowledge directory."""
        engine = cls()

        issues_path = directory / "issues.json"
        if issues_path.exists():
            issues_data = json.loads(issues_path.read_text())
            issues = [_dict_to_issue(d) for d in issues_data]
            engine._issues = {issue.entity_id: issue for issue in issues}
            engine._build_bm25(issues)

        faiss_path = directory / "faiss.index"
        faiss_ids_path = directory / "faiss_ids.json"
        if faiss_path.exists() and faiss_ids_path.exists():
            try:
                import faiss  # type: ignore[import-untyped]

                engine._faiss_index = faiss.read_index(str(faiss_path))
                engine._faiss_entity_ids = json.loads(faiss_ids_path.read_text())
                engine._faiss_dim = engine._faiss_index.d
            except ImportError:
                logger.warning("faiss-cpu not installed; semantic search disabled")

        return engine


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------


def _issue_to_dict(issue: NormalizedIssue) -> dict[str, Any]:
    from dataclasses import asdict

    return asdict(issue)


def _dict_to_issue(d: dict[str, Any]) -> NormalizedIssue:
    from ceph_issue_kb.models import Comment, Relationship

    comments = [Comment(**c) for c in d.pop("comments", [])]
    relationships = [Relationship(**r) for r in d.pop("relationships", [])]
    return NormalizedIssue(**d, comments=comments, relationships=relationships)
