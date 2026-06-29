"""Tests for the MCP server — tests each tool via the KnowledgeBase facade.

The MCP layer is a thin wrapper around KnowledgeBase, so testing the
facade validates tool behaviour.  A smoke test verifies that the server
can be constructed with FastMCP.
"""

from __future__ import annotations

import pytest

from ceph_issue_kb.models import Comment, NormalizedIssue, Relationship, make_entity_id
from ceph_issue_kb.search.engine import SearchEngine
from ceph_issue_kb.server.kb import KnowledgeBase


def _issue(
    sid: str,
    title: str,
    *,
    desc: str = "",
    source: str = "test",
    components: list[str] | None = None,
    status: str = "open",
    priority: str = "medium",
    labels: list[str] | None = None,
    health_warnings: list[str] | None = None,
    stacktraces: list[str] | None = None,
    assertions: list[str] | None = None,
    affected_versions: list[str] | None = None,
    fixed_versions: list[str] | None = None,
    resolution: str = "",
    release: str = "",
    comments: list[Comment] | None = None,
    relationships: list[Relationship] | None = None,
    updated_at: str = "2025-06-01T00:00:00Z",
) -> NormalizedIssue:
    return NormalizedIssue(
        entity_id=make_entity_id(source, sid),
        source=source,
        source_id=sid,
        source_url=f"https://example.com/{sid}",
        title=title,
        summary=desc[:500] if desc else "",
        description=desc,
        components=components or [],
        status=status,
        priority=priority,
        labels=labels or [],
        health_warnings=health_warnings or [],
        stacktraces=stacktraces or [],
        assertions=assertions or [],
        affected_versions=affected_versions or [],
        fixed_versions=fixed_versions or [],
        resolution=resolution,
        release=release,
        comments=comments or [],
        relationships=relationships or [],
        updated_at=updated_at,
    )


@pytest.fixture
def issues():
    return [
        _issue(
            "100", "OSD crash during deep scrub",
            desc="The OSD crashes with a segfault during deep-scrub. Workaround: disable deep scrub.",
            components=["osd", "bluestore"],
            status="open",
            priority="critical",
            labels=["regression"],
            health_warnings=["HEALTH_WARN", "OSD_DOWN"],
            stacktraces=["Thread 1 at ceph_osd::do_scrub() 0xdead"],
            assertions=["ceph_assert(googly > 0)"],
            affected_versions=["18.2.0", "19.1.0"],
            release="reef",
            comments=[
                Comment(author="dev1", body="Workaround: set osd_deep_scrub_enabled=false", created_at="2025-01-15"),
                Comment(author="dev2", body="Fixed by commit abc1234 in PR #5678", created_at="2025-02-01"),
            ],
            updated_at="2025-06-15T00:00:00Z",
        ),
        _issue(
            "101", "RGW multisite sync stall",
            desc="Multisite data sync stalls under heavy load.",
            components=["rgw", "multisite"],
            status="open",
            priority="high",
            affected_versions=["19.1.0"],
            release="squid",
            updated_at="2025-06-10T00:00:00Z",
        ),
        _issue(
            "102", "CephFS snapshot rollback fails",
            desc="Rolling back a snapshot on CephFS returns EIO.",
            components=["cephfs"],
            status="resolved",
            resolution="fixed",
            fixed_versions=["19.2.0"],
            release="squid",
            comments=[
                Comment(author="dev3", body="Fix merged to main, backport cherry-picked to squid", created_at="2025-03-01"),
            ],
            updated_at="2025-05-20T00:00:00Z",
        ),
        _issue(
            "103", "MON election flapping in stretch mode",
            desc="Monitors keep flapping between sites.",
            components=["mon"],
            status="open",
            priority="critical",
            labels=["blocker"],
            health_warnings=["MON_DOWN"],
            updated_at="2025-06-20T00:00:00Z",
        ),
    ]


@pytest.fixture
def kb(issues):
    pytest.importorskip("rank_bm25", reason="rank-bm25 required")
    engine = SearchEngine.from_issues(issues)
    return KnowledgeBase(search_engine=engine)


# -- get_issue --------------------------------------------------------------


