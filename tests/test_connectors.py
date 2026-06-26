"""Tests for the connector framework and Redmine connector.

Uses ``responses`` to mock HTTP requests — no real API calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
import responses

from ceph_issue_kb.config import AuthConfig, ConnectorConfig
from ceph_issue_kb.connectors import ConnectorError, get_connector
from ceph_issue_kb.connectors.auth import AuthError, AuthProvider
from ceph_issue_kb.connectors.bugzilla import BugzillaConnector
from ceph_issue_kb.connectors.jira import JiraConnector
from ceph_issue_kb.connectors.redmine import RedmineConnector
from ceph_issue_kb.connectors.rhkb import RHKBConnector

FIXTURES = Path(__file__).parent / "fixtures"


def _redmine_config() -> ConnectorConfig:
    return ConnectorConfig(
        name="ceph-tracker",
        connector_type="redmine",
        enabled=True,
        base_url="https://tracker.ceph.com",
        auth=AuthConfig(method="none"),
        rate_limit=100,
        since="2024-01-01",
        extra={"project": "ceph"},
    )


class TestAuthProvider:
    def test_none_auth(self):
        creds = AuthProvider().resolve(AuthConfig(method="none"))
        assert creds.method == "none"

    def test_api_token_from_env(self):
        auth = AuthConfig(method="api_token", username_env="TEST_USER", token_env="TEST_TOKEN")
        with patch.dict("os.environ", {"TEST_USER": "alice", "TEST_TOKEN": "secret"}):
            creds = AuthProvider().resolve(auth)
            assert creds.username == "alice"
            assert creds.token == "secret"

    def test_missing_env_raises(self):
        auth = AuthConfig(method="api_key", key_env="MISSING_KEY")
        with patch.dict("os.environ", {}, clear=True):
            with pytest.raises(AuthError, match="not set or empty"):
                AuthProvider().resolve(auth)

    def test_unknown_method_raises(self):
        auth = AuthConfig(method="kerberos")
        with pytest.raises(AuthError, match="Unknown auth method"):
            AuthProvider().resolve(auth)


class TestGetConnector:
    def test_redmine(self):
        connector = get_connector(_redmine_config())
        assert isinstance(connector, RedmineConnector)

    def test_jira(self):
        cfg = ConnectorConfig(
            name="ibm-jira",
            connector_type="jira",
            enabled=True,
            base_url="https://ibm-ceph.atlassian.net",
            auth=AuthConfig(
                method="api_token",
                username_env="JIRA_USERNAME",
                token_env="JIRA_API_TOKEN",
            ),
            rate_limit=100,
            extra={"project": "IBMCEPH"},
        )
        with patch.dict(
            "os.environ",
            {"JIRA_USERNAME": "u@example.com", "JIRA_API_TOKEN": "tok"},
        ):
            connector = get_connector(cfg)
        assert isinstance(connector, JiraConnector)

    def test_bugzilla(self):
        cfg = ConnectorConfig(
            name="redhat-bugzilla",
            connector_type="bugzilla",
            enabled=True,
            base_url="https://bugzilla.redhat.com",
            auth=AuthConfig(method="api_key", key_env="BUGZILLA_API_KEY"),
            rate_limit=100,
            extra={"product": "Red Hat Ceph Storage"},
        )
        with patch.dict("os.environ", {"BUGZILLA_API_KEY": "key123"}):
            connector = get_connector(cfg)
        assert isinstance(connector, BugzillaConnector)

    def test_rhkb(self):
        cfg = ConnectorConfig(
            name="redhat-kb",
            connector_type="rhkb",
            enabled=True,
            base_url="https://access.redhat.com",
            auth=AuthConfig(method="cookie", cookie_env="RH_SSO_COOKIE"),
            rate_limit=100,
        )
        with patch.dict("os.environ", {"RH_SSO_COOKIE": "cookie_val"}):
            connector = get_connector(cfg)
        assert isinstance(connector, RHKBConnector)

    def test_unknown_type(self):
        cfg = _redmine_config()
        cfg.connector_type = "unknown"
        with pytest.raises(ConnectorError, match="Unknown connector type"):
            get_connector(cfg)


class TestRedmineConnector:
    def setup_method(self):
        self.config = _redmine_config()
        self.connector = RedmineConnector(self.config)

    def test_authenticate_is_noop(self):
        self.connector.authenticate()

    @responses.activate
    def test_fetch_single_issue(self):
        fixture = json.loads((FIXTURES / "redmine_issue.json").read_text())
        responses.add(
            responses.GET,
            "https://tracker.ceph.com/issues/68051.json",
            json=fixture,
            status=200,
        )
        raw = self.connector.fetch("68051")
        assert raw.source == "ceph-tracker"
        assert raw.source_id == "68051"
        assert raw.source_url == "https://tracker.ceph.com/issues/68051"
        assert raw.data["subject"] == "OSD crash during deep scrub with BlueStore"
        assert len(raw.data["journals"]) == 2

    @responses.activate
    def test_health_ok(self):
        responses.add(
            responses.GET,
            "https://tracker.ceph.com/issues.json",
            json={"issues": [], "total_count": 4500, "offset": 0, "limit": 1},
            status=200,
        )
        h = self.connector.health()
        assert h["ok"] is True
        assert h["total_issues"] == 4500

    @responses.activate
    def test_health_failure(self):
        responses.add(
            responses.GET,
            "https://tracker.ceph.com/issues.json",
            status=500,
        )
        h = self.connector.health()
        assert h["ok"] is False

    @responses.activate
    def test_search_with_pagination(self):
        page_data = json.loads((FIXTURES / "redmine_issues_page.json").read_text())
        issue_data = json.loads((FIXTURES / "redmine_issue.json").read_text())
        responses.add(
            responses.GET,
            "https://tracker.ceph.com/issues.json",
            json=page_data,
            status=200,
        )
        responses.add(
            responses.GET,
            "https://tracker.ceph.com/issues/68051.json",
            json=issue_data,
            status=200,
        )
        issue2_data = {
            "issue": {
                "id": 68050,
                "subject": "RGW multisite sync stuck after zone rename",
                "journals": [],
                "relations": [],
            }
        }
        responses.add(
            responses.GET,
            "https://tracker.ceph.com/issues/68050.json",
            json=issue2_data,
            status=200,
        )
        results = list(self.connector.search("OSD crash"))
        assert len(results) == 2
        assert results[0].source_id == "68051"
        assert results[1].source_id == "68050"

    @responses.activate
    def test_fetch_updates_since(self):
        page_data = json.loads((FIXTURES / "redmine_issues_page.json").read_text())
        issue_data = json.loads((FIXTURES / "redmine_issue.json").read_text())
        responses.add(
            responses.GET,
            "https://tracker.ceph.com/issues.json",
            json=page_data,
            status=200,
        )
        responses.add(
            responses.GET,
            "https://tracker.ceph.com/issues/68051.json",
            json=issue_data,
            status=200,
        )
        issue2_data = {
            "issue": {
                "id": 68050,
                "subject": "RGW multisite sync stuck after zone rename",
                "journals": [],
                "relations": [],
            }
        }
        responses.add(
            responses.GET,
            "https://tracker.ceph.com/issues/68050.json",
            json=issue2_data,
            status=200,
        )
        results = list(self.connector.fetch_updates("2024-10-01"))
        assert len(results) == 2

    @responses.activate
    def test_fetch_404_raises(self):
        responses.add(
            responses.GET,
            "https://tracker.ceph.com/issues/99999.json",
            status=404,
        )
        with pytest.raises(ConnectorError):
            self.connector.fetch("99999")
