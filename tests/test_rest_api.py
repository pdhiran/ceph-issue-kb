"""Tests for the REST API — each endpoint via Starlette TestClient."""

from __future__ import annotations

import pytest

from ceph_issue_kb.models import Comment, NormalizedIssue, make_entity_id
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
    affected_versions: list[str] | None = None,
    release: str = "",
    comments: list[Comment] | None = None,
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
        affected_versions=affected_versions or [],
        release=release,
        comments=comments or [],
        updated_at=updated_at,
    )


@pytest.fixture
def issues():
    return [
        _issue(
            "10", "OSD crash during deep scrub",
            desc="Segfault during deep-scrub. Workaround: disable deep scrub.",
            components=["osd"],
            priority="critical",
            health_warnings=["HEALTH_WARN"],
            stacktraces=["at ceph_osd::do_scrub()"],
            affected_versions=["18.2.0"],
            comments=[
                Comment(author="dev", body="Workaround: set noscrub flag", created_at="2025-01-01"),
                Comment(author="dev", body="Fixed by commit abc123", created_at="2025-02-01"),
            ],
            updated_at="2025-06-15T00:00:00Z",
        ),
        _issue(
            "11", "RGW multisite sync fails",
            desc="Multisite replication stalls.",
            components=["rgw"],
            updated_at="2025-06-10T00:00:00Z",
        ),
        _issue(
            "12", "MON election flapping",
            desc="Monitor keeps switching leaders.",
            components=["mon"],
            status="resolved",
            updated_at="2025-05-01T00:00:00Z",
        ),
    ]


@pytest.fixture
def client(issues):
    pytest.importorskip("rank_bm25", reason="rank-bm25 required")
    starlette = pytest.importorskip("starlette", reason="starlette required")
    httpx = pytest.importorskip("httpx", reason="httpx required for TestClient")
    from starlette.testclient import TestClient

    from ceph_issue_kb.server.rest_api import create_app

    engine = SearchEngine.from_issues(issues)
    kb = KnowledgeBase(search_engine=engine)
    app = create_app(kb)
    return TestClient(app)


# -- POST /api/search_issues -----------------------------------------------


class TestSearchIssuesEndpoint:
    def test_basic_search(self, client):
        resp = client.post("/api/search_issues", json={"query": "OSD crash"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0

    def test_missing_query(self, client):
        resp = client.post("/api/search_issues", json={})
        assert resp.status_code == 400
        assert "error" in resp.json()

    def test_with_filters(self, client):
        resp = client.post("/api/search_issues", json={
            "query": "crash",
            "component": "osd",
            "limit": 5,
        })
        assert resp.status_code == 200
        for item in resp.json()["results"]:
            assert "osd" in item["components"]


# -- POST /api/find_similar -------------------------------------------------


class TestFindSimilarEndpoint:
    def test_basic(self, client):
        resp = client.post("/api/find_similar", json={
            "description": "OSD crashes during scrub",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "results" in data

    def test_missing_description(self, client):
        resp = client.post("/api/find_similar", json={})
        assert resp.status_code == 400


# -- POST /api/is_known_issue -----------------------------------------------


class TestIsKnownIssueEndpoint:
    def test_known(self, client):
        resp = client.post("/api/is_known_issue", json={
            "error_message": "OSD crash deep scrub segfault",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "known" in data

    def test_missing_error_message(self, client):
        resp = client.post("/api/is_known_issue", json={})
        assert resp.status_code == 400


# -- POST /api/find_workaround ----------------------------------------------


class TestFindWorkaroundEndpoint:
    def test_basic(self, client):
        resp = client.post("/api/find_workaround", json={
            "query": "OSD crash deep scrub",
        })
        assert resp.status_code == 200
        assert "workarounds" in resp.json()

    def test_missing_query(self, client):
        resp = client.post("/api/find_workaround", json={})
        assert resp.status_code == 400


# -- POST /api/find_fix -----------------------------------------------------


class TestFindFixEndpoint:
    def test_basic(self, client):
        resp = client.post("/api/find_fix", json={
            "query": "OSD crash deep scrub",
        })
        assert resp.status_code == 200
        assert "fixes" in resp.json()

    def test_missing_query(self, client):
        resp = client.post("/api/find_fix", json={})
        assert resp.status_code == 400


# -- POST /api/find_related -------------------------------------------------


class TestFindRelatedEndpoint:
    def test_not_found(self, client):
        resp = client.post("/api/find_related", json={
            "issue_id": "0000000000000000",
        })
        assert resp.status_code == 200
        assert "error" in resp.json()

    def test_missing_issue_id(self, client):
        resp = client.post("/api/find_related", json={})
        assert resp.status_code == 400


# -- POST /api/search_stacktrace -------------------------------------------


class TestSearchStacktraceEndpoint:
    def test_basic(self, client):
        resp = client.post("/api/search_stacktrace", json={
            "stacktrace": "at ceph_osd::do_scrub()",
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "fingerprint" in data

    def test_missing_stacktrace(self, client):
        resp = client.post("/api/search_stacktrace", json={})
        assert resp.status_code == 400


# -- POST /api/search_health_warning ----------------------------------------


class TestSearchHealthWarningEndpoint:
    def test_basic(self, client):
        resp = client.post("/api/search_health_warning", json={
            "warning": "HEALTH_WARN",
        })
        assert resp.status_code == 200
        assert resp.json()["total"] > 0

    def test_missing_warning(self, client):
        resp = client.post("/api/search_health_warning", json={})
        assert resp.status_code == 400


# -- GET /api/hot_issues ----------------------------------------------------


class TestHotIssuesEndpoint:
    def test_basic(self, client):
        resp = client.get("/api/hot_issues")
        assert resp.status_code == 200
        assert resp.json()["total"] > 0

    def test_with_component(self, client):
        resp = client.get("/api/hot_issues?component=osd")
        assert resp.status_code == 200
        for item in resp.json()["results"]:
            assert "osd" in item["components"]


# -- GET /api/component_health/{component} ----------------------------------


class TestComponentHealthEndpoint:
    def test_basic(self, client):
        resp = client.get("/api/component_health/osd")
        assert resp.status_code == 200
        data = resp.json()
        assert data["component"] == "osd"
        assert "open_issues" in data


# -- GET /health ------------------------------------------------------------


class TestHealthEndpoint:
    def test_ok(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["total_issues"] > 0


# -- GET /capabilities ------------------------------------------------------


class TestCapabilitiesEndpoint:
    def test_ok(self, client):
        resp = client.get("/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "ceph-issue-kb"
        assert "search_issues" in data["operations"]