class TestGetIssue:
    def test_by_entity_id(self, kb, issues):
        eid = issues[0].entity_id
        result = kb.get_issue(eid)
        assert "error" not in result
        assert result["entity_id"] == eid
        assert result["title"] == "OSD crash during deep scrub"

    def test_by_source_id(self, kb):
        result = kb.get_issue("100")
        assert "error" not in result
        assert result["source_id"] == "100"

    def test_returns_full_description(self, kb, issues):
        eid = issues[0].entity_id
        result = kb.get_issue(eid)
        assert "description" in result
        assert "deep-scrub" in result["description"]

    def test_returns_all_comments(self, kb, issues):
        eid = issues[0].entity_id
        result = kb.get_issue(eid)
        assert "comments" in result
        assert len(result["comments"]) == 2
        assert result["comments"][0]["author"] == "dev1"
        assert "body" in result["comments"][0]
        assert "created_at" in result["comments"][0]

    def test_returns_stacktraces(self, kb, issues):
        eid = issues[0].entity_id
        result = kb.get_issue(eid)
        assert "stacktraces" in result
        assert len(result["stacktraces"]) == 1

    def test_returns_relationships(self, kb, issues):
        eid = issues[0].entity_id
        result = kb.get_issue(eid)
        assert "relationships" in result

    def test_not_found(self, kb):
        result = kb.get_issue("nonexistent_id_12345")
        assert "error" in result

    def test_returns_comment_id(self, kb, issues):
        eid = issues[0].entity_id
        result = kb.get_issue(eid)
        for comment in result["comments"]:
            assert "comment_id" in comment


# -- search_issues (comment_count) -----------------------------------------


class TestCommentCount:
    def test_search_results_include_comment_count(self, kb):
        result = kb.search_issues("OSD crash deep scrub")
        for item in result["results"]:
            assert "comment_count" in item
            assert isinstance(item["comment_count"], int)

    def test_comment_count_matches(self, kb, issues):
        result = kb.search_issues("OSD crash deep scrub")
        osd_items = [r for r in result["results"] if r["source_id"] == "100"]
        if osd_items:
            assert osd_items[0]["comment_count"] == 2

    def test_zero_comments(self, kb, issues):
        result = kb.search_issues("multisite sync stall")
        rgw_items = [r for r in result["results"] if r["source_id"] == "101"]
        if rgw_items:
            assert rgw_items[0]["comment_count"] == 0


# -- search_issues ----------------------------------------------------------


class TestSearchIssues:
    def test_basic_search(self, kb):
        result = kb.search_issues("OSD crash deep scrub")
        assert result["total"] > 0
        assert "results" in result

    def test_returns_score(self, kb):
        result = kb.search_issues("OSD crash")
        for item in result["results"]:
            assert "score" in item
            assert isinstance(item["score"], float)

    def test_filter_by_component(self, kb):
        result = kb.search_issues("crash", component="osd")
        for item in result["results"]:
            assert "osd" in item["components"]

    def test_filter_by_status(self, kb):
        result = kb.search_issues("snapshot", status="resolved")
        for item in result["results"]:
            assert item["status"] == "resolved"

    def test_filter_by_version(self, kb):
        result = kb.search_issues("OSD", version="19.1.0")
        for item in result["results"]:
            assert "19.1.0" in item["affected_versions"]

    def test_limit(self, kb):
        result = kb.search_issues("OSD", limit=1)
        assert len(result["results"]) <= 1


# -- find_similar_issue -----------------------------------------------------


class TestFindSimilarIssue:
    def test_returns_similarity_scores(self, kb):
        result = kb.find_similar_issue("OSD crashes during scrub")
        assert result["total"] > 0
        for item in result["results"]:
            assert "similarity" in item
            assert "matched_signals" in item

    def test_with_stacktrace(self, kb):
        result = kb.find_similar_issue(
            "OSD segfault",
            stacktrace="Thread 1 at ceph_osd::do_scrub()",
        )
        assert result["total"] > 0


# -- is_known_issue ---------------------------------------------------------


class TestIsKnownIssue:
    def test_known_issue_found(self, kb):
        result = kb.is_known_issue("OSD crash deep scrub segfault")
        assert result["known"] is True
        assert "issue" in result

    def test_known_issue_with_version(self, kb):
        result = kb.is_known_issue("OSD crash deep scrub", version="18.2.0")
        assert result["known"] is True

    def test_unknown_issue(self, kb):
        result = kb.is_known_issue("completely unrelated xyzzy foobar")
        assert result["known"] is False

    def test_resolved_issues_excluded(self, kb):
        result = kb.is_known_issue("snapshot rollback")
        assert result["known"] is False or result.get("issue", {}).get("status") != "resolved"


# -- find_workaround --------------------------------------------------------


class TestFindWorkaround:
    def test_finds_workaround_in_comments(self, kb):
        result = kb.find_workaround("OSD crash deep scrub")
        assert result["total"] > 0
        assert any("workaround" in w["text"].lower() or "Workaround" in w["text"] for w in result["workarounds"])

    def test_no_workaround(self, kb):
        result = kb.find_workaround("multisite sync stall")
        assert result["total"] == 0


