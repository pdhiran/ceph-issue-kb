"""Mock workflow tests validating search recall improvements.

Simulates the IBMCEPH-16989 scenario: a Jira issue with error text inside
ADF code blocks that must be surfaced by keyword, health warning, error
message, and direct lookup queries.
"""

from __future__ import annotations

import pytest

from ceph_issue_kb.indexer.normalizer import (
    _adf_to_text,
    _jira_field_to_text,
    normalize,
)
from ceph_issue_kb.models import (
    Comment,
    NormalizedIssue,
    RawIssue,
    SearchResult,
    make_entity_id,
)
from ceph_issue_kb.search.engine import SearchEngine, _bm25_doc_text
from ceph_issue_kb.server.kb import KnowledgeBase
from ceph_issue_kb.signal_extractor import extract_signals


# ---------------------------------------------------------------------------
# ADF fixtures — simulate Jira API v3 response for IBMCEPH-16989
# ---------------------------------------------------------------------------

ADF_DESCRIPTION = {
    "version": 1,
    "type": "doc",
    "content": [
        {
            "type": "paragraph",
            "content": [
                {
                    "type": "text",
                    "text": "During upgrade from 9.1z1 to 9.2, the standby MGR daemon fails to start because it cannot parse its keyring.",
                }
            ],
        },
        {
            "type": "heading",
            "attrs": {"level": 3},
            "content": [{"type": "text", "text": "Error logs"}],
        },
        {
            "type": "codeBlock",
            "attrs": {"language": ""},
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Aug 03 11:49:35 node2 ceph-mgr[100281]: auth: error parsing file "
                        "/var/lib/ceph/mgr/ceph-node2.xcbjdc/keyring: error setting modifier for "
                        "[mgr.node2.xcbjdc] type=key val=AgBSWXBqEimNBSAA...: Malformed input [buffer:3]\n"
                        "Aug 03 11:49:35 node2 ceph-mgr[100281]: auth: failed to load "
                        "/var/lib/ceph/mgr/ceph-node2.xcbjdc/keyring: (5) Input/output error\n"
                        "Aug 03 11:49:35 node2 ceph-mgr[100281]: monclient: keyring not found"
                    ),
                }
            ],
        },
        {
            "type": "paragraph",
            "content": [
                {
                    "type": "text",
                    "text": "The upgrade halts with UPGRADE_NO_STANDBY_MGR because no standby MGR is available.",
                }
            ],
        },
    ],
}

ADF_COMMENT = {
    "version": 1,
    "type": "doc",
    "content": [
        {
            "type": "paragraph",
            "content": [
                {
                    "type": "text",
                    "text": (
                        "Root cause: 9.2 does not have the cephx changes from 9.1z1. "
                        "The keyring was created with aes256k cipher but the 9.2 binary "
                        "expects the old AES cipher. Fix is to cherry-pick the cephx commits."
                    ),
                }
            ],
        }
    ],
}

JIRA_ISSUE_16989 = {
    "key": "IBMCEPH-16989",
    "fields": {
        "summary": "9.1 to 9.2 upgrade fails: MGR keyring Malformed input",
        "description": ADF_DESCRIPTION,
        "status": {"name": "Open"},
        "priority": {"name": "Critical"},
        "resolution": None,
        "issuetype": {"name": "Bug"},
        "project": {"key": "IBMCEPH"},
        "reporter": {"displayName": "Saraut Tester"},
        "assignee": {"displayName": "Adam King"},
        "created": "2026-07-28T10:00:00.000+0000",
        "updated": "2026-08-01T14:30:00.000+0000",
        "components": [{"name": "MGR"}, {"name": "cephadm"}],
        "labels": ["upgrade", "keyring", "cephx"],
        "fixVersions": [],
        "versions": [{"name": "20.2.1"}],
        "comment": {
            "comments": [
                {
                    "id": "90001",
                    "author": {"displayName": "Adam King"},
                    "body": ADF_COMMENT,
                    "created": "2026-07-29T09:15:00.000+0000",
                },
            ],
            "total": 1,
        },
        "issuelinks": [],
    },
}


# ---------------------------------------------------------------------------
# Test: ADF parser
# ---------------------------------------------------------------------------


class TestAdfParser:
    def test_plain_string_passthrough(self):
        assert _adf_to_text("hello world") == "hello world"

    def test_none_returns_empty(self):
        assert _adf_to_text(None) == ""

    def test_text_node(self):
        assert _adf_to_text({"type": "text", "text": "foo"}) == "foo"

    def test_paragraph(self):
        node = {
            "type": "paragraph",
            "content": [{"type": "text", "text": "line one"}],
        }
        result = _adf_to_text(node)
        assert "line one" in result

    def test_code_block_preserves_text(self):
        node = {
            "type": "codeBlock",
            "content": [{"type": "text", "text": "error log line here"}],
        }
        result = _adf_to_text(node)
        assert "error log line here" in result

    def test_nested_doc(self):
        result = _adf_to_text(ADF_DESCRIPTION)
        assert "Malformed input" in result
        assert "UPGRADE_NO_STANDBY_MGR" in result
        assert "keyring" in result
        assert "failed to load" in result

    def test_jira_field_to_text_dict(self):
        result = _jira_field_to_text(ADF_DESCRIPTION)
        assert "Malformed input" in result
        assert len(result) > 50

    def test_jira_field_to_text_string(self):
        assert _jira_field_to_text("plain text") == "plain text"

    def test_jira_field_to_text_none(self):
        assert _jira_field_to_text(None) == ""


