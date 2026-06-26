"""Tests for the Bugzilla connector.

Uses ``responses`` to mock HTTP requests — no real API calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import responses

from ceph_issue_kb.config import AuthConfig, ConnectorConfig
from ceph_issue_kb.connectors.base import ConnectorError
from ceph_issue_kb.connectors.bugzilla import PAGE_SIZE, BugzillaConnector

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://bugzilla.redhat.com"


def _bugzilla_config() -> ConnectorConfig:
    return ConnectorConfig(
        name="redhat-bugzilla",
        connector_type="bugzilla",
        enabled=True,
        base_url=BASE_URL,
        auth=AuthConfig(method="api_key", key_env="BUGZILLA_API_KEY"),
        rate_limit=100,
        since="2024-01-01",
        extra={"product": "Red Hat Ceph Storage"},
    )


@pytest.fixture()
def connector():
    with patch.dict("os.environ", {"BUGZILLA_API_KEY": "test-bz-key-123"}):
        return BugzillaConnector(_bugzilla_config())


class TestBugzillaConnectorInit:
    def test_creates_from_config(self, connector):
        assert connector.name == "redhat-bugzilla"
        assert connector.product == "Red Hat Ceph Storage"
        assert connector.base_url == BASE_URL

    def test_session_has_api_key_header(self, connector):
        assert connector._session.headers["X-BUGZILLA-API-KEY"] == "test-bz-key-123"

    def test_session_headers(self, connector):
        assert connector._session.headers["Content-Type"] == "application/json"
        assert connector._session.headers["Accept"] == "application/json"


class TestBugzillaAuthenticate:
    @responses.activate
    def test_authenticate_success(self, connector):
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/whoami",
            json={"id": 12345, "real_name": "Test User", "name": "testuser"},
            status=200,
        )
        connector.authenticate()

    @responses.activate
    def test_authenticate_no_user_id(self, connector):
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/whoami",
            json={"error": True, "message": "invalid key"},
            status=200,
        )
        with pytest.raises(ConnectorError, match="authentication failed"):
            connector.authenticate()

    @responses.activate
    def test_authenticate_failure(self, connector):
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/whoami",
            status=401,
        )
        with pytest.raises(ConnectorError, match="Bugzilla request failed"):
            connector.authenticate()


class TestBugzillaFetch:
    @responses.activate
    def test_fetch_single_bug(self, connector):
        bug_fixture = json.loads((FIXTURES / "bugzilla_bug.json").read_text())
        comments_fixture = json.loads(
            (FIXTURES / "bugzilla_comments.json").read_text()
        )
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/bug/2189456",
            json=bug_fixture,
            status=200,
        )
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/bug/2189456/comment",
            json=comments_fixture,
            status=200,
        )
        raw = connector.fetch("2189456")
        assert raw.source == "redhat-bugzilla"
        assert raw.source_id == "2189456"
        assert raw.source_url == f"{BASE_URL}/show_bug.cgi?id=2189456"
        assert "BlueStore" in raw.data["summary"]
        assert len(raw.data["comments"]) == 2

    @responses.activate
    def test_fetch_not_found(self, connector):
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/bug/9999999",
            json={"bugs": [], "faults": []},
            status=200,
        )
        with pytest.raises(ConnectorError, match="not found"):
            connector.fetch("9999999")

    @responses.activate
    def test_fetch_http_error(self, connector):
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/bug/2189456",
            status=500,
        )
        with pytest.raises(ConnectorError):
            connector.fetch("2189456")


class TestBugzillaSearch:
    @responses.activate
    def test_search_returns_bugs(self, connector):
        search_fixture = json.loads(
            (FIXTURES / "bugzilla_search.json").read_text()
        )
        batch_comments = {
            "bugs": {
                "2189456": {
                    "comments": [{"id": 1, "text": "comment 1", "creator": "u@rh.com"}]
                },
                "2189400": {
                    "comments": [{"id": 2, "text": "comment 2", "creator": "u@rh.com"}]
                },
            }
        }
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/bug",
            json=search_fixture,
            status=200,
        )
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/bug/2189456,2189400/comment",
            json=batch_comments,
            status=200,
        )
        results = list(connector.search("OSD memory"))
        assert len(results) == 2
        assert results[0].source_id == "2189456"
        assert results[1].source_id == "2189400"
        assert results[0].data["comments"][0]["text"] == "comment 1"

    @responses.activate
    def test_search_with_since(self, connector):
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/bug",
            json={"bugs": []},
            status=200,
        )
        list(connector.search("OSD", since="2024-10-01"))
        request = responses.calls[0].request
        assert "last_change_time=2024-10-01" in request.url

    @responses.activate
    def test_search_respects_limit(self, connector):
        search_fixture = json.loads(
            (FIXTURES / "bugzilla_search.json").read_text()
        )
        batch_comments = {
            "bugs": {
                "2189456": {"comments": []},
                "2189400": {"comments": []},
            }
        }
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/bug",
            json=search_fixture,
            status=200,
        )
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/bug/2189456,2189400/comment",
            json=batch_comments,
            status=200,
        )
        results = list(connector.search("OSD", limit=1))
        assert len(results) == 1


class TestBugzillaFetchUpdates:
    @responses.activate
    def test_fetch_updates_since(self, connector):
        search_fixture = json.loads(
            (FIXTURES / "bugzilla_search.json").read_text()
        )
        batch_comments = {
            "bugs": {
                "2189456": {"comments": []},
                "2189400": {"comments": []},
            }
        }
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/bug",
            json=search_fixture,
            status=200,
        )
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/bug/2189456,2189400/comment",
            json=batch_comments,
            status=200,
        )
        results = list(connector.fetch_updates("2024-10-01"))
        assert len(results) == 2
        request = responses.calls[0].request
        assert "last_change_time=2024-10-01" in request.url


class TestBugzillaHealth:
    @responses.activate
    def test_health_ok(self, connector):
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/bug",
            json={
                "bugs": [{"id": 1, "summary": "test"}],
                "total_matches": 42,
            },
            status=200,
        )
        h = connector.health()
        assert h["ok"] is True
        assert h["total_issues"] == 42
        assert "Red Hat Ceph Storage" in h["message"]

    @responses.activate
    def test_health_failure(self, connector):
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/bug",
            status=503,
        )
        h = connector.health()
        assert h["ok"] is False
        assert h["total_issues"] == 0


class TestBugzillaPagination:
    @responses.activate
    def test_search_pagination(self, connector):
        """Multiple pages are fetched; all results are yielded."""
        page1_bugs = [
            {"id": 100 + i, "summary": f"bug {i}"}
            for i in range(PAGE_SIZE)
        ]
        page2_bugs = [
            {"id": 200, "summary": "last bug"},
        ]
        page1_ids = ",".join(str(b["id"]) for b in page1_bugs)
        page1_comments = {
            "bugs": {
                str(b["id"]): {"comments": []} for b in page1_bugs
            }
        }
        page2_comments = {
            "bugs": {"200": {"comments": []}}
        }
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/bug",
            json={"bugs": page1_bugs},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/bug/{page1_ids}/comment",
            json=page1_comments,
            status=200,
        )
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/bug",
            json={"bugs": page2_bugs},
            status=200,
        )
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/bug/200/comment",
            json=page2_comments,
            status=200,
        )
        results = list(connector.search("test", limit=PAGE_SIZE + 1))
        assert len(results) == PAGE_SIZE + 1
        assert results[0].source_id == "100"
        assert results[-1].source_id == "200"
