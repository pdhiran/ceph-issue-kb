"""Tests for the JIRA connector.

Uses ``responses`` to mock HTTP requests — no real API calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import responses
from urllib.parse import unquote_plus

from ceph_issue_kb.config import AuthConfig, ConnectorConfig
from ceph_issue_kb.connectors.base import ConnectorError
from ceph_issue_kb.connectors.jira import JiraConnector

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://ibm-ceph.atlassian.net"


def _jira_config() -> ConnectorConfig:
    return ConnectorConfig(
        name="ibm-jira",
        connector_type="jira",
        enabled=True,
        base_url=BASE_URL,
        auth=AuthConfig(
            method="api_token",
            username_env="JIRA_USERNAME",
            token_env="JIRA_API_TOKEN",
        ),
        rate_limit=100,
        since="2024-01-01",
        extra={"project": "IBMCEPH"},
    )


@pytest.fixture()
def connector():
    with patch.dict(
        "os.environ",
        {"JIRA_USERNAME": "user@example.com", "JIRA_API_TOKEN": "test-token"},
    ):
        return JiraConnector(_jira_config())


class TestJiraConnectorInit:
    def test_creates_from_config(self, connector):
        assert connector.name == "ibm-jira"
        assert connector.project == "IBMCEPH"
        assert connector.base_url == BASE_URL

    def test_session_has_auth(self, connector):
        assert connector._session.auth == ("user@example.com", "test-token")

    def test_session_headers(self, connector):
        assert connector._session.headers["Content-Type"] == "application/json"
        assert connector._session.headers["Accept"] == "application/json"


class TestJiraAuthenticate:
    @responses.activate
    def test_authenticate_success(self, connector):
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/api/3/myself",
            json={"accountId": "abc123", "displayName": "Test User"},
            status=200,
        )
        connector.authenticate()

    @responses.activate
    def test_authenticate_failure(self, connector):
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/api/3/myself",
            status=401,
        )
        with pytest.raises(ConnectorError, match="JIRA request failed"):
            connector.authenticate()


class TestJiraFetch:
    @responses.activate
    def test_fetch_single_issue(self, connector):
        fixture = json.loads((FIXTURES / "jira_issue.json").read_text())
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/api/3/issue/IBMCEPH-1234",
            json=fixture,
            status=200,
        )
        raw = connector.fetch("IBMCEPH-1234")
        assert raw.source == "ibm-jira"
        assert raw.source_id == "IBMCEPH-1234"
        assert raw.source_url == f"{BASE_URL}/browse/IBMCEPH-1234"
        assert raw.data["key"] == "IBMCEPH-1234"
        assert "RGW" in raw.data["fields"]["summary"]

    @responses.activate
    def test_fetch_404_raises(self, connector):
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/api/3/issue/IBMCEPH-9999",
            status=404,
        )
        with pytest.raises(ConnectorError):
            connector.fetch("IBMCEPH-9999")

    @responses.activate
    def test_fetch_passes_expand_params(self, connector):
        fixture = json.loads((FIXTURES / "jira_issue.json").read_text())
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/api/3/issue/IBMCEPH-1234",
            json=fixture,
            status=200,
        )
        connector.fetch("IBMCEPH-1234")
        request = responses.calls[0].request
        assert "expand=renderedFields" in request.url
        assert "fields=%2Aall" in request.url or "fields=*all" in request.url


class TestJiraSearch:
    @responses.activate
    def test_search_returns_issues(self, connector):
        fixture = json.loads((FIXTURES / "jira_search.json").read_text())
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/api/3/search/jql",
            json=fixture,
            status=200,
        )
        results = list(connector.search("resharding"))
        assert len(results) == 2
        assert results[0].source_id == "IBMCEPH-1234"
        assert results[1].source_id == "IBMCEPH-1220"

    @responses.activate
    def test_search_with_since(self, connector):
        fixture = json.loads((FIXTURES / "jira_search.json").read_text())
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/api/3/search/jql",
            json=fixture,
            status=200,
        )
        list(connector.search("resharding", since="2024-10-01"))
        request = responses.calls[0].request
        assert "2024-10-01" in request.url

    @responses.activate
    def test_search_respects_limit(self, connector):
        fixture = json.loads((FIXTURES / "jira_search.json").read_text())
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/api/3/search/jql",
            json=fixture,
            status=200,
        )
        results = list(connector.search("resharding", limit=1))
        assert len(results) == 1

    @responses.activate
    def test_search_pagination(self, connector):
        page1 = {
            "issues": [
                {
                    "key": "IBMCEPH-1234",
                    "fields": {"summary": "Issue 1"},
                }
            ],
            "nextPageToken": "token-page-2",
            "isLast": False,
        }
        page2 = {
            "issues": [
                {
                    "key": "IBMCEPH-1220",
                    "fields": {"summary": "Issue 2"},
                }
            ],
            "isLast": True,
        }
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/api/3/search/jql",
            json=page1,
            status=200,
        )
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/api/3/search/jql",
            json=page2,
            status=200,
        )
        results = list(connector.search("test"))
        assert len(results) == 2
        assert results[0].source_id == "IBMCEPH-1234"
        assert results[1].source_id == "IBMCEPH-1220"
        request2 = responses.calls[1].request
        assert "nextPageToken=token-page-2" in request2.url


class TestJiraFetchUpdates:
    @responses.activate
    def test_fetch_updates_since(self, connector):
        fixture = json.loads((FIXTURES / "jira_search.json").read_text())
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/api/3/search/jql",
            json=fixture,
            status=200,
        )
        results = list(connector.fetch_updates("2024-10-01"))
        assert len(results) == 2
        request = responses.calls[0].request
        assert "2024-10-01" in request.url


class TestJiraJqlEscaping:
    def test_escape_jql_double_quotes(self):
        assert JiraConnector._escape_jql('foo"bar') == 'foo\\"bar'

    def test_escape_jql_backslash(self):
        assert JiraConnector._escape_jql("foo\\bar") == "foo\\\\bar"

    def test_escape_jql_combined(self):
        assert JiraConnector._escape_jql('a\\"b') == 'a\\\\\\"b'

    @responses.activate
    def test_search_with_quotes_in_query(self, connector):
        fixture = {
            "issues": [],
            "isLast": True,
        }
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/api/3/search/jql",
            json=fixture,
            status=200,
        )
        list(connector.search('error "null pointer"'))
        request = responses.calls[0].request
        decoded = unquote_plus(request.url)
        assert '\\"null pointer\\"' in decoded


class TestJiraHealth:
    @responses.activate
    def test_health_ok(self, connector):
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/api/3/search/jql",
            json={
                "issues": [{"key": "IBMCEPH-1", "fields": {"summary": "test"}}],
                "isLast": True,
            },
            status=200,
        )
        h = connector.health()
        assert h["ok"] is True
        assert "IBMCEPH" in h["message"]

    @responses.activate
    def test_health_failure(self, connector):
        responses.add(
            responses.GET,
            f"{BASE_URL}/rest/api/3/search/jql",
            status=503,
        )
        h = connector.health()
        assert h["ok"] is False
