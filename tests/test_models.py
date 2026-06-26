"""Tests for data models."""

from ceph_issue_kb.models import Comment, NormalizedIssue, RawIssue, Relationship, make_entity_id


class TestMakeEntityId:
    def test_deterministic(self):
        id1 = make_entity_id("ceph-tracker", "68051")
        id2 = make_entity_id("ceph-tracker", "68051")
        assert id1 == id2

    def test_different_sources_differ(self):
        id1 = make_entity_id("ceph-tracker", "123")
        id2 = make_entity_id("ibm-jira", "123")
        assert id1 != id2

    def test_length(self):
        eid = make_entity_id("ceph-tracker", "68051")
        assert len(eid) == 16


class TestNormalizedIssue:
    def test_defaults(self):
        issue = NormalizedIssue(
            entity_id="abc123",
            source="ceph-tracker",
            source_id="68051",
            source_url="https://tracker.ceph.com/issues/68051",
            title="Test issue",
        )
        assert issue.entity_type == "issue"
        assert issue.schema_version == "1.0"
        assert issue.knowledge_base == "ceph-issue-kb"
        assert issue.indexed_at
        assert issue.components == []
        assert issue.stacktraces == []

    def test_with_signals(self):
        issue = NormalizedIssue(
            entity_id="abc123",
            source="ceph-tracker",
            source_id="68051",
            source_url="https://tracker.ceph.com/issues/68051",
            title="OSD crash",
            stacktraces=["#0 0x00 in ceph_abort()"],
            health_warnings=["HEALTH_WARN"],
            components=["bluestore"],
        )
        assert len(issue.stacktraces) == 1
        assert issue.health_warnings == ["HEALTH_WARN"]


class TestRawIssue:
    def test_basic(self):
        raw = RawIssue(
            source="ceph-tracker",
            source_id="68051",
            source_url="https://tracker.ceph.com/issues/68051",
            data={"subject": "test"},
        )
        assert raw.data["subject"] == "test"


class TestComment:
    def test_basic(self):
        c = Comment(author="user", body="text", created_at="2024-01-01")
        assert c.author == "user"


class TestRelationship:
    def test_basic(self):
        r = Relationship(relation_type="duplicate", target_source="ceph-tracker", target_id="12345")
        assert r.relation_type == "duplicate"
        assert r.target_url == ""
