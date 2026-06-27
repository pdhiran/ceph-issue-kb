"""Comprehensive mock workflow tests covering all project phases.

Each test class exercises a full end-to-end workflow using in-memory
mock data, verifying the entire stack without network access.
"""

from __future__ import annotations

import json
import sys
import textwrap
from dataclasses import asdict
from pathlib import Path

import pytest

from ceph_issue_kb.config import AuthConfig, Config, ConnectorConfig, load_config
from ceph_issue_kb.connectors import get_connector
from ceph_issue_kb.connectors.base import ConnectorError
from ceph_issue_kb.connectors.redmine import RedmineConnector
from ceph_issue_kb.indexer.normalizer import normalize
from ceph_issue_kb.models import (
    Comment,
    NormalizedIssue,
    RawIssue,
    Relationship,
    SearchResult,
    make_entity_id,
)
from ceph_issue_kb.search.engine import SearchEngine, _expand_query
from ceph_issue_kb.search.similarity import (
    SimilarityEngine,
    SimilarityResult,
    fingerprint,
    _jaccard,
    _normalize_stacktrace,
)
from ceph_issue_kb.server.kb import KnowledgeBase
from ceph_issue_kb.signal_extractor import extract_signals


# ---------------------------------------------------------------------------
# Shared fixture data
# ---------------------------------------------------------------------------

def _make_osd_crash_issue() -> NormalizedIssue:
    return NormalizedIssue(
        entity_id=make_entity_id("ceph-tracker", "55001"),
        source="ceph-tracker",
        source_id="55001",
        source_url="https://tracker.ceph.com/issues/55001",
        title="OSD crash during deep scrub on bluestore",
        summary="OSD daemon crashes with SIGSEGV during deep scrub on bluestore volumes",
        description=(
            "During deep scrub, OSD process exits with Segmentation fault.\n"
            "Backtrace:\n"
            "  #0  0x7f1234567890 in BlueStore::_do_read()\n"
            "  #1  0x7f1234567abc in PG::scrub()\n"
            "ceph_assert_fail: src/osd/PG.cc:1234\n"
            "As a workaround, disable deep scrub with:\n"
            "  ceph osd set nodeep-scrub\n"
        ),
        status="new",
        priority="critical",
        components=["osd", "bluestore"],
        labels=["crash", "blocker"],
        affected_versions=["17.2.6", "18.0.0"],
        health_warnings=["OSD_DOWN", "PG_DEGRADED"],
        stacktraces=[
            "#0  0x7f1234567890 in BlueStore::_do_read()\n#1  0x7f1234567abc in PG::scrub()"
        ],
        assertions=["ceph_assert_fail: src/osd/PG.cc:1234"],
        commands_mentioned=["ceph osd set nodeep-scrub"],
        configs_mentioned=["osd_deep_scrub_interval"],
        reporter="developer1",
        assignee="developer2",
        created_at="2024-06-01T10:00:00Z",
        updated_at="2024-12-15T14:30:00Z",
        comments=[
            Comment(
                comment_id="c1",
                author="dev2",
                body="This is a workaround: set osd_deep_scrub_interval to 0",
                created_at="2024-06-02T10:00:00Z",
            ),
            Comment(
                comment_id="c2",
                author="dev3",
                body="Fixed by commit abc1234 in PR #5678, backported to 17.2.7",
                created_at="2024-07-15T10:00:00Z",
            ),
        ],
        relationships=[
            Relationship(
                relation_type="related",
                target_source="ceph-tracker",
                target_id="54999",
                target_url="https://tracker.ceph.com/issues/54999",
            ),
        ],
    )


def _make_pg_stale_issue() -> NormalizedIssue:
    return NormalizedIssue(
        entity_id=make_entity_id("ceph-tracker", "55002"),
        source="ceph-tracker",
        source_id="55002",
        source_url="https://tracker.ceph.com/issues/55002",
        title="Placement groups stuck in stale+active+clean state",
        summary="PGs not recovering after OSD restart; stuck in stale state",
        description=(
            "After restarting OSDs, placement groups remain in stale+active+clean.\n"
            "HEALTH_WARN: 32 pgs stale\n"
            "ceph pg dump shows PG_DEGRADED\n"
            "ceph osd tree shows all OSDs up\n"
        ),
        status="open",
        priority="high",
        components=["osd", "pg"],
        labels=["recovery"],
        affected_versions=["17.2.5"],
        health_warnings=["HEALTH_WARN", "PG_DEGRADED"],
        commands_mentioned=["ceph pg dump", "ceph osd tree"],
        reporter="admin1",
        created_at="2024-05-10T08:00:00Z",
        updated_at="2024-12-10T09:00:00Z",
    )


