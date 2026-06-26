#!/usr/bin/env python3
"""Workflow D: Model stress test.

Tests NormalizedIssue, Comment, Relationship with max/min fields,
entity_id uniqueness, bulk creation, and edge-case content.
"""
import sys
sys.path.insert(0, "src")

from ceph_issue_kb.models import (
    Comment, NormalizedIssue, RawIssue, Relationship,
    make_entity_id, SCHEMA_VERSION, KNOWLEDGE_BASE,
)

PASS = 0
FAIL = 0

def check(label, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


print("=" * 80)
print("WORKFLOW D: Model Stress Test")
print("=" * 80)

# ---------------------------------------------------------------------------
# 1. NormalizedIssue with ALL fields populated
# ---------------------------------------------------------------------------
print("\n--- Test 1: NormalizedIssue with maximum fields ---")
comments = [
    Comment(author=f"user{i}", body=f"Comment body {i}", created_at=f"2024-01-0{i+1}T00:00:00Z", comment_id=f"c{i}")
    for i in range(5)
]
relationships = [
    Relationship(relation_type="duplicate", target_source="jira", target_id="JIRA-100", target_url="https://jira.example.com/JIRA-100"),
    Relationship(relation_type="blocks", target_source="bugzilla", target_id="BZ-200"),
    Relationship(relation_type="related", target_source="ceph-tracker", target_id="67999", target_url="https://tracker.ceph.com/issues/67999"),
]
max_issue = NormalizedIssue(
    entity_id=make_entity_id("ceph-tracker", "99999"),
    source="ceph-tracker",
    source_id="99999",
    source_url="https://tracker.ceph.com/issues/99999",
    title="Fully populated test issue",
    summary="This is a comprehensive test of all fields.",
    description="Long description with lots of detail.\n" * 10,
    comments=comments,
    status="Resolved",
    resolution="Fixed",
    priority="Urgent",
    severity="Critical",
    components=["bluestore", "osd", "mon"],
    labels=["regression", "reef", "performance"],
    affected_versions=["18.2.0", "18.2.1", "18.2.2"],
    fixed_versions=["18.2.3"],
    release="reef",
    reporter="reporter_user",
    assignee="assignee_user",
    created_at="2024-01-01T00:00:00Z",
    updated_at="2024-06-15T12:00:00Z",
    resolved_at="2024-06-10T08:00:00Z",
    stacktraces=["#0 0x00 in ceph_abort()", "Traceback..."],
    assertions=["FAILED assertion: x > 0"],
    health_warnings=["HEALTH_WARN", "OSD_DOWN"],
    commands_mentioned=["ceph osd pool set testpool size 3"],
    configs_mentioned=["osd_deep_scrub_interval"],
    log_snippets=["2024-01-01T00:00:00 osd.5 shutting down"],
    relationships=relationships,
    keywords=["crash", "bluestore", "deep-scrub"],
    entity_type="issue",
    indexed_at="2024-06-20T00:00:00Z",
    schema_version=SCHEMA_VERSION,
    knowledge_base=KNOWLEDGE_BASE,
)
print(f"  entity_id: {max_issue.entity_id}")
print(f"  title: {max_issue.title}")
print(f"  comments: {len(max_issue.comments)}")
print(f"  relationships: {len(max_issue.relationships)}")
print(f"  components: {max_issue.components}")
print(f"  affected_versions: {max_issue.affected_versions}")
print(f"  stacktraces: {len(max_issue.stacktraces)}")
print(f"  schema_version: {max_issue.schema_version}")
check("All 5 comments stored", len(max_issue.comments) == 5)
check("All 3 relationships stored", len(max_issue.relationships) == 3)
check("3 components", len(max_issue.components) == 3)
check("3 affected_versions", len(max_issue.affected_versions) == 3)
check("resolved_at is set", max_issue.resolved_at == "2024-06-10T08:00:00Z")
check("indexed_at is custom (not auto)", max_issue.indexed_at == "2024-06-20T00:00:00Z")

# ---------------------------------------------------------------------------
# 2. NormalizedIssue with minimum fields (empty lists, None optionals)
# ---------------------------------------------------------------------------
print("\n--- Test 2: NormalizedIssue with minimum fields ---")
min_issue = NormalizedIssue(
    entity_id=make_entity_id("test", "1"),
    source="test",
    source_id="1",
    source_url="",
)
print(f"  entity_id: {min_issue.entity_id}")
print(f"  title: {min_issue.title!r}")
print(f"  comments: {min_issue.comments}")
print(f"  resolved_at: {min_issue.resolved_at}")
print(f"  indexed_at: {min_issue.indexed_at}")
check("Empty title is ''", min_issue.title == "")
check("Empty comments", min_issue.comments == [])
check("Empty stacktraces", min_issue.stacktraces == [])
check("resolved_at is None", min_issue.resolved_at is None)
check("indexed_at auto-generated", len(min_issue.indexed_at) > 0)
check("schema_version auto-set", min_issue.schema_version == SCHEMA_VERSION)
check("knowledge_base auto-set", min_issue.knowledge_base == KNOWLEDGE_BASE)

# ---------------------------------------------------------------------------
# 3. Entity ID uniqueness: same source+source_id = same entity_id
# ---------------------------------------------------------------------------
print("\n--- Test 3: Entity ID determinism ---")
id_a1 = make_entity_id("ceph-tracker", "68051")
id_a2 = make_entity_id("ceph-tracker", "68051")
id_b = make_entity_id("ceph-tracker", "68052")
id_c = make_entity_id("ibm-jira", "68051")
print(f"  ceph-tracker:68051 = {id_a1}")
print(f"  ceph-tracker:68051 = {id_a2}")
print(f"  ceph-tracker:68052 = {id_b}")
print(f"  ibm-jira:68051     = {id_c}")
check("Same source+id => same entity_id", id_a1 == id_a2)
check("Different id => different entity_id", id_a1 != id_b)
check("Different source => different entity_id", id_a1 != id_c)
check("entity_id is 16 chars hex", len(id_a1) == 16 and all(c in "0123456789abcdef" for c in id_a1))

# ---------------------------------------------------------------------------
# 4. Bulk creation: 100 issues, no entity_id collisions
# ---------------------------------------------------------------------------
print("\n--- Test 4: Bulk create 100 issues, check for collisions ---")
entity_ids = set()
for i in range(100):
    eid = make_entity_id("bulk-test", str(i))
    entity_ids.add(eid)
print(f"  Generated 100 entity_ids, unique count: {len(entity_ids)}")
check("100 unique entity_ids (no collisions)", len(entity_ids) == 100)

# Also test with different sources for the same id
cross_source_ids = set()
sources = ["ceph-tracker", "ibm-jira", "bugzilla", "rhkb", "github"]
for src in sources:
    for i in range(20):
        eid = make_entity_id(src, str(i))
        cross_source_ids.add(eid)
print(f"  Cross-source: 5 sources x 20 ids = {len(cross_source_ids)} unique (expected 100)")
check("Cross-source: 100 unique entity_ids", len(cross_source_ids) == 100)

# ---------------------------------------------------------------------------
# 5. Comment edge cases
# ---------------------------------------------------------------------------
print("\n--- Test 5: Comment edge cases ---")
c_empty = Comment(author="", body="", created_at="")
check("Comment with all empty strings", c_empty.author == "" and c_empty.body == "")

c_unicode = Comment(
    author="用户名",
    body="This is a comment with émojis 🎉 and ünïcödé and 中文",
    created_at="2024-01-01T00:00:00Z",
    comment_id="unicode-1",
)
print(f"  Unicode comment author: {c_unicode.author}")
print(f"  Unicode comment body: {c_unicode.body[:60]}...")
check("Comment with unicode author", c_unicode.author == "用户名")
check("Comment with unicode/emoji body", "🎉" in c_unicode.body)

c_special = Comment(
    author="user<script>alert('xss')</script>",
    body="Line 1\nLine 2\n\tTabbed\n\n\nMultiple blanks",
    created_at="2024-01-01",
)
print(f"  Special chars author: {c_special.author}")
check("Comment stores special chars as-is", "<script>" in c_special.author)
check("Comment preserves newlines", "\n" in c_special.body)

c_long = Comment(
    author="a" * 1000,
    body="x" * 100_000,
    created_at="2024-01-01",
)
print(f"  Long comment: author len={len(c_long.author)}, body len={len(c_long.body)}")
check("Comment handles very long strings", len(c_long.body) == 100_000)

# ---------------------------------------------------------------------------
# 6. Relationship edge cases
# ---------------------------------------------------------------------------
print("\n--- Test 6: Relationship edge cases ---")
r_empty = Relationship(relation_type="", target_source="", target_id="")
check("Relationship with all empty strings", r_empty.relation_type == "")

r_types = ["duplicate", "related", "blocks", "blocked_by", "copied_to", "copied_from",
           "has_subtask", "subtask_of", "fixed_by", "causes"]
for rt in r_types:
    r = Relationship(relation_type=rt, target_source="ceph-tracker", target_id="12345")
    check(f"Relationship type '{rt}'", r.relation_type == rt)

r_url = Relationship(
    relation_type="related",
    target_source="github",
    target_id="PR-5678",
    target_url="https://github.com/ceph/ceph/pull/5678",
)
check("Relationship with URL", "github.com" in r_url.target_url)

# ---------------------------------------------------------------------------
# 7. RawIssue edge cases
# ---------------------------------------------------------------------------
print("\n--- Test 7: RawIssue edge cases ---")
raw_empty = RawIssue(source="test", source_id="1", source_url="")
check("RawIssue with empty data dict", raw_empty.data == {})

raw_nested = RawIssue(
    source="test", source_id="2", source_url="https://example.com",
    data={
        "deeply": {"nested": {"value": [1, 2, {"key": "val"}]}},
        "list": [1, "two", 3.0, None, True],
        "null_val": None,
    },
)
check("RawIssue with deeply nested data", raw_nested.data["deeply"]["nested"]["value"][2]["key"] == "val")
check("RawIssue data with mixed types", raw_nested.data["list"][3] is None)

# ---------------------------------------------------------------------------
print("\n" + "=" * 80)
print(f"WORKFLOW D SUMMARY: {PASS} passed, {FAIL} failed")
print("=" * 80)
sys.exit(1 if FAIL else 0)
