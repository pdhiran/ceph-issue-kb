"""Tests for the SimilarityEngine — scoring, fingerprinting, edge cases."""

from __future__ import annotations

import pytest

from ceph_issue_kb.models import Comment, NormalizedIssue, make_entity_id
from ceph_issue_kb.search.similarity import (
    SimilarityEngine,
    SimilarityResult,
    _jaccard,
    _normalize_stacktrace,
    fingerprint,
)


def _make_issue(
    source_id: str,
    title: str,
    desc: str = "",
    source: str = "test",
    components: list[str] | None = None,
    status: str = "open",
    health_warnings: list[str] | None = None,
    stacktraces: list[str] | None = None,
    assertions: list[str] | None = None,
    updated_at: str = "2025-01-01T00:00:00Z",
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
        stacktraces=stacktraces or [],
        assertions=assertions or [],
        updated_at=updated_at,
    )


@pytest.fixture
def sample_issues() -> list[NormalizedIssue]:
    return [
        _make_issue(
            "1",
            "OSD crash during deep scrub with BlueStore",
            "The OSD daemon crashes with a segfault during deep-scrub operations on BlueStore.",
            components=["osd", "bluestore"],
            health_warnings=["HEALTH_WARN", "OSD_DOWN"],
            stacktraces=["Thread 1 crashed at ceph_osd::do_scrub() at 0xdeadbeef"],
            assertions=["ceph_assert(googly > 0)"],
        ),
        _make_issue(
            "2",
            "RGW bucket resharding fails with ENOENT",
            "Dynamic resharding on multisite secondary zone fails with ENOENT error.",
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
            "OSD crash segfault in BlueStore compaction",
            "Segfault during BlueStore compaction leads to OSD crash.",
            components=["osd", "bluestore"],
            stacktraces=["Thread 1 crashed at ceph_osd::compact() at 0x1234abcd"],
        ),
    ]


@pytest.fixture
def engine(sample_issues):
    pytest.importorskip("rank_bm25", reason="rank-bm25 not installed")
    from ceph_issue_kb.search.engine import SearchEngine

    return SearchEngine.from_issues(sample_issues)


@pytest.fixture
def similarity(engine):
    return SimilarityEngine(engine)


# ── find_similar ──────────────────────────────────────────────────────────


class TestFindSimilar:
    def test_returns_results(self, similarity):
        results = similarity.find_similar("OSD crash during deep scrub")
        assert len(results) > 0
        assert all(isinstance(r, SimilarityResult) for r in results)
        assert all(0 <= r.similarity <= 1 for r in results)

    def test_results_sorted_descending(self, similarity):
        results = similarity.find_similar("OSD crash BlueStore")
        scores = [r.similarity for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_empty_description_returns_empty(self, similarity):
        assert similarity.find_similar("") == []

    def test_none_description_returns_empty(self, similarity):
        assert similarity.find_similar("", stacktrace=None) == []

    def test_with_stacktrace(self, similarity):
        results = similarity.find_similar(
            "OSD crash",
            stacktrace="Thread 1 crashed at ceph_osd::do_scrub()",
        )
        assert len(results) > 0
        has_st_signal = any(
            "stacktrace" in s for r in results for s in r.matched_signals
        )
        assert has_st_signal

    def test_component_filter_boosts_match(self, similarity):
        with_comp = similarity.find_similar("OSD crash", component="osd")
        without_comp = similarity.find_similar("OSD crash")

        osd_ids_with = {r.issue.entity_id for r in with_comp if "osd" in r.issue.components}
        osd_ids_without = {r.issue.entity_id for r in without_comp if "osd" in r.issue.components}
        assert osd_ids_with == osd_ids_without

        if with_comp:
            assert any("same component" in s for s in with_comp[0].matched_signals)

    def test_matched_signals_populated(self, similarity):
        results = similarity.find_similar("OSD crash deep scrub BlueStore")
        for r in results:
            assert isinstance(r.matched_signals, list)

    def test_limit_respected(self, similarity):
        results = similarity.find_similar("OSD", limit=2)
        assert len(results) <= 2


# ── fingerprint ───────────────────────────────────────────────────────────


class TestFingerprint:
    def test_deterministic(self):
        fp1 = fingerprint("crash at do_scrub", "assert(x>0)", "osd")
        fp2 = fingerprint("crash at do_scrub", "assert(x>0)", "osd")
        assert fp1 == fp2

    def test_length(self):
        fp = fingerprint("some trace", "some assert", "osd")
        assert len(fp) == 16
        assert all(c in "0123456789abcdef" for c in fp)

    def test_different_inputs_different_fingerprints(self):
        fp1 = fingerprint("crash at do_scrub", "assert(x>0)", "osd")
        fp2 = fingerprint("crash at compact", "assert(y>0)", "rgw")
        assert fp1 != fp2

    def test_normalises_addresses(self):
        fp1 = fingerprint("crash at 0xdeadbeef in func()", "", "osd")
        fp2 = fingerprint("crash at 0x12345678 in func()", "", "osd")
        assert fp1 == fp2

    def test_normalises_line_numbers(self):
        fp1 = fingerprint('File "foo.py", line 42, in bar', "", "")
        fp2 = fingerprint('File "foo.py", line 99, in bar', "", "")
        assert fp1 == fp2

    def test_empty_stacktrace(self):
        fp = fingerprint("", "assert(x>0)", "osd")
        assert len(fp) == 16

    def test_all_empty(self):
        fp = fingerprint("", "", "")
        assert len(fp) == 16


# ── text helpers ──────────────────────────────────────────────────────────


class TestJaccard:
    def test_identical_texts(self):
        assert _jaccard("hello world", "hello world") == 1.0

    def test_disjoint_texts(self):
        assert _jaccard("hello world", "foo bar") == 0.0

    def test_partial_overlap(self):
        score = _jaccard("hello world foo", "hello world bar")
        assert 0.0 < score < 1.0

    def test_empty_string(self):
        assert _jaccard("", "hello") == 0.0
        assert _jaccard("hello", "") == 0.0
        assert _jaccard("", "") == 0.0

    def test_case_insensitive(self):
        assert _jaccard("Hello World", "hello world") == 1.0


class TestNormalizeStacktrace:
    def test_strips_hex_addresses(self):
        result = _normalize_stacktrace("crash at 0xDEADBEEF")
        assert "0xDEADBEEF" not in result
        assert "ADDR" in result.upper()

    def test_strips_line_numbers(self):
        result = _normalize_stacktrace('File "foo.py", line 42')
        assert "42" not in result
        assert "line n" in result

    def test_strips_file_paths(self):
        result = _normalize_stacktrace('File "/usr/lib/python3/foo.py"')
        assert "/usr/lib/python3/" not in result

    def test_strips_large_numbers(self):
        result = _normalize_stacktrace("size=123456789 offset=987654321")
        assert "123456789" not in result