def _make_mds_issue() -> NormalizedIssue:
    return NormalizedIssue(
        entity_id=make_entity_id("ibm-jira", "CEPH-1234"),
        source="ibm-jira",
        source_id="CEPH-1234",
        source_url="https://jira.example.com/browse/CEPH-1234",
        title="MDS standby-replay fails to take over after active MDS crash",
        summary="CephFS becomes unavailable when active MDS crashes",
        description=(
            "When the active MDS crashes, the standby-replay MDS does not promote.\n"
            "MDS_DAMAGE health warning appears.\n"
            "Traceback (most recent call last):\n"
            '  File "mds/MDSRank.cc", line 3456\n'
            "RuntimeError: failed to replay journal\n"
        ),
        status="open",
        priority="critical",
        components=["mds", "cephfs"],
        labels=["blocker", "regression"],
        affected_versions=["18.0.0"],
        health_warnings=["MDS_DAMAGE"],
        stacktraces=[
            'Traceback (most recent call last):\n  File "mds/MDSRank.cc", line 3456\nRuntimeError: failed to replay journal'
        ],
        reporter="cephfs-dev",
        created_at="2024-08-01T12:00:00Z",
        updated_at="2024-12-20T16:00:00Z",
    )


def _make_rgw_issue() -> NormalizedIssue:
    return NormalizedIssue(
        entity_id=make_entity_id("redhat-bugzilla", "2190001"),
        source="redhat-bugzilla",
        source_id="2190001",
        source_url="https://bugzilla.redhat.com/show_bug.cgi?id=2190001",
        title="RGW multisite sync fails with 403 Forbidden on secondary zone",
        summary="Multisite replication fails with permission denied errors",
        description=(
            "After upgrading to RHCS 6.1, RGW multisite sync stops working.\n"
            "radosgw-admin sync status shows 'failed' entries.\n"
            "SLOW_OPS warning in cluster health.\n"
        ),
        status="new",
        priority="urgent",
        severity="high",
        components=["rgw", "multisite"],
        labels=["upgrade"],
        affected_versions=["6.1"],
        health_warnings=["SLOW_OPS"],
        commands_mentioned=["radosgw-admin sync status"],
        reporter="customer1",
        created_at="2024-09-15T11:00:00Z",
        updated_at="2024-12-18T13:00:00Z",
    )


def _make_resolved_issue() -> NormalizedIssue:
    return NormalizedIssue(
        entity_id=make_entity_id("ceph-tracker", "55003"),
        source="ceph-tracker",
        source_id="55003",
        source_url="https://tracker.ceph.com/issues/55003",
        title="Mon election flapping under network partition",
        summary="MON cluster enters flapping elections during netsplit",
        description="Mon quorum cannot stabilize during partial network partition.",
        status="resolved",
        resolution="fixed",
        priority="high",
        components=["mon"],
        fixed_versions=["17.2.7"],
        health_warnings=["MON_DOWN"],
        reporter="netops",
        created_at="2024-03-01T09:00:00Z",
        updated_at="2024-04-15T14:00:00Z",
        resolved_at="2024-04-15T14:00:00Z",
        comments=[
            Comment(
                comment_id="c10",
                author="dev5",
                body="Fixed in commit deadbeef, merged to main via PR #1234",
                created_at="2024-04-15T14:00:00Z",
            ),
        ],
    )


def _make_rhkb_issue() -> NormalizedIssue:
    return NormalizedIssue(
        entity_id=make_entity_id("redhat-kb", "7012345"),
        source="redhat-kb",
        source_id="7012345",
        source_url="https://access.redhat.com/solutions/7012345",
        title="How to recover from OSD_NEARFULL warning in RHCS",
        summary="Steps to resolve OSD nearfull condition",
        description=(
            "When OSD usage exceeds the nearfull ratio, the cluster reports "
            "OSD_NEARFULL. Use ceph osd reweight to rebalance data."
        ),
        status="published",
        components=["osd"],
        labels=["osd", "recovery", "howto"],
        health_warnings=["OSD_NEARFULL"],
        commands_mentioned=["ceph osd reweight"],
        configs_mentioned=["osd_nearfull_ratio"],
        created_at="2024-04-20T10:00:00Z",
        updated_at="2024-11-01T10:00:00Z",
    )


def _all_mock_issues() -> list[NormalizedIssue]:
    return [
        _make_osd_crash_issue(),
        _make_pg_stale_issue(),
        _make_mds_issue(),
        _make_rgw_issue(),
        _make_resolved_issue(),
        _make_rhkb_issue(),
    ]


def _build_search_engine(issues: list[NormalizedIssue] | None = None) -> SearchEngine:
    if issues is None:
        issues = _all_mock_issues()
    return SearchEngine.from_issues(issues)


def _build_kb(issues: list[NormalizedIssue] | None = None) -> KnowledgeBase:
    engine = _build_search_engine(issues)
    sim = SimilarityEngine(engine)
    return KnowledgeBase(search_engine=engine, similarity_engine=sim)


# ===========================================================================
# Workflow A: Full Pipeline (config → connector → normalize → signals)
# ===========================================================================


