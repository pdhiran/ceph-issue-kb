"""Tests for the Red Hat Knowledge Base connector.

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
from ceph_issue_kb.connectors.rhkb import RHKBConnector, _HYDRA_SEARCH_PATH

FIXTURES = Path(__file__).parent / "fixtures"
BASE_URL = "https://access.redhat.com"
HYDRA_URL = f"{BASE_URL}{_HYDRA_SEARCH_PATH}"


def _rhkb_config(*, method: str = "cookie") -> ConnectorConfig:
    if method == "offline_token":
        auth = AuthConfig(method="offline_token", token_env="RH_OFFLINE_TOKEN")
    else:
        auth = AuthConfig(method="cookie", cookie_env="RH_SSO_COOKIE")
    return ConnectorConfig(
        name="redhat-kb",
        connector_type="rhkb",
        enabled=True,
        base_url=BASE_URL,
        auth=auth,
        rate_limit=100,
        since="2024-01-01",
        extra={},
    )


@pytest.fixture()
def connector():
    with patch.dict("os.environ", {"RH_SSO_COOKIE": "test-sso-cookie-value"}):
        return RHKBConnector(_rhkb_config(method="cookie"))


@pytest.fixture()
def token_connector():
    with patch.dict("os.environ", {"RH_OFFLINE_TOKEN": "test-offline-token"}):
        return RHKBConnector(_rhkb_config(method="offline_token"))


class TestRHKBConnectorInit:
    def test_creates_from_config(self, connector):
        assert connector.name == "redhat-kb"
        assert connector.base_url == BASE_URL

    def test_cookie_auth_sets_cookie(self, connector):
        cookie = connector._session.cookies.get("rh_jwt", domain=".redhat.com")
        assert cookie == "test-sso-cookie-value"

    def test_offline_token_stored(self, token_connector):
        assert token_connector._offline_token == "test-offline-token"

    def test_session_headers(self, connector):
        assert connector._session.headers["Accept"] == "application/json"


class TestRHKBAuthenticate:
    @responses.activate
    def test_authenticate_success(self, connector):
        responses.add(
            responses.GET,
            HYDRA_URL,
            json={"response": {"numFound": 100, "docs": []}},
            status=200,
        )
        connector.authenticate()

    @responses.activate
    def test_authenticate_expired_cookie(self, connector):
        responses.add(
            responses.GET,
            HYDRA_URL,
            status=401,
        )
        with pytest.raises(ConnectorError, match="RH KB request failed"):
            connector.authenticate()


class TestRHKBFetch:
    @responses.activate
    def test_fetch_single_article(self, connector):
        fixture = json.loads((FIXTURES / "rhkb_article.json").read_text())
        responses.add(
            responses.GET,
            HYDRA_URL,
            json={"response": {"numFound": 1, "docs": [fixture]}},
            status=200,
        )
        raw = connector.fetch("7045678")
        assert raw.source == "redhat-kb"
        assert raw.source_id == "7045678"
        assert "stuck PG" in raw.data["title"]
        assert raw.data["documentKind"] == "Solution"
        request = responses.calls[0].request
        assert "id%3A7045678" in request.url or "id:7045678" in request.url

    @responses.activate
    def test_fetch_not_found_raises(self, connector):
        responses.add(
            responses.GET,
            HYDRA_URL,
            json={"response": {"numFound": 0, "docs": []}},
            status=200,
        )
        with pytest.raises(ConnectorError, match="not found"):
            connector.fetch("9999999")


class TestRHKBSearch:
    @responses.activate
    def test_search_returns_articles(self, connector):
        fixture = json.loads((FIXTURES / "rhkb_search.json").read_text())
        responses.add(
            responses.GET,
            HYDRA_URL,
            json=fixture,
            status=200,
        )
        results = list(connector.search("stuck PG"))
        assert len(results) == 2
        assert results[0].source_id == "7045678"
        assert results[1].source_id == "7045500"

    @responses.activate
    def test_search_with_since(self, connector):
        fixture = json.loads((FIXTURES / "rhkb_search.json").read_text())
        responses.add(
            responses.GET,
            HYDRA_URL,
            json=fixture,
            status=200,
        )
        list(connector.search("ceph", since="2024-10-01"))
        request = responses.calls[0].request
        assert "lastModifiedDate" in request.url

    @responses.activate
    def test_search_respects_limit(self, connector):
        fixture = json.loads((FIXTURES / "rhkb_search.json").read_text())
        responses.add(
            responses.GET,
            HYDRA_URL,
            json=fixture,
            status=200,
        )
        results = list(connector.search("ceph", limit=1))
        assert len(results) == 1

    @responses.activate
    def test_search_pagination(self, connector):
        page1 = {
            "response": {
                "numFound": 2,
                "start": 0,
                "docs": [
                    {
                        "id": "7045678",
                        "view_uri": f"{BASE_URL}/solutions/7045678",
                        "title": "Article 1",
                        "documentKind": "Solution",
                    }
                ],
            }
        }
        page2 = {
            "response": {
                "numFound": 2,
                "start": 1,
                "docs": [
                    {
                        "id": "7045500",
                        "view_uri": f"{BASE_URL}/solutions/7045500",
                        "title": "Article 2",
                        "documentKind": "Solution",
                    }
                ],
            }
        }
        responses.add(
            responses.GET,
            HYDRA_URL,
            json=page1,
            status=200,
        )
        responses.add(
            responses.GET,
            HYDRA_URL,
            json=page2,
            status=200,
        )
        results = list(connector.search("ceph"))
        assert len(results) == 2
        assert results[0].source_id == "7045678"
        assert results[1].source_id == "7045500"


class TestRHKBFetchUpdates:
    @responses.activate
    def test_fetch_updates_since(self, connector):
        fixture = json.loads((FIXTURES / "rhkb_search.json").read_text())
        responses.add(
            responses.GET,
            HYDRA_URL,
            json=fixture,
            status=200,
        )
        results = list(connector.fetch_updates("2024-10-01"))
        assert len(results) == 2
        request = responses.calls[0].request
        assert "lastModifiedDate" in request.url


class TestRHKBHealth:
    @responses.activate
    def test_health_ok(self, connector):
        responses.add(
            responses.GET,
            HYDRA_URL,
            json={"response": {"numFound": 245, "docs": []}},
            status=200,
        )
        h = connector.health()
        assert h["ok"] is True
        assert h["total_issues"] == 245
        assert "245" in h["message"]

    @responses.activate
    def test_health_failure(self, connector):
        responses.add(
            responses.GET,
            HYDRA_URL,
            status=503,
        )
        h = connector.health()
        assert h["ok"] is False
        assert h["total_issues"] == 0