# ---------------------------------------------------------------------------
# Test: Jira normalizer with ADF
# ---------------------------------------------------------------------------


class TestJiraNormalizerAdf:
    def setup_method(self):
        self.raw = RawIssue(
            source="ibm-jira",
            source_id="IBMCEPH-16989",
            source_url="https://ibm-ceph.atlassian.net/browse/IBMCEPH-16989",
            data=JIRA_ISSUE_16989,
        )
        self.issue = normalize(self.raw)

    def test_description_is_plain_text(self):
        assert "Malformed input" in self.issue.description
        assert "type" not in self.issue.description or "type=key" in self.issue.description
        assert "{" not in self.issue.description[:20]

    def test_comment_body_is_plain_text(self):
        assert len(self.issue.comments) == 1
        body = self.issue.comments[0].body
        assert "aes256k" in body
        assert "cherry-pick" in body

    def test_summary_is_clean(self):
        assert "Malformed" in self.issue.summary or "upgrade" in self.issue.summary.lower()

    def test_health_warnings_extracted(self):
        assert "UPGRADE_NO_STANDBY_MGR" in self.issue.health_warnings

    def test_error_messages_extracted(self):
        assert any("Malformed input" in e for e in self.issue.error_messages)

    def test_log_snippets_extracted(self):
        assert len(self.issue.log_snippets) > 0

    def test_components(self):
        assert "mgr" in self.issue.components
        assert "cephadm" in self.issue.components


# ---------------------------------------------------------------------------
# Test: Signal extraction on IBMCEPH-16989-like text
# ---------------------------------------------------------------------------


class TestSignalExtraction16989:
    def setup_method(self):
        self.text = _jira_field_to_text(ADF_DESCRIPTION)
        self.signals = extract_signals(self.text)

    def test_health_warning_upgrade(self):
        assert "UPGRADE_NO_STANDBY_MGR" in self.signals.health_warnings

    def test_error_message_malformed(self):
        assert any("Malformed input" in e for e in self.signals.error_messages)

    def test_error_message_failed_to_load(self):
        assert any("failed to load" in e for e in self.signals.error_messages)

    def test_error_message_keyring_not_found(self):
        assert any("keyring not found" in e for e in self.signals.error_messages)

    def test_log_snippets(self):
        assert any("Aug 03" in s for s in self.signals.log_snippets)


# ---------------------------------------------------------------------------
# Test: BM25 expanded corpus includes description and comments
# ---------------------------------------------------------------------------


class TestBm25Expansion:
    def setup_method(self):
        raw = RawIssue(
            source="ibm-jira",
            source_id="IBMCEPH-16989",
            source_url="https://ibm-ceph.atlassian.net/browse/IBMCEPH-16989",
            data=JIRA_ISSUE_16989,
        )
        self.issue = normalize(raw)

    def test_bm25_text_includes_description(self):
        text = _bm25_doc_text(self.issue)
        assert "Malformed input" in text

    def test_bm25_text_includes_comment(self):
        text = _bm25_doc_text(self.issue)
        assert "aes256k" in text

    def test_bm25_text_includes_health_warnings(self):
        text = _bm25_doc_text(self.issue)
        assert "UPGRADE_NO_STANDBY_MGR" in text

    def test_bm25_text_includes_error_messages(self):
        text = _bm25_doc_text(self.issue)
        assert "Malformed input" in text

    def test_bm25_text_includes_log_snippets(self):
        text = _bm25_doc_text(self.issue)
        assert "ceph-mgr" in text


# ---------------------------------------------------------------------------
# Test: End-to-end search recall (the actual IBMCEPH-16989 scenario)
# ---------------------------------------------------------------------------


def _build_test_engine() -> SearchEngine:
    """Build a SearchEngine with a mix of issues including IBMCEPH-16989."""
    raw_16989 = RawIssue(
        source="ibm-jira",
        source_id="IBMCEPH-16989",
        source_url="https://ibm-ceph.atlassian.net/browse/IBMCEPH-16989",
        data=JIRA_ISSUE_16989,
    )
    issue_16989 = normalize(raw_16989)

    other_issues = [
        NormalizedIssue(
            entity_id=make_entity_id("test", "other-1"),
            source="test",
            source_id="OTHER-1",
            source_url="https://example.com/OTHER-1",
            title="OSD crash during deep scrub with BlueStore",
            description="The OSD crashes with a segfault during deep-scrub operations.",
            summary="OSD crashes during deep-scrub",
            components=["osd", "bluestore"],
            status="open",
            health_warnings=["HEALTH_WARN", "OSD_DOWN"],
        ),
        NormalizedIssue(
            entity_id=make_entity_id("test", "other-2"),
            source="test",
            source_id="OTHER-2",
            source_url="https://example.com/OTHER-2",
            title="RGW bucket resharding fails with ENOENT",
            description="Dynamic resharding on multisite secondary zone fails.",
            summary="RGW resharding ENOENT",
            components=["rgw", "multisite"],
            status="open",
        ),
        NormalizedIssue(
            entity_id=make_entity_id("test", "other-3"),
            source="test",
            source_id="OTHER-3",
            source_url="https://example.com/OTHER-3",
            title="CephFS client eviction causes kernel hang",
            description="MDS evicts client, kernel mount hangs instead of returning EIO.",
            summary="CephFS eviction hang",
            components=["cephfs", "mds"],
            status="resolved",
        ),
    ]

    all_issues = [issue_16989] + other_issues
    return SearchEngine.from_issues(all_issues)