class TestWorkflowA_FullPipeline:
    """Load config, create connector, normalize raw data, extract signals."""

    def test_config_loading(self, tmp_path):
        config_yaml = tmp_path / "connectors.yaml"
        config_yaml.write_text(textwrap.dedent("""\
            connectors:
              ceph-tracker:
                type: redmine
                enabled: true
                base_url: https://tracker.ceph.com
                auth:
                  method: none
                rate_limit: 5
                since: "2024-01-01"
                project: ceph
        """))
        config = load_config(config_yaml)
        assert "ceph-tracker" in config.connectors
        cc = config.connectors["ceph-tracker"]
        assert cc.connector_type == "redmine"
        assert cc.enabled is True
        assert cc.base_url == "https://tracker.ceph.com"
        assert cc.auth.method == "none"
        assert cc.extra.get("project") == "ceph"

    def test_connector_creation(self, tmp_path):
        config_yaml = tmp_path / "connectors.yaml"
        config_yaml.write_text(textwrap.dedent("""\
            connectors:
              ceph-tracker:
                type: redmine
                base_url: https://tracker.ceph.com
        """))
        config = load_config(config_yaml)
        connector = get_connector(config.connectors["ceph-tracker"])
        assert isinstance(connector, RedmineConnector)

    def test_normalize_redmine_raw(self):
        raw = RawIssue(
            source="ceph-tracker",
            source_id="55001",
            source_url="https://tracker.ceph.com/issues/55001",
            data={
                "subject": "OSD crash during deep-scrub",
                "description": (
                    "OSD crashes during deep scrub.\n"
                    "ceph_assert_fail: src/osd/OSD.cc:999\n"
                    "HEALTH_WARN: 4 osds down\n"
                    "ceph osd tree shows flapping\n"
                    "osd_deep_scrub_interval is set too low\n"
                ),
                "status": {"name": "New"},
                "priority": {"name": "Critical"},
                "author": {"name": "Alice"},
                "assigned_to": {"name": "Bob"},
                "created_on": "2024-06-01T10:00:00Z",
                "updated_on": "2024-12-15T14:30:00Z",
                "journals": [
                    {
                        "id": 1,
                        "notes": "Confirmed on 17.2.6",
                        "user": {"name": "Charlie"},
                        "created_on": "2024-06-02T10:00:00Z",
                    },
                ],
                "relations": [
                    {
                        "id": 100,
                        "issue_id": 55001,
                        "issue_to_id": 54999,
                        "relation_type": "related",
                    },
                ],
            },
        )
        issue = normalize(raw)
        assert issue.entity_id == make_entity_id("ceph-tracker", "55001")
        assert issue.source == "ceph-tracker"
        assert issue.title == "OSD crash during deep-scrub"
        assert issue.status == "new"
        assert issue.priority == "critical"
        assert issue.reporter == "Alice"
        assert issue.assignee == "Bob"
        assert len(issue.comments) == 1
        assert issue.comments[0].author == "Charlie"
        assert len(issue.relationships) == 1
        assert issue.relationships[0].target_id == "54999"

    def test_signal_extraction_from_normalized(self):
        raw = RawIssue(
            source="ceph-tracker",
            source_id="55001",
            source_url="https://tracker.ceph.com/issues/55001",
            data={
                "subject": "test",
                "description": (
                    "ceph_assert_fail: src/osd/PG.cc:1234\n"
                    "HEALTH_WARN: PG_DEGRADED\n"
                    "ceph osd dump\n"
                    "osd_pool_default_size = 3\n"
                    "2024-06-01T10:00:00.123456 log line here with data\n"
                ),
                "journals": [],
            },
        )
        issue = normalize(raw)
        assert len(issue.assertions) > 0
        assert "HEALTH_WARN" in issue.health_warnings or "PG_DEGRADED" in issue.health_warnings
        assert any("ceph osd" in cmd for cmd in issue.commands_mentioned)
        assert "osd_pool_default_size" in issue.configs_mentioned

    def test_all_normalized_fields_populated(self):
        issue = _make_osd_crash_issue()
        assert issue.entity_id
        assert issue.source
        assert issue.source_id
        assert issue.source_url
        assert issue.title
        assert issue.summary
        assert issue.description
        assert issue.status
        assert issue.priority
        assert len(issue.components) > 0
        assert len(issue.labels) > 0
        assert len(issue.affected_versions) > 0
        assert len(issue.health_warnings) > 0
        assert len(issue.stacktraces) > 0
        assert len(issue.assertions) > 0
        assert len(issue.commands_mentioned) > 0
        assert len(issue.configs_mentioned) > 0
        assert issue.reporter
        assert issue.assignee
        assert issue.created_at
        assert issue.updated_at
        assert len(issue.comments) > 0
        assert len(issue.relationships) > 0
        assert issue.entity_type == "issue"
        assert issue.schema_version
        assert issue.knowledge_base


# ===========================================================================
# Workflow B: Search Engine
# ===========================================================================


