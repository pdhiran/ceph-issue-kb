"""Tests for configuration loading."""

from pathlib import Path

import pytest

from ceph_issue_kb.config import AuthConfig, ConnectorConfig, load_config


class TestAuthConfig:
    def test_none_auth(self):
        auth = AuthConfig.from_dict(None)
        assert auth.method == "none"

    def test_api_token_auth(self):
        auth = AuthConfig.from_dict(
            {"method": "api_token", "username_env": "JIRA_USER", "token_env": "JIRA_TOKEN"}
        )
        assert auth.method == "api_token"
        assert auth.username_env == "JIRA_USER"
        assert auth.token_env == "JIRA_TOKEN"


class TestConnectorConfig:
    def test_from_dict(self):
        cc = ConnectorConfig.from_dict(
            "ceph-tracker",
            {
                "type": "redmine",
                "enabled": True,
                "base_url": "https://tracker.ceph.com/",
                "project": "ceph",
                "rate_limit": 10,
            },
        )
        assert cc.name == "ceph-tracker"
        assert cc.connector_type == "redmine"
        assert cc.base_url == "https://tracker.ceph.com"
        assert cc.extra == {"project": "ceph"}

    def test_defaults(self):
        cc = ConnectorConfig.from_dict("test", {"type": "x"})
        assert cc.enabled is True
        assert cc.since == "2024-01-01"
        assert cc.rate_limit == 10


class TestLoadConfig:
    def test_load_connectors_yaml(self, tmp_path: Path):
        yaml_file = tmp_path / "connectors.yaml"
        yaml_file.write_text(
            "connectors:\n"
            "  test-conn:\n"
            "    type: redmine\n"
            "    enabled: true\n"
            "    base_url: https://example.com\n"
        )
        cfg = load_config(yaml_file)
        assert "test-conn" in cfg.connectors
        assert cfg.connectors["test-conn"].connector_type == "redmine"

    def test_missing_file_raises(self, tmp_path: Path):
        with pytest.raises(FileNotFoundError):
            load_config(tmp_path / "nope.yaml")

    def test_missing_connectors_key(self, tmp_path: Path):
        yaml_file = tmp_path / "bad.yaml"
        yaml_file.write_text("foo: bar\n")
        with pytest.raises(ValueError, match="missing 'connectors' key"):
            load_config(yaml_file)

    def test_enabled_connectors(self, tmp_path: Path):
        yaml_file = tmp_path / "connectors.yaml"
        yaml_file.write_text(
            "connectors:\n"
            "  a:\n"
            "    type: redmine\n"
            "    enabled: true\n"
            "    base_url: https://a.com\n"
            "  b:\n"
            "    type: jira\n"
            "    enabled: false\n"
            "    base_url: https://b.com\n"
        )
        cfg = load_config(yaml_file)
        enabled = cfg.enabled_connectors
        assert "a" in enabled
        assert "b" not in enabled
