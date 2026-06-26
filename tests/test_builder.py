"""Tests for the build pipeline with mocked connectors."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ceph_issue_kb.config import AuthConfig, Config, ConnectorConfig
from ceph_issue_kb.models import RawIssue


FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


def _make_config(*source_names: str) -> Config:
    connectors = {}
    for name in source_names:
        connectors[name] = ConnectorConfig(
            name=name,
            connector_type="redmine",
            enabled=True,
            base_url="https://tracker.ceph.com",
            auth=AuthConfig(method="none"),
            rate_limit=100,
            since="2024-01-01",
        )
    return Config(connectors=connectors)


def _make_mock_connector(name: str, raw_issues: list[RawIssue]) -> MagicMock:
    connector = MagicMock()
    connector.name = name

    def fake_fetch_updates(since: str):
        yield from raw_issues

    connector.fetch_updates = fake_fetch_updates
    return connector


class TestBuildIndex:
    def test_basic_pipeline(self):
        rank_bm25 = pytest.importorskip("rank_bm25", reason="rank-bm25 not installed")
        from ceph_issue_kb.indexer.builder import build_index

        fixture = _load("redmine_issue.json")
        issue_data = fixture["issue"]
        raw = RawIssue(
            source="ceph-tracker",
            source_id=str(issue_data["id"]),
            source_url=f"https://tracker.ceph.com/issues/{issue_data['id']}",
            data=issue_data,
        )

        config = _make_config("ceph-tracker")
        mock_connector = _make_mock_connector("ceph-tracker", [raw])

        with tempfile.TemporaryDirectory() as tmpdir:
            metadata = build_index(
                config,
                tmpdir,
                since="2024-01-01",
                connectors_override={"ceph-tracker": mock_connector},
            )

            assert metadata["total_issues"] == 1
            assert "ceph-tracker" in metadata["sources"]
            assert metadata["sources"]["ceph-tracker"]["normalized"] == 1

            source_dir = Path(tmpdir) / "ceph-tracker"
            assert source_dir.exists()
            assert (source_dir / "issues.json").exists()

            issues_data = json.loads((source_dir / "issues.json").read_text())
            assert len(issues_data) == 1
            assert issues_data[0]["source_id"] == "68051"

    def test_multiple_sources(self):
        rank_bm25 = pytest.importorskip("rank_bm25", reason="rank-bm25 not installed")
        from ceph_issue_kb.indexer.builder import build_index

        redmine_fixture = _load("redmine_issue.json")
        redmine_data = redmine_fixture["issue"]
        redmine_raw = RawIssue(
            source="ceph-tracker",
            source_id=str(redmine_data["id"]),
            source_url=f"https://tracker.ceph.com/issues/{redmine_data['id']}",
            data=redmine_data,
        )

        jira_fixture = _load("jira_issue.json")
        jira_raw = RawIssue(
            source="ibm-jira",
            source_id=jira_fixture["key"],
            source_url=f"https://ibm-ceph.atlassian.net/browse/{jira_fixture['key']}",
            data=jira_fixture,
        )

        config = _make_config("ceph-tracker", "ibm-jira")
        connectors = {
            "ceph-tracker": _make_mock_connector("ceph-tracker", [redmine_raw]),
            "ibm-jira": _make_mock_connector("ibm-jira", [jira_raw]),
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            metadata = build_index(
                config, tmpdir, connectors_override=connectors,
            )
            assert metadata["total_issues"] == 2
            assert (Path(tmpdir) / "ceph-tracker" / "issues.json").exists()
            assert (Path(tmpdir) / "ibm-jira" / "issues.json").exists()
            assert (Path(tmpdir) / "metadata.json").exists()
            assert (Path(tmpdir) / "merged_bm25_index.json").exists()

    def test_connector_failure_skips_gracefully(self):
        rank_bm25 = pytest.importorskip("rank_bm25", reason="rank-bm25 not installed")
        from ceph_issue_kb.connectors.base import ConnectorError
        from ceph_issue_kb.indexer.builder import build_index

        failing = MagicMock()
        failing.name = "failing-source"

        def fail_fetch(since):
            raise ConnectorError("API down")

        failing.fetch_updates = fail_fetch

        config = _make_config("failing-source")

        with tempfile.TemporaryDirectory() as tmpdir:
            metadata = build_index(
                config, tmpdir, connectors_override={"failing-source": failing},
            )
            assert metadata["total_issues"] == 0
            assert "error" in metadata["sources"]["failing-source"]

    def test_metadata_file_written(self):
        rank_bm25 = pytest.importorskip("rank_bm25", reason="rank-bm25 not installed")
        from ceph_issue_kb.indexer.builder import build_index

        config = _make_config("empty-source")
        empty_connector = _make_mock_connector("empty-source", [])

        with tempfile.TemporaryDirectory() as tmpdir:
            build_index(
                config, tmpdir, connectors_override={"empty-source": empty_connector},
            )
            meta_path = Path(tmpdir) / "metadata.json"
            assert meta_path.exists()
            meta = json.loads(meta_path.read_text())
            assert "built_at" in meta
            assert "total_issues" in meta

    def test_bugzilla_source(self):
        rank_bm25 = pytest.importorskip("rank_bm25", reason="rank-bm25 not installed")
        from ceph_issue_kb.indexer.builder import build_index

        bug_fixture = _load("bugzilla_bug.json")
        comments_fixture = _load("bugzilla_comments.json")
        bug_data = bug_fixture["bugs"][0]
        bug_id = str(bug_data["id"])
        bug_data["comments"] = comments_fixture["bugs"][bug_id]["comments"]

        raw = RawIssue(
            source="redhat-bugzilla",
            source_id=bug_id,
            source_url=f"https://bugzilla.redhat.com/show_bug.cgi?id={bug_id}",
            data=bug_data,
        )

        config = _make_config("redhat-bugzilla")
        mock_connector = _make_mock_connector("redhat-bugzilla", [raw])

        with tempfile.TemporaryDirectory() as tmpdir:
            metadata = build_index(
                config, tmpdir, connectors_override={"redhat-bugzilla": mock_connector},
            )
            assert metadata["total_issues"] == 1
            issues_data = json.loads(
                (Path(tmpdir) / "redhat-bugzilla" / "issues.json").read_text()
            )
            assert issues_data[0]["source"] == "redhat-bugzilla"