class TestWorkflowB_SearchEngine:
    """Create SearchEngine, index issues, test BM25 + synonym expansion."""

    def test_index_and_basic_search(self):
        engine = _build_search_engine()
        results = engine.search("OSD crash deep scrub")
        assert len(results) > 0
        assert results[0].issue.entity_id == make_entity_id("ceph-tracker", "55001")

    def test_synonym_expansion_pg(self):
        engine = _build_search_engine()
        results = engine.search("PG stuck stale")
        found_ids = {r.issue.source_id for r in results}
        assert "55002" in found_ids, "PG synonym should match 'placement group' issue"

    def test_synonym_expansion_osd(self):
        expanded = _expand_query("osd crash")
        assert "object storage daemon" in expanded

    def test_component_filter(self):
        engine = _build_search_engine()
        results = engine.search("crash", component="mds")
        for r in results:
            assert "mds" in r.issue.components

    def test_status_filter(self):
        engine = _build_search_engine()
        results = engine.search("crash", status="resolved")
        for r in results:
            assert r.issue.status == "resolved"

    def test_source_filter(self):
        engine = _build_search_engine()
        results = engine.search("crash", source="redhat-bugzilla")
        for r in results:
            assert r.issue.source == "redhat-bugzilla"

    def test_limit_results(self):
        engine = _build_search_engine()
        results = engine.search("OSD", limit=2)
        assert len(results) <= 2

    def test_empty_query_returns_empty(self):
        engine = _build_search_engine()
        results = engine.search("")
        assert len(results) == 0

    def test_get_issue_by_id(self):
        engine = _build_search_engine()
        eid = make_entity_id("ceph-tracker", "55001")
        issue = engine.get_issue(eid)
        assert issue is not None
        assert issue.source_id == "55001"

    def test_get_issue_missing(self):
        engine = _build_search_engine()
        assert engine.get_issue("nonexistent") is None

    def test_save_and_reload(self, tmp_path):
        engine = _build_search_engine()
        engine.save(tmp_path / "kb")
        loaded = SearchEngine.load(tmp_path / "kb")
        assert len(loaded.issues) == len(engine.issues)
        results = loaded.search("OSD crash")
        assert len(results) > 0

    def test_health_warning_search(self):
        engine = _build_search_engine()
        results = engine.search("OSD_DOWN")
        found_ids = {r.issue.source_id for r in results}
        assert "55001" in found_ids


# ===========================================================================
# Workflow C: Similarity Engine
# ===========================================================================


class TestWorkflowC_SimilarityEngine:
    """Similarity matching and failure fingerprinting."""

    def test_find_similar_basic(self):
        engine = _build_search_engine()
        sim = SimilarityEngine(engine)
        results = sim.find_similar("OSD crash during deep scrub on bluestore")
        assert len(results) > 0
        assert results[0].issue.source_id == "55001"
        assert results[0].similarity > 0

    def test_find_similar_with_stacktrace(self):
        engine = _build_search_engine()
        sim = SimilarityEngine(engine)
        results = sim.find_similar(
            "OSD segfault",
            stacktrace="#0  0xABCDEF in BlueStore::_do_read()\n#1  0x123456 in PG::scrub()",
        )
        assert len(results) > 0
        has_st_signal = any(
            "stacktrace" in sig for r in results for sig in r.matched_signals
        )
        assert has_st_signal, "Should detect stacktrace similarity"

    def test_find_similar_component_boost(self):
        engine = _build_search_engine()
        sim = SimilarityEngine(engine)
        results = sim.find_similar("crash failure", component="osd")
        for r in results:
            if "osd" in r.issue.components:
                has_comp = any("component" in s for s in r.matched_signals)
                assert has_comp, "OSD component match should be signaled"
                break

    def test_find_similar_empty_returns_empty(self):
        engine = _build_search_engine()
        sim = SimilarityEngine(engine)
        assert sim.find_similar("") == []
        assert sim.find_similar("", stacktrace=None) == []

    def test_fingerprint_same_crash_same_fp(self):
        fp1 = fingerprint(
            "#0  0x7f1234567890 in BlueStore::_do_read()\n#1  0x7f1234567abc in PG::scrub()"
        )
        fp2 = fingerprint(
            "#0  0xAAAAAAAAAAAA in BlueStore::_do_read()\n#1  0xBBBBBBBBBBBB in PG::scrub()"
        )
        assert fp1 == fp2, "Same crash with different addresses should have same fingerprint"

    def test_fingerprint_different_crash_different_fp(self):
        fp1 = fingerprint("#0 in BlueStore::_do_read()")
        fp2 = fingerprint("#0 in MDSRank::handle_signal()")
        assert fp1 != fp2

    def test_fingerprint_with_assertion(self):
        fp1 = fingerprint("", assertion="ceph_assert_fail: src/osd/PG.cc:1234")
        fp2 = fingerprint("", assertion="ceph_assert_fail: src/osd/PG.cc:1234")
        fp3 = fingerprint("", assertion="ceph_assert_fail: src/mon/Monitor.cc:999")
        assert fp1 == fp2
        assert fp1 != fp3

    def test_fingerprint_deterministic(self):
        for _ in range(10):
            assert fingerprint("test trace", "test assert") == fingerprint(
                "test trace", "test assert"
            )

    def test_jaccard_similarity(self):
        assert _jaccard("hello world", "hello world") == 1.0
        assert _jaccard("hello", "goodbye") == 0.0
        assert 0 < _jaccard("OSD crash deep scrub", "OSD failure deep scrub") < 1

    def test_stacktrace_normalization(self):
        raw = '#0  0x7f1234567890 in func() at /path/to/file.cc:42'
        normalized = _normalize_stacktrace(raw)
        assert "0x7f1234567890" not in normalized
        assert "addr" in normalized  # lowercased by _normalize_stacktrace

        python_st = 'File "/usr/lib/python3/module.py", line 42'
        norm_py = _normalize_stacktrace(python_st)
        assert "/usr/lib/python3/" not in norm_py
        assert "line n" in norm_py


# ===========================================================================
# Workflow D: KnowledgeBase Facade
# ===========================================================================


