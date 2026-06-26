"""Tests for the SearchEngine — BM25, semantic, and merged search.

BM25 tests use rank-bm25 directly.  Semantic tests use mock embeddings
to avoid downloading real models.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from ceph_issue_kb.models import NormalizedIssue, SearchResult, make_entity_id


def _make_issue(
    source_id: str,
    title: str,
    desc: str = "",
    source: str = "test",
    components: list[str] | None = None,
    status: str = "open",
    health_warnings: list[str] | None = None,
    commands: list[str] | None = None,
) -> NormalizedIssue:
    return NormalizedIssue(
        entity_id=make_entity_id(source, source_id),
        source=source,
        source_id=source_id,
        source_url=f"https://example.com/{source_id}",
        title=title,
        summary=desc[:500] if desc else "",
        description=desc,
        components=components or [],
        status=status,
        health_warnings=health_warnings or [],
        commands_mentioned=commands or [],
    )


@pytest.fixture
def sample_issues() -> list[NormalizedIssue]:
    return [
        _make_issue(
            "1",
            "OSD crash during deep scrub with BlueStore",
            "The OSD crashes with a segfault during deep-scrub operations.",
            components=["osd", "bluestore"],
            health_warnings=["HEALTH_WARN", "OSD_DOWN"],
        ),
        _make_issue(
            "2",
            "RGW bucket resharding fails with ENOENT",
            "Dynamic resharding on multisite secondary zone fails.",
            components=["rgw", "multisite"],
        ),
        _make_issue(
            "3",
            "CephFS client eviction causes kernel hang",
            "MDS evicts client, kernel mount hangs instead of returning EIO.",
            components=["cephfs", "mds"],
            status="resolved",
        ),
        _make_issue(
            "4",
            "PG stuck in peering after OSD replacement",
            "After replacing an OSD, several placement groups remain stuck peering.",
            components=["osd"],
            health_warnings=["PG_DEGRADED"],
            commands=["ceph pg dump_stuck unclean"],
        ),
        _make_issue(
            "5",
            "MON election flapping with stretch mode",
            "Monitor election oscillates between data sites in stretch mode.",
            components=["mon"],
        ),
    ]


class TestBM25Search:
    def test_basic_keyword_search(self, sample_issues):
        rank_bm25 = pytest.importorskip("rank_bm25", reason="rank-bm25 not installed")
        from ceph_issue_kb.search.engine import SearchEngine

        engine = SearchEngine.from_issues(sample_issues)
        results = engine.search("OSD crash deep scrub")

        assert len(results) > 0
        top = results[0]
        assert isinstance(top, SearchResult)
        assert "OSD" in top.issue.title or "osd" in top.issue.title.lower()

    def test_synonym_expansion(self, sample_issues):
        rank_bm25 = pytest.importorskip("rank_bm25", reason="rank-bm25 not installed")
        from ceph_issue_kb.search.engine import SearchEngine, _expand_query

        expanded = _expand_query("PG stuck peering")
        assert "placement group" in expanded

        engine = SearchEngine.from_issues(sample_issues)
        results = engine.search("PG stuck peering")
        assert len(results) > 0

    def test_health_warning_search(self, sample_issues):
        rank_bm25 = pytest.importorskip("rank_bm25", reason="rank-bm25 not installed")
        from ceph_issue_kb.search.engine import SearchEngine

        engine = SearchEngine.from_issues(sample_issues)
        results = engine.search("HEALTH_WARN OSD_DOWN")
        assert len(results) > 0
        assert any("OSD" in r.issue.title for r in results)

    def test_command_search(self, sample_issues):
        rank_bm25 = pytest.importorskip("rank_bm25", reason="rank-bm25 not installed")
        from ceph_issue_kb.search.engine import SearchEngine

        engine = SearchEngine.from_issues(sample_issues)
        results = engine.search("ceph pg dump_stuck")
        assert len(results) > 0

    def test_empty_query(self, sample_issues):
        rank_bm25 = pytest.importorskip("rank_bm25", reason="rank-bm25 not installed")
        from ceph_issue_kb.search.engine import SearchEngine

        engine = SearchEngine.from_issues(sample_issues)
        results = engine.search("")
        assert isinstance(results, list)


class TestSearchFilters:
    def test_filter_by_source(self, sample_issues):
        rank_bm25 = pytest.importorskip("rank_bm25", reason="rank-bm25 not installed")
        from ceph_issue_kb.search.engine import SearchEngine

        issues = sample_issues.copy()
        issues[0] = _make_issue(
            "1", "OSD crash", "crash", source="ceph-tracker", components=["osd"]
        )
        issues[1] = _make_issue(
            "2", "OSD memory issue", "memory", source="ibm-jira", components=["osd"]
        )
        engine = SearchEngine.from_issues(issues)
        results = engine.search("OSD", source="ceph-tracker")
        assert all(r.issue.source == "ceph-tracker" for r in results)

    def test_filter_by_component(self, sample_issues):
        rank_bm25 = pytest.importorskip("rank_bm25", reason="rank-bm25 not installed")
        from ceph_issue_kb.search.engine import SearchEngine

        engine = SearchEngine.from_issues(sample_issues)
        results = engine.search("crash", component="osd")
        assert all("osd" in r.issue.components for r in results)

    def test_filter_by_status(self, sample_issues):
        rank_bm25 = pytest.importorskip("rank_bm25", reason="rank-bm25 not installed")
        from ceph_issue_kb.search.engine import SearchEngine

        engine = SearchEngine.from_issues(sample_issues)
        results = engine.search("client", status="resolved")
        assert all(r.issue.status == "resolved" for r in results)

    def test_limit_results(self, sample_issues):
        rank_bm25 = pytest.importorskip("rank_bm25", reason="rank-bm25 not installed")
        from ceph_issue_kb.search.engine import SearchEngine

        engine = SearchEngine.from_issues(sample_issues)
        results = engine.search("OSD", limit=2)
        assert len(results) <= 2


class TestMergedSearch:
    def test_merged_results_have_correct_source(self, sample_issues):
        rank_bm25 = pytest.importorskip("rank_bm25", reason="rank-bm25 not installed")
        from ceph_issue_kb.search.engine import SearchEngine

        engine = SearchEngine.from_issues(sample_issues)
        results = engine.search("OSD crash")
        assert all(r.search_source in ("bm25", "semantic", "merged") for r in results)

    def test_merge_results_rrf(self):
        from ceph_issue_kb.search.engine import SearchEngine

        issue_a = _make_issue("a", "Issue A")
        issue_b = _make_issue("b", "Issue B")
        issue_c = _make_issue("c", "Issue C")

        bm25 = [
            SearchResult(issue=issue_a, score=5.0, search_source="bm25"),
            SearchResult(issue=issue_b, score=3.0, search_source="bm25"),
        ]
        semantic = [
            SearchResult(issue=issue_b, score=0.9, search_source="semantic"),
            SearchResult(issue=issue_c, score=0.8, search_source="semantic"),
        ]

        merged = SearchEngine._merge_results(bm25, semantic)
        assert len(merged) == 3
        eids = [r.issue.entity_id for r in merged]
        assert eids[0] == issue_b.entity_id  # appears in both tiers


class TestSearchPersistence:
    def test_save_and_load_bm25(self, sample_issues):
        rank_bm25 = pytest.importorskip("rank_bm25", reason="rank-bm25 not installed")
        from ceph_issue_kb.search.engine import SearchEngine

        engine = SearchEngine.from_issues(sample_issues)

        with tempfile.TemporaryDirectory() as tmpdir:
            engine.save(Path(tmpdir))
            loaded = SearchEngine.load(Path(tmpdir))
            results = loaded.search("OSD crash")
            assert len(results) > 0

    def test_save_and_load_with_faiss(self, sample_issues):
        rank_bm25 = pytest.importorskip("rank_bm25", reason="rank-bm25 not installed")
        faiss = pytest.importorskip("faiss", reason="faiss-cpu not installed")
        from ceph_issue_kb.search.engine import SearchEngine

        rng = np.random.default_rng(42)
        vectors = rng.random((len(sample_issues), 64)).astype(np.float32)
        entity_ids = [issue.entity_id for issue in sample_issues]

        engine = SearchEngine.from_issues(sample_issues, vectors=vectors, vector_entity_ids=entity_ids)

        with tempfile.TemporaryDirectory() as tmpdir:
            engine.save(Path(tmpdir))

            assert (Path(tmpdir) / "faiss.index").exists()
            assert (Path(tmpdir) / "faiss_ids.json").exists()

            loaded = SearchEngine.load(Path(tmpdir))
            assert loaded._faiss_index is not None
            assert loaded._faiss_index.ntotal == len(sample_issues)


class TestExpandQuery:
    def test_pg_expands(self):
        from ceph_issue_kb.search.engine import _expand_query

        result = _expand_query("PG stuck")
        assert "placement group" in result

    def test_osd_expands(self):
        from ceph_issue_kb.search.engine import _expand_query

        result = _expand_query("OSD down")
        assert "object storage daemon" in result

    def test_no_synonyms_unchanged(self):
        from ceph_issue_kb.search.engine import _expand_query

        result = _expand_query("random query")
        assert result == "random query"

    def test_multiple_synonyms(self):
        from ceph_issue_kb.search.engine import _expand_query

        result = _expand_query("PG on OSD")
        assert "placement group" in result
        assert "object storage daemon" in result