# -- find_fix ---------------------------------------------------------------


class TestFindFix:
    def test_finds_fix_in_comments(self, kb):
        result = kb.find_fix("OSD crash deep scrub")
        assert result["total"] > 0
        assert any("commit" in f["text"].lower() or "PR" in f["text"] for f in result["fixes"])

    def test_resolved_issue_has_resolution(self, kb):
        result = kb.find_fix("snapshot rollback")
        if "resolution" in result:
            assert result["resolution"] == "fixed"


# -- find_related_issues ----------------------------------------------------


class TestFindRelatedIssues:
    def test_issue_not_found(self, kb):
        result = kb.find_related_issues("0000000000000000")
        assert "error" in result

    def test_issue_with_no_relations(self, kb, issues):
        eid = issues[0].entity_id
        result = kb.find_related_issues(eid)
        assert "issue" in result
        assert result["total"] == 0

    def test_issue_with_relations(self, kb, issues):
        rel_issue = _issue(
            "200", "Related OSD issue",
            components=["osd"],
            relationships=[
                Relationship(
                    relation_type="duplicate",
                    target_source="test",
                    target_id=issues[0].entity_id,
                ),
            ],
        )
        engine = SearchEngine.from_issues(issues + [rel_issue])
        kb2 = KnowledgeBase(search_engine=engine)
        result = kb2.find_related_issues(rel_issue.entity_id)
        assert result["total"] == 1
        assert result["related"][0]["relation_type"] == "duplicate"


# -- search_stacktrace -----------------------------------------------------


class TestSearchStacktrace:
    def test_finds_matching_stacktrace(self, kb):
        result = kb.search_stacktrace("Thread 1 at ceph_osd::do_scrub()")
        assert result["total"] > 0
        assert "fingerprint" in result

    def test_returns_fingerprint(self, kb):
        result = kb.search_stacktrace("any stacktrace text")
        assert len(result["fingerprint"]) == 16


# -- search_health_warning -------------------------------------------------


class TestSearchHealthWarning:
    def test_finds_issues_with_warning(self, kb):
        result = kb.search_health_warning("HEALTH_WARN")
        assert result["total"] > 0

    def test_returns_warning_field(self, kb):
        result = kb.search_health_warning("OSD_DOWN")
        assert result["warning"] == "OSD_DOWN"


# -- hot_issues -------------------------------------------------------------


class TestHotIssues:
    def test_returns_sorted_by_updated(self, kb):
        result = kb.hot_issues()
        assert result["total"] > 0
        dates = [r["updated_at"] for r in result["results"] if r["updated_at"]]
        assert dates == sorted(dates, reverse=True)

    def test_filter_by_component(self, kb):
        result = kb.hot_issues(component="osd")
        for item in result["results"]:
            assert "osd" in item["components"]

    def test_limit(self, kb):
        result = kb.hot_issues(limit=2)
        assert len(result["results"]) <= 2


# -- component_health ------------------------------------------------------


class TestComponentHealth:
    def test_osd_health(self, kb):
        result = kb.component_health("osd")
        assert result["component"] == "osd"
        assert result["open_issues"] >= 1
        assert isinstance(result["critical_issues"], list)

    def test_critical_issues_listed(self, kb):
        result = kb.component_health("osd")
        assert len(result["critical_issues"]) >= 1

    def test_blocker_issues_listed(self, kb):
        result = kb.component_health("mon")
        assert len(result["blockers"]) >= 1


# -- capabilities / health ------------------------------------------------


class TestCapabilities:
    def test_has_required_fields(self, kb):
        cap = kb.capabilities()
        assert cap["name"] == "ceph-issue-kb"
        assert "issue" in cap["entity_types"]
        assert "search_issues" in cap["operations"]
        assert "get_issue" in cap["operations"]
        assert "entity_counts" in cap


class TestHealth:
    def test_loaded_kb_ok(self, kb):
        h = kb.health()
        assert h["status"] == "ok"
        assert h["total_issues"] > 0

    def test_empty_kb_degraded(self):
        kb = KnowledgeBase.empty()
        h = kb.health()
        assert h["status"] == "degraded"
        assert h["total_issues"] == 0


# -- MCP server smoke test -------------------------------------------------


class TestMCPServerCreation:
    def test_create_server(self, kb):
        mcp = pytest.importorskip("mcp", reason="mcp package not installed")
        from ceph_issue_kb.server.mcp_server import create_mcp_server

        server = create_mcp_server(kb)
        assert server is not None