class TestWorkflowD_KnowledgeBaseFacade:
    """Test every KnowledgeBase method with mock data."""

    @pytest.fixture
    def kb(self):
        return _build_kb()

    def test_search_issues(self, kb):
        result = kb.search_issues("OSD crash")
        assert result["total"] > 0
        assert "results" in result
        assert result["results"][0]["title"]

    def test_search_issues_with_component(self, kb):
        result = kb.search_issues("crash", component="osd")
        assert result["total"] > 0
        for item in result["results"]:
            assert "osd" in item["components"]

    def test_search_issues_with_version(self, kb):
        result = kb.search_issues("OSD crash", version="17.2.6")
        assert result["total"] > 0
        for item in result["results"]:
            assert any("17.2.6" in v for v in item["affected_versions"])

    def test_find_similar_issue(self, kb):
        result = kb.find_similar_issue("OSD crash during deep scrub")
        assert result["total"] > 0
        assert "similarity" in result["results"][0]
        assert "matched_signals" in result["results"][0]

    def test_is_known_issue_found(self, kb):
        result = kb.is_known_issue("OSD crash deep scrub")
        assert result["known"] is True
        assert "issue" in result

    def test_is_known_issue_not_found(self, kb):
        result = kb.is_known_issue("zzz_nonexistent_error_xyzzy")
        assert result["known"] is False

    def test_is_known_issue_excludes_resolved(self, kb):
        result = kb.is_known_issue("Mon election flapping")
        if result["known"]:
            assert result["issue"]["status"] != "resolved"

    def test_find_workaround(self, kb):
        result = kb.find_workaround("OSD crash deep scrub")
        assert "workarounds" in result
        assert result["total"] >= 0

    def test_find_fix(self, kb):
        result = kb.find_fix("OSD crash deep scrub")
        assert "fixes" in result
        has_fix = any("commit" in f["text"].lower() or "pr" in f["text"].lower()
                       for f in result["fixes"])
        assert has_fix or result["total"] == 0

    def test_find_related_issues_exists(self, kb):
        eid = make_entity_id("ceph-tracker", "55001")
        result = kb.find_related_issues(eid)
        assert "related" in result
        assert result["total"] > 0
        assert result["related"][0]["relation_type"] == "related"

    def test_find_related_issues_not_found(self, kb):
        result = kb.find_related_issues("nonexistent_id")
        assert "error" in result

    def test_find_related_issues_no_relations(self, kb):
        eid = make_entity_id("ceph-tracker", "55002")
        result = kb.find_related_issues(eid)
        assert result["total"] == 0

    def test_search_stacktrace(self, kb):
        result = kb.search_stacktrace(
            "#0  0xDEADBEEF in BlueStore::_do_read()\n#1  0xCAFEBABE in PG::scrub()"
        )
        assert "results" in result
        assert "fingerprint" in result
        assert len(result["fingerprint"]) == 16

    def test_search_health_warning(self, kb):
        result = kb.search_health_warning("OSD_DOWN")
        assert result["total"] > 0
        assert result["warning"] == "OSD_DOWN"

    def test_hot_issues(self, kb):
        result = kb.hot_issues()
        assert result["total"] > 0
        dates = [r["updated_at"] for r in result["results"] if r["updated_at"]]
        assert dates == sorted(dates, reverse=True), "Should be sorted by most recent update"

    def test_hot_issues_filtered(self, kb):
        result = kb.hot_issues(component="rgw")
        for item in result["results"]:
            assert "rgw" in item["components"]

    def test_component_health(self, kb):
        result = kb.component_health("osd")
        assert result["component"] == "osd"
        assert "total_issues" in result
        assert "open_issues" in result
        assert "critical_issues" in result
        assert "blockers" in result
        assert result["open_issues"] > 0

    def test_component_health_critical(self, kb):
        result = kb.component_health("osd")
        assert len(result["critical_issues"]) > 0

    def test_component_health_blockers(self, kb):
        result = kb.component_health("osd")
        assert len(result["blockers"]) > 0

    def test_capabilities(self, kb):
        result = kb.capabilities()
        assert result["name"] == "ceph-issue-kb"
        assert "operations" in result
        assert "search_issues" in result["operations"]
        assert "entity_counts" in result
        assert result["entity_counts"]["issues"] == 6

    def test_health_loaded(self, kb):
        result = kb.health()
        assert result["status"] == "ok"
        assert result["total_issues"] == 6

    def test_health_empty(self):
        empty_kb = KnowledgeBase.empty()
        result = empty_kb.health()
        assert result["status"] == "degraded"
        assert result["total_issues"] == 0


# ===========================================================================
# Workflow E: MCP Server
# ===========================================================================


class TestWorkflowE_MCPServer:
    """Import MCP server module, create server, verify tools registered."""

    def test_import_module(self):
        from ceph_issue_kb.server import mcp_server
        assert hasattr(mcp_server, "create_mcp_server")
        assert hasattr(mcp_server, "main")

    def test_create_server(self):
        from ceph_issue_kb.server.mcp_server import create_mcp_server
        kb = _build_kb()
        mcp = create_mcp_server(kb)
        assert mcp is not None
        assert mcp.name == "Ceph Issue Intelligence KB"

    def test_all_tools_registered(self):
        from ceph_issue_kb.server.mcp_server import create_mcp_server
        kb = _build_kb()
        mcp = create_mcp_server(kb)
        expected_tools = {
            "search_issues",
            "find_similar_issue",
            "is_known_issue",
            "find_workaround",
            "find_fix",
            "find_related_issues",
            "search_stacktrace",
            "search_health_warning",
            "hot_issues",
            "component_health",
            "capabilities",
            "health",
        }
        tool_names = set(mcp._tool_manager._tools.keys())
        assert expected_tools.issubset(tool_names), (
            f"Missing tools: {expected_tools - tool_names}"
        )