class TestSearchRecall:
    """Validate that the 5 query patterns from the plan all find IBMCEPH-16989."""

    def setup_method(self):
        self.engine = _build_test_engine()
        self.kb = KnowledgeBase(search_engine=self.engine)

    def test_query_malformed_input_keyword(self):
        """Query 1: BM25 keyword match on error text in description."""
        result = self.kb.search_issues("Malformed input buffer keyring mgr upgrade")
        assert result["total"] > 0
        source_ids = [r["source_id"] for r in result["results"]]
        assert "IBMCEPH-16989" in source_ids

    def test_query_error_message_is_known(self):
        """Query 2: is_known_issue with the exact error line."""
        result = self.kb.is_known_issue(
            "auth: error parsing file keyring Malformed input [buffer:3]"
        )
        assert result["known"] is True
        assert result["issue"]["source_id"] == "IBMCEPH-16989"

    def test_query_upgrade_health_warning(self):
        """Query 3: Search for the health warning code."""
        result = self.kb.search_issues("UPGRADE_NO_STANDBY_MGR")
        assert result["total"] > 0
        source_ids = [r["source_id"] for r in result["results"]]
        assert "IBMCEPH-16989" in source_ids

    def test_query_health_warning_tool(self):
        """Query 5: search_health_warning for upgrade warning."""
        result = self.kb.search_health_warning("UPGRADE_NO_STANDBY_MGR")
        assert result["total"] > 0
        source_ids = [r["source_id"] for r in result["results"]]
        assert "IBMCEPH-16989" in source_ids

    def test_query_direct_lookup(self):
        """Direct lookup by Jira key in query."""
        result = self.kb.search_issues("IBMCEPH-16989 upgrade failure")
        assert result["total"] > 0
        assert result["results"][0]["source_id"] == "IBMCEPH-16989"

    def test_get_issue_by_source_id(self):
        """get_issue by Jira key works."""
        result = self.kb.get_issue("IBMCEPH-16989")
        assert "error" not in result
        assert result["source_id"] == "IBMCEPH-16989"
        assert "Malformed input" in result["description"]

    def test_query_keyring_failed(self):
        """Additional: search for 'failed to load keyring' error pattern."""
        result = self.kb.search_issues("failed to load keyring Input/output error")
        assert result["total"] > 0
        source_ids = [r["source_id"] for r in result["results"]]
        assert "IBMCEPH-16989" in source_ids

    def test_query_aes256k_cipher(self):
        """Additional: search for cipher mismatch mentioned in comments."""
        result = self.kb.search_issues("aes256k cipher keyring mismatch")
        assert result["total"] > 0
        source_ids = [r["source_id"] for r in result["results"]]
        assert "IBMCEPH-16989" in source_ids


# ---------------------------------------------------------------------------
# Test: Embedder includes comments
# ---------------------------------------------------------------------------


class TestEmbedderInclusion:
    def test_embed_text_includes_comments(self):
        from ceph_issue_kb.indexer.embedder import issue_embed_text

        raw = RawIssue(
            source="ibm-jira",
            source_id="IBMCEPH-16989",
            source_url="https://ibm-ceph.atlassian.net/browse/IBMCEPH-16989",
            data=JIRA_ISSUE_16989,
        )
        issue = normalize(raw)
        text = issue_embed_text(issue)
        assert "aes256k" in text
        assert "Malformed input" in text


# ---------------------------------------------------------------------------
# Test: Backward compatibility — plain-text Jira descriptions still work
# ---------------------------------------------------------------------------


class TestPlainTextJiraCompat:
    """Ensure the existing plain-text fixture (API v2 style) still normalizes."""

    def test_existing_fixture_unchanged(self):
        import json
        from pathlib import Path

        fixture_path = Path(__file__).parent / "fixtures" / "jira_issue.json"
        data = json.loads(fixture_path.read_text())
        raw = RawIssue(
            source="ibm-jira",
            source_id=data["key"],
            source_url=f"https://ibm-ceph.atlassian.net/browse/{data['key']}",
            data=data,
        )
        issue = normalize(raw)
        assert "ENOENT" in issue.description
        assert issue.comments[0].author == "Casey Bodley"
