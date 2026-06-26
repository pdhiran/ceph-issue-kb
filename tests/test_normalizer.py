"""Tests for the normalizer — RawIssue -> NormalizedIssue for each source."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from ceph_issue_kb.indexer.normalizer import normalize
from ceph_issue_kb.models import NormalizedIssue, RawIssue

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


# ---------------------------------------------------------------------------
# Redmine
# ---------------------------------------------------------------------------


class TestNormalizeRedmine:
    def setup_method(self):
        fixture = _load("redmine_issue.json")
        issue_data = fixture["issue"]
        self.raw = RawIssue(
            source="ceph-tracker",
            source_id=str(issue_data["id"]),
            source_url=f"https://tracker.ceph.com/issues/{issue_data['id']}",
            data=issue_data,
        )
        self.issue = normalize(self.raw)

    def test_returns_normalized_issue(self):
        assert isinstance(self.issue, NormalizedIssue)

    def test_entity_id_is_deterministic(self):
        issue2 = normalize(self.raw)
        assert self.issue.entity_id == issue2.entity_id
        assert len(self.issue.entity_id) == 16

    def test_identity_fields(self):
        assert self.issue.source == "ceph-tracker"
        assert self.issue.source_id == "68051"
        assert "tracker.ceph.com" in self.issue.source_url

    def test_title(self):
        assert self.issue.title == "OSD crash during deep scrub with BlueStore"

    def test_status_and_priority(self):
        assert self.issue.status == "new"
        assert self.issue.priority == "normal"

    def test_summary_truncated(self):
        assert len(self.issue.summary) <= 503  # 500 + "..."

    def test_comments_extracted(self):
        assert len(self.issue.comments) == 2
        assert self.issue.comments[0].author == "John Reviewer"
        assert "reproduce" in self.issue.comments[0].body.lower()

    def test_relationships_extracted(self):
        assert len(self.issue.relationships) == 1
        rel = self.issue.relationships[0]
        assert rel.relation_type == "related"
        assert rel.target_id == "67999"

    def test_components_from_custom_fields(self):
        assert "bluestore" in self.issue.components

    def test_signals_extracted(self):
        assert len(self.issue.stacktraces) > 0 or len(self.issue.assertions) > 0
        assert "HEALTH_WARN" in self.issue.health_warnings
        assert any("osd" in cmd for cmd in self.issue.commands_mentioned)

    def test_configs_extracted(self):
        assert "osd_deep_scrub_interval" in self.issue.configs_mentioned

    def test_reporter(self):
        assert self.issue.reporter == "Jane Dev"

    def test_dates(self):
        assert self.issue.created_at == "2024-10-15T08:00:00Z"
        assert self.issue.updated_at == "2024-10-16T12:00:00Z"


# ---------------------------------------------------------------------------
# JIRA
# ---------------------------------------------------------------------------


class TestNormalizeJira:
    def setup_method(self):
        fixture = _load("jira_issue.json")
        self.raw = RawIssue(
            source="ibm-jira",
            source_id=fixture["key"],
            source_url=f"https://ibm-ceph.atlassian.net/browse/{fixture['key']}",
            data=fixture,
        )
        self.issue = normalize(self.raw)

    def test_returns_normalized_issue(self):
        assert isinstance(self.issue, NormalizedIssue)

    def test_identity_fields(self):
        assert self.issue.source == "ibm-jira"
        assert self.issue.source_id == "IBMCEPH-1234"

    def test_title(self):
        assert "RGW" in self.issue.title
        assert "resharding" in self.issue.title

    def test_status_and_priority(self):
        assert self.issue.status == "in progress"
        assert self.issue.priority == "high"

    def test_comments(self):
        assert len(self.issue.comments) == 2
        assert self.issue.comments[0].author == "Casey Bodley"

    def test_components(self):
        assert "rgw" in self.issue.components
        assert "multisite" in self.issue.components

    def test_labels(self):
        assert "resharding" in self.issue.labels

    def test_versions(self):
        assert "18.2.4" in self.issue.affected_versions
        assert "18.2.5" in self.issue.fixed_versions

    def test_relationships(self):
        assert len(self.issue.relationships) == 1
        assert self.issue.relationships[0].target_id == "IBMCEPH-1100"

    def test_reporter_and_assignee(self):
        assert self.issue.reporter == "Pawan Dhiran"
        assert self.issue.assignee == "Casey Bodley"

    def test_signals_from_description(self):
        assert len(self.issue.log_snippets) > 0 or len(self.issue.commands_mentioned) > 0


# ---------------------------------------------------------------------------
# Bugzilla
# ---------------------------------------------------------------------------


class TestNormalizeBugzilla:
    def setup_method(self):
        bug_fixture = _load("bugzilla_bug.json")
        comments_fixture = _load("bugzilla_comments.json")
        bug_data = bug_fixture["bugs"][0]
        bug_id = str(bug_data["id"])
        bug_data["comments"] = comments_fixture["bugs"][bug_id]["comments"]
        self.raw = RawIssue(
            source="redhat-bugzilla",
            source_id=bug_id,
            source_url=f"https://bugzilla.redhat.com/show_bug.cgi?id={bug_id}",
            data=bug_data,
        )
        self.issue = normalize(self.raw)

    def test_returns_normalized_issue(self):
        assert isinstance(self.issue, NormalizedIssue)

    def test_identity_fields(self):
        assert self.issue.source == "redhat-bugzilla"
        assert self.issue.source_id == "2189456"

    def test_title(self):
        assert "memory leak" in self.issue.title.lower()

    def test_description_from_first_comment(self):
        assert "ungraceful" in self.issue.description.lower()
        assert len(self.issue.description) > 100

    def test_status_and_priority(self):
        assert self.issue.status == "assigned"
        assert self.issue.priority == "high"
        assert self.issue.severity == "high"

    def test_components(self):
        assert "osd" in self.issue.components

    def test_comments(self):
        assert len(self.issue.comments) == 2

    def test_relationships(self):
        assert any(r.relation_type == "blocks" for r in self.issue.relationships)
        assert any(r.target_id == "2189000" for r in self.issue.relationships)

    def test_versions(self):
        assert "7.1" in self.issue.affected_versions
        assert "7.2" in self.issue.fixed_versions

    def test_labels_from_keywords(self):
        assert "Triaged" in self.issue.labels

    def test_configs_extracted(self):
        assert "bluestore_cache_size_ssd" in self.issue.configs_mentioned or \
               "bluestore_cache_autotune" in self.issue.configs_mentioned


# ---------------------------------------------------------------------------
# RHKB
# ---------------------------------------------------------------------------


class TestNormalizeRhkb:
    def setup_method(self):
        fixture = _load("rhkb_article.json")
        self.raw = RawIssue(
            source="redhat-kb",
            source_id=str(fixture["id"]),
            source_url=f"https://access.redhat.com/solutions/{fixture['id']}",
            data=fixture,
        )
        self.issue = normalize(self.raw)

    def test_returns_normalized_issue(self):
        assert isinstance(self.issue, NormalizedIssue)

    def test_identity_fields(self):
        assert self.issue.source == "redhat-kb"
        assert self.issue.source_id == "7045678"

    def test_title(self):
        assert "stuck PG" in self.issue.title

    def test_description_from_body(self):
        assert "OSD failure" in self.issue.description or "PG" in self.issue.description

    def test_status(self):
        assert self.issue.status == "published"

    def test_components_from_tags(self):
        assert "osd" in self.issue.components or "pg" in self.issue.components

    def test_labels_from_tags(self):
        assert "ceph" in self.issue.labels

    def test_affected_versions(self):
        assert "7" in self.issue.affected_versions

    def test_commands_extracted(self):
        assert any("ceph" in cmd for cmd in self.issue.commands_mentioned)

    def test_html_stripped(self):
        assert "<h2>" not in self.issue.description
        assert "<p>" not in self.issue.description


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestNormalizerEdgeCases:
    def test_unknown_source_raises(self):
        raw = RawIssue(source="unknown-source", source_id="1", source_url="", data={})
        with pytest.raises(ValueError, match="No normalizer"):
            normalize(raw)

    def test_source_with_known_substring(self):
        """A source name containing 'redmine' should dispatch to Redmine normalizer."""
        fixture = _load("redmine_issue.json")
        raw = RawIssue(
            source="my-redmine-instance",
            source_id="68051",
            source_url="https://example.com/issues/68051",
            data=fixture["issue"],
        )
        issue = normalize(raw)
        assert isinstance(issue, NormalizedIssue)
        assert issue.source == "my-redmine-instance"

    def test_empty_description(self):
        raw = RawIssue(
            source="ceph-tracker",
            source_id="1",
            source_url="https://tracker.ceph.com/issues/1",
            data={"subject": "Test", "description": "", "journals": [], "relations": []},
        )
        issue = normalize(raw)
        assert issue.title == "Test"
        assert issue.description == ""
        assert issue.summary == ""

    def test_missing_optional_fields(self):
        raw = RawIssue(
            source="ceph-tracker",
            source_id="2",
            source_url="https://tracker.ceph.com/issues/2",
            data={"subject": "Minimal", "journals": [], "relations": []},
        )
        issue = normalize(raw)
        assert issue.title == "Minimal"
        assert issue.status == ""
        assert issue.comments == []