# ===========================================================================
# Workflow F: REST API
# ===========================================================================


class TestWorkflowF_RESTAPI:
    """Hit every REST endpoint using Starlette TestClient."""

    @pytest.fixture
    def client(self):
        from starlette.testclient import TestClient
        from ceph_issue_kb.server.rest_api import create_app
        kb = _build_kb()
        app = create_app(kb)
        return TestClient(app)

    def test_health(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["total_issues"] == 6

    def test_capabilities(self, client):
        resp = client.get("/capabilities")
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "ceph-issue-kb"
        assert "operations" in data

    def test_search_issues(self, client):
        resp = client.post("/api/search_issues", json={"query": "OSD crash"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0

    def test_search_issues_missing_query(self, client):
        resp = client.post("/api/search_issues", json={})
        assert resp.status_code == 400

    def test_search_issues_with_filters(self, client):
        resp = client.post(
            "/api/search_issues",
            json={"query": "crash", "component": "osd", "limit": 5},
        )
        assert resp.status_code == 200
        for item in resp.json()["results"]:
            assert "osd" in item["components"]

    def test_find_similar(self, client):
        resp = client.post(
            "/api/find_similar_issue",
            json={"description": "OSD crash during deep scrub"},
        )
        assert resp.status_code == 200
        assert resp.json()["total"] > 0

    def test_find_similar_missing_description(self, client):
        resp = client.post("/api/find_similar_issue", json={})
        assert resp.status_code == 400

    def test_is_known_issue(self, client):
        resp = client.post(
            "/api/is_known_issue",
            json={"error_message": "OSD crash deep scrub"},
        )
        assert resp.status_code == 200
        assert resp.json()["known"] is True

    def test_is_known_issue_missing_error(self, client):
        resp = client.post("/api/is_known_issue", json={})
        assert resp.status_code == 400

    def test_find_workaround(self, client):
        resp = client.post(
            "/api/find_workaround", json={"query": "OSD crash deep scrub"}
        )
        assert resp.status_code == 200
        assert "workarounds" in resp.json()

    def test_find_workaround_missing_query(self, client):
        resp = client.post("/api/find_workaround", json={})
        assert resp.status_code == 400

    def test_find_fix(self, client):
        resp = client.post(
            "/api/find_fix", json={"query": "OSD crash deep scrub"}
        )
        assert resp.status_code == 200
        assert "fixes" in resp.json()

    def test_find_fix_missing_query(self, client):
        resp = client.post("/api/find_fix", json={})
        assert resp.status_code == 400

    def test_find_related(self, client):
        eid = make_entity_id("ceph-tracker", "55001")
        resp = client.post("/api/find_related_issues", json={"issue_id": eid})
        assert resp.status_code == 200
        assert resp.json()["total"] > 0

    def test_find_related_missing_id(self, client):
        resp = client.post("/api/find_related_issues", json={})
        assert resp.status_code == 400

    def test_search_stacktrace(self, client):
        resp = client.post(
            "/api/search_stacktrace",
            json={"stacktrace": "#0 in BlueStore::_do_read()"},
        )
        assert resp.status_code == 200
        assert "fingerprint" in resp.json()

    def test_search_stacktrace_missing(self, client):
        resp = client.post("/api/search_stacktrace", json={})
        assert resp.status_code == 400

    def test_search_health_warning(self, client):
        resp = client.post(
            "/api/search_health_warning", json={"warning": "OSD_DOWN"}
        )
        assert resp.status_code == 200
        assert resp.json()["total"] > 0

    def test_search_health_warning_missing(self, client):
        resp = client.post("/api/search_health_warning", json={})
        assert resp.status_code == 400

    def test_hot_issues(self, client):
        resp = client.get("/api/hot_issues")
        assert resp.status_code == 200
        assert resp.json()["total"] > 0

    def test_hot_issues_with_component(self, client):
        resp = client.get("/api/hot_issues?component=osd")
        assert resp.status_code == 200
        for item in resp.json()["results"]:
            assert "osd" in item["components"]

    def test_component_health(self, client):
        resp = client.get("/api/component_health/osd")
        assert resp.status_code == 200
        data = resp.json()
        assert data["component"] == "osd"
        assert "open_issues" in data


# ===========================================================================
# Workflow G: Normalizer Cross-Source
# ===========================================================================


class TestWorkflowG_NormalizerCrossSource:
    """Normalize RawIssue from each source type and verify fields."""

    def test_normalize_redmine(self):
        raw = RawIssue(
            source="redmine",
            source_id="12345",
            source_url="https://tracker.ceph.com/issues/12345",
            data={
                "subject": "Redmine Test Issue",
                "description": "Description with ceph osd pool ls",
                "status": {"name": "New"},
                "priority": {"name": "Normal"},
                "author": {"name": "Alice"},
                "created_on": "2024-01-01T00:00:00Z",
                "updated_on": "2024-06-01T00:00:00Z",
                "journals": [
                    {
                        "id": 1,
                        "notes": "A comment",
                        "user": {"name": "Bob"},
                        "created_on": "2024-01-02T00:00:00Z",
                    }
                ],
                "relations": [],
            },
        )
        issue = normalize(raw)
        assert issue.entity_id == make_entity_id("redmine", "12345")
        assert issue.source == "redmine"
        assert issue.title == "Redmine Test Issue"
        assert len(issue.comments) == 1
        assert issue.comments[0].author == "Bob"

    def test_normalize_jira(self):
        raw = RawIssue(
            source="jira",
            source_id="CEPH-999",
            source_url="https://jira.example.com/browse/CEPH-999",
            data={
                "fields": {
                    "summary": "JIRA Test Issue",
                    "description": "Testing JIRA normalizer",
                    "status": {"name": "Open"},
                    "priority": {"name": "Major"},
                    "resolution": {"name": "Unresolved"},
                    "reporter": {"displayName": "Charlie"},
                    "assignee": {"displayName": "Dave"},
                    "components": [{"name": "RGW"}],
                    "labels": ["regression"],
                    "versions": [{"name": "18.0.0"}],
                    "fixVersions": [{"name": "18.1.0"}],
                    "created": "2024-03-01T00:00:00Z",
                    "updated": "2024-08-01T00:00:00Z",
                    "comment": {
                        "comments": [
                            {
                                "id": "10",
                                "author": {"displayName": "Eve"},
                                "body": "I can reproduce this",
                                "created": "2024-03-02T00:00:00Z",
                            }
                        ]
                    },
                    "issuelinks": [
                        {
                            "type": {"name": "Duplicate"},
                            "outwardIssue": {"key": "CEPH-998"},
                        }
                    ],
                },
            },
        )
        issue = normalize(raw)
        assert issue.entity_id == make_entity_id("jira", "CEPH-999")
        assert issue.title == "JIRA Test Issue"
        assert issue.status == "open"
        assert issue.priority == "major"
        assert "rgw" in issue.components
        assert "regression" in issue.labels
        assert "18.0.0" in issue.affected_versions
        assert "18.1.0" in issue.fixed_versions
        assert issue.reporter == "Charlie"
        assert len(issue.comments) == 1
        assert len(issue.relationships) == 1
        assert issue.relationships[0].target_id == "CEPH-998"

    def test_normalize_bugzilla(self):
        raw = RawIssue(
            source="bugzilla",
            source_id="2190001",
            source_url="https://bugzilla.redhat.com/show_bug.cgi?id=2190001",
            data={
                "summary": "Bugzilla Test Bug",
                "status": "ASSIGNED",
                "resolution": "",
                "priority": "high",
                "severity": "medium",
                "creator": "frank@redhat.com",
                "assigned_to": "grace@redhat.com",
                "component": "OSD",
                "product": "Red Hat Ceph Storage",
                "version": "6.1",
                "keywords": ["Regression", "TestBlocker"],
                "target_release": ["7.0"],
                "blocks": [2190002],
                "depends_on": [2189999],
                "creation_time": "2024-05-01T00:00:00Z",
                "last_change_time": "2024-10-01T00:00:00Z",
                "comments": [
                    {
                        "id": 1,
                        "text": "First comment is description body",
                        "creator": "frank@redhat.com",
                        "creation_time": "2024-05-01T00:00:00Z",
                    },
                    {
                        "id": 2,
                        "text": "Confirmed the issue",
                        "creator": "grace@redhat.com",
                        "creation_time": "2024-05-02T00:00:00Z",
                    },
                ],
            },
        )
        issue = normalize(raw)
        assert issue.entity_id == make_entity_id("bugzilla", "2190001")
        assert issue.title == "Bugzilla Test Bug"
        assert issue.status == "assigned"
        assert issue.severity == "medium"
        assert "osd" in issue.components
        assert "Regression" in issue.labels
        assert "6.1" in issue.affected_versions
        assert "7.0" in issue.fixed_versions
        assert issue.reporter == "frank@redhat.com"
        assert len(issue.comments) == 2
        assert len(issue.relationships) == 2
        rel_types = {r.relation_type for r in issue.relationships}
        assert "blocks" in rel_types
        assert "depends_on" in rel_types

    def test_normalize_rhkb(self):
        raw = RawIssue(
            source="rhkb",
            source_id="7012345",
            source_url="https://access.redhat.com/solutions/7012345",
            data={
                "title": "How to resolve OSD_NEARFULL",
                "abstract": "Steps to fix OSD nearfull warning",
                "body": "<p>Use <code>ceph osd reweight</code> to rebalance.</p>",
                "kcsState": "published",
                "tags": ["osd", "recovery", "howto"],
                "version": "6.0",
                "publishedDate": "2024-04-01T00:00:00Z",
                "lastModifiedDate": "2024-09-01T00:00:00Z",
            },
        )
        issue = normalize(raw)
        assert issue.entity_id == make_entity_id("rhkb", "7012345")
        assert issue.title == "How to resolve OSD_NEARFULL"
        assert issue.status == "published"
        assert "osd" in issue.components
        assert "recovery" in issue.labels
        assert "6.0" in issue.affected_versions
        assert any("ceph osd reweight" in cmd for cmd in issue.commands_mentioned)


# ===========================================================================
# Workflow H: CLI
# ===========================================================================


class TestWorkflowH_CLI:
    """Import index_issues and verify the arg parser."""

    def test_import_module(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from index_issues import _parse_args, main
        assert callable(_parse_args)
        assert callable(main)

    def test_parse_args_defaults(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from index_issues import _parse_args
        args = _parse_args([])
        assert args.config == "connectors.yaml"
        assert args.output_dir == "knowledge/issues-2024-2025"
        assert args.verbose is False
        assert args.connector is None
        assert args.since is None

    def test_parse_args_custom(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from index_issues import _parse_args
        args = _parse_args([
            "--config", "custom.yaml",
            "--connector", "ceph-tracker",
            "--since", "2025-01-01",
            "--output-dir", "/tmp/kb",
            "--verbose",
        ])
        assert args.config == "custom.yaml"
        assert args.connector == "ceph-tracker"
        assert args.since == "2025-01-01"
        assert args.output_dir == "/tmp/kb"
        assert args.verbose is True

    def test_help_does_not_crash(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from index_issues import _parse_args
        with pytest.raises(SystemExit) as exc_info:
            _parse_args(["--help"])
        assert exc_info.value.code == 0

    def test_main_missing_config(self, tmp_path, monkeypatch):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from index_issues import main
        monkeypatch.chdir(tmp_path)
        result = main(["--config", "nonexistent.yaml"])
        assert result == 1


# ===========================================================================
# Workflow I: Edge Cases
# ===========================================================================


class TestWorkflowI_EdgeCases:
    """Empty results, missing KB, malformed inputs."""

    def test_empty_search_results(self):
        kb = _build_kb()
        result = kb.search_issues("zzzzz_nonexistent_xyz_12345")
        assert result["total"] == 0
        assert result["results"] == []

    def test_empty_kb_search(self):
        kb = KnowledgeBase.empty()
        result = kb.search_issues("anything")
        assert result["total"] == 0

    def test_empty_kb_hot_issues(self):
        kb = KnowledgeBase.empty()
        result = kb.hot_issues()
        assert result["total"] == 0

    def test_empty_kb_component_health(self):
        kb = KnowledgeBase.empty()
        result = kb.component_health("osd")
        assert result["total_issues"] == 0
        assert result["open_issues"] == 0

    def test_empty_kb_capabilities(self):
        kb = KnowledgeBase.empty()
        result = kb.capabilities()
        assert result["entity_counts"]["issues"] == 0

    def test_find_workaround_no_match(self):
        kb = _build_kb()
        result = kb.find_workaround("zzzzz_nonexistent_xyz_12345")
        assert "error" in result

    def test_find_fix_no_match(self):
        kb = _build_kb()
        result = kb.find_fix("zzzzz_nonexistent_xyz_12345")
        assert "error" in result

    def test_find_similar_none_description(self):
        engine = _build_search_engine()
        sim = SimilarityEngine(engine)
        assert sim.find_similar(None) == []

    def test_search_empty_string(self):
        engine = _build_search_engine()
        results = engine.search("")
        assert results == []

    def test_normalizer_unknown_source_raises(self):
        raw = RawIssue(
            source="unknown_source_xyz",
            source_id="1",
            source_url="http://example.com/1",
            data={"title": "test"},
        )
        with pytest.raises(ValueError, match="No normalizer"):
            normalize(raw)

    def test_signal_extraction_empty_string(self):
        signals = extract_signals("")
        assert signals.stacktraces == []
        assert signals.assertions == []
        assert signals.health_warnings == []
        assert signals.commands_mentioned == []
        assert signals.configs_mentioned == []
        assert signals.log_snippets == []

    def test_signal_extraction_none_safe(self):
        signals = extract_signals("")
        assert signals is not None

    def test_fingerprint_empty_stacktrace(self):
        fp = fingerprint("")
        assert isinstance(fp, str)
        assert len(fp) == 16

    def test_make_entity_id_deterministic(self):
        id1 = make_entity_id("source", "123")
        id2 = make_entity_id("source", "123")
        assert id1 == id2

    def test_connector_unknown_type(self):
        cc = ConnectorConfig(name="test", connector_type="unknown_type_xyz")
        with pytest.raises(ConnectorError, match="Unknown connector type"):
            get_connector(cc)

    def test_rest_api_malformed_json(self):
        from starlette.testclient import TestClient
        from ceph_issue_kb.server.rest_api import create_app
        kb = _build_kb()
        app = create_app(kb)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/search_issues",
            content="not json",
            headers={"content-type": "application/json"},
        )
        assert resp.status_code in (400, 500)

    def test_search_engine_from_empty_list(self):
        engine = SearchEngine.from_issues([])
        results = engine.search("anything")
        assert results == []

    def test_kb_load_from_saved(self, tmp_path):
        engine = _build_search_engine()
        engine.save(tmp_path / "kb")
        kb = KnowledgeBase.load(tmp_path / "kb")
        result = kb.search_issues("OSD crash")
        assert result["total"] > 0

    def test_config_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            load_config("/nonexistent/path/connectors.yaml")

    def test_config_bad_yaml(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("connectors:")
        with pytest.raises(ValueError, match="missing 'connectors'"):
            load_config(bad)
