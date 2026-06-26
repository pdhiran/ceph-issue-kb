#!/usr/bin/env python3
"""Workflow F: Integration workflow - full pipeline simulation.

Simulates the complete flow: load config -> create connector -> fetch issue
-> parse into RawIssue -> extract signals -> build NormalizedIssue.
All HTTP is mocked.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "src")

import responses

from ceph_issue_kb.config import load_config
from ceph_issue_kb.connectors import get_connector
from ceph_issue_kb.connectors.redmine import RedmineConnector
from ceph_issue_kb.models import (
    Comment, NormalizedIssue, RawIssue, Relationship, make_entity_id,
)
from ceph_issue_kb.signal_extractor import extract_signals

FIXTURES = Path("tests/fixtures")
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
print("WORKFLOW F: Full Integration Pipeline")
print("=" * 80)

# ===== STEP 1: Load Config =====
print("\n===== STEP 1: Load Configuration =====")
cfg = load_config("connectors.yaml")
print(f"  Loaded {len(cfg.connectors)} connectors")
print(f"  Enabled: {list(cfg.enabled_connectors.keys())}")
check("Config loaded successfully", len(cfg.connectors) > 0)

tracker_cfg = cfg.connectors["ceph-tracker"]
print(f"  ceph-tracker config:")
print(f"    type: {tracker_cfg.connector_type}")
print(f"    base_url: {tracker_cfg.base_url}")
print(f"    rate_limit: {tracker_cfg.rate_limit}")
print(f"    project: {tracker_cfg.extra.get('project')}")

# ===== STEP 2: Create Connector =====
print("\n===== STEP 2: Create RedmineConnector from Config =====")
connector = get_connector(tracker_cfg)
print(f"  Connector type: {type(connector).__name__}")
print(f"  Connector name: {connector.name}")
print(f"  Base URL: {connector.base_url}")
check("Created RedmineConnector", isinstance(connector, RedmineConnector))
check("Connector name matches config", connector.name == "ceph-tracker")

# ===== STEP 3: Fetch Issue (mocked) =====
print("\n===== STEP 3: Fetch Issue via Connector (mocked HTTP) =====")
fixture = json.loads((FIXTURES / "redmine_issue.json").read_text())

@responses.activate
def step3_fetch():
    responses.add(responses.GET, "https://tracker.ceph.com/issues/68051.json",
                  json=fixture, status=200)
    raw = connector.fetch("68051")
    return raw

raw_issue = step3_fetch()
print(f"  RawIssue:")
print(f"    source: {raw_issue.source}")
print(f"    source_id: {raw_issue.source_id}")
print(f"    source_url: {raw_issue.source_url}")
print(f"    data.subject: {raw_issue.data.get('subject')}")
print(f"    data.status: {raw_issue.data.get('status', {}).get('name')}")
print(f"    data.priority: {raw_issue.data.get('priority', {}).get('name')}")
print(f"    data.author: {raw_issue.data.get('author', {}).get('name')}")
print(f"    journals: {len(raw_issue.data.get('journals', []))}")
print(f"    relations: {len(raw_issue.data.get('relations', []))}")
check("RawIssue has correct source", raw_issue.source == "ceph-tracker")
check("RawIssue has data", len(raw_issue.data) > 0)

# ===== STEP 4: Extract Signals =====
print("\n===== STEP 4: Extract Signals from Description =====")
description = raw_issue.data.get("description", "")
print(f"  Description text ({len(description)} chars):")
for line in description.split("\n"):
    print(f"    | {line}")

signals = extract_signals(description)
print(f"\n  Extracted signals:")
print(f"    stacktraces: {len(signals.stacktraces)}")
for i, s in enumerate(signals.stacktraces):
    print(f"      [{i}] {s[:80]}")
print(f"    assertions: {len(signals.assertions)}")
for a in signals.assertions:
    print(f"      - {a[:80]}")
print(f"    health_warnings: {signals.health_warnings}")
print(f"    commands: {signals.commands_mentioned}")
print(f"    configs: {signals.configs_mentioned}")
print(f"    log_snippets: {len(signals.log_snippets)}")
for ls in signals.log_snippets:
    print(f"      - {ls[:80]}")

check("Signals: has stacktraces", len(signals.stacktraces) > 0)
check("Signals: has health warnings", len(signals.health_warnings) > 0)
check("Signals: has commands", len(signals.commands_mentioned) > 0)
check("Signals: has configs", len(signals.configs_mentioned) > 0)
check("Signals: has log snippets", len(signals.log_snippets) > 0)

# Also extract signals from comments
print("\n  Extracting signals from comments:")
journal_text = " ".join(j.get("notes", "") for j in raw_issue.data.get("journals", []))
comment_signals = extract_signals(journal_text)
print(f"    Comment text: {journal_text[:100]}...")
print(f"    stacktraces from comments: {len(comment_signals.stacktraces)}")
print(f"    assertions from comments: {len(comment_signals.assertions)}")

# ===== STEP 5: Build NormalizedIssue =====
print("\n===== STEP 5: Build NormalizedIssue =====")
data = raw_issue.data
entity_id = make_entity_id(raw_issue.source, raw_issue.source_id)

comments = []
for j in data.get("journals", []):
    if j.get("notes"):
        comments.append(Comment(
            author=j["user"]["name"],
            body=j["notes"],
            created_at=j["created_on"],
            comment_id=str(j["id"]),
        ))

relationships = []
for rel in data.get("relations", []):
    target_id = str(rel.get("issue_to_id", rel.get("issue_id", "")))
    if str(data["id"]) == target_id:
        target_id = str(rel.get("issue_id", ""))
    relationships.append(Relationship(
        relation_type=rel["relation_type"],
        target_source=raw_issue.source,
        target_id=target_id,
        target_url=f"{connector.base_url}/issues/{target_id}",
    ))

components = []
for cf in data.get("custom_fields", []):
    if cf.get("name") == "Component" and cf.get("value"):
        components.append(cf["value"])

all_text = description + "\n" + journal_text
all_signals = extract_signals(all_text)

normalized = NormalizedIssue(
    entity_id=entity_id,
    source=raw_issue.source,
    source_id=raw_issue.source_id,
    source_url=raw_issue.source_url,
    title=data.get("subject", ""),
    description=description,
    comments=comments,
    status=data.get("status", {}).get("name", ""),
    priority=data.get("priority", {}).get("name", ""),
    reporter=data.get("author", {}).get("name", ""),
    created_at=data.get("created_on", ""),
    updated_at=data.get("updated_on", ""),
    components=components,
    stacktraces=all_signals.stacktraces,
    assertions=all_signals.assertions,
    health_warnings=all_signals.health_warnings,
    commands_mentioned=all_signals.commands_mentioned,
    configs_mentioned=all_signals.configs_mentioned,
    log_snippets=all_signals.log_snippets,
    relationships=relationships,
)

print(f"\n  Final NormalizedIssue:")
print(f"    entity_id: {normalized.entity_id}")
print(f"    source: {normalized.source}")
print(f"    source_id: {normalized.source_id}")
print(f"    source_url: {normalized.source_url}")
print(f"    title: {normalized.title}")
print(f"    status: {normalized.status}")
print(f"    priority: {normalized.priority}")
print(f"    reporter: {normalized.reporter}")
print(f"    created_at: {normalized.created_at}")
print(f"    updated_at: {normalized.updated_at}")
print(f"    components: {normalized.components}")
print(f"    entity_type: {normalized.entity_type}")
print(f"    schema_version: {normalized.schema_version}")
print(f"    knowledge_base: {normalized.knowledge_base}")
print(f"    indexed_at: {normalized.indexed_at}")
print(f"    ---")
print(f"    comments: {len(normalized.comments)}")
for c in normalized.comments:
    print(f"      [{c.comment_id}] {c.author}: {c.body[:60]}...")
print(f"    relationships: {len(normalized.relationships)}")
for r in normalized.relationships:
    print(f"      {r.relation_type} -> {r.target_source}:{r.target_id} ({r.target_url})")
print(f"    stacktraces: {len(normalized.stacktraces)}")
print(f"    assertions: {len(normalized.assertions)}")
print(f"    health_warnings: {normalized.health_warnings}")
print(f"    commands: {normalized.commands_mentioned}")
print(f"    configs: {normalized.configs_mentioned}")
print(f"    log_snippets: {len(normalized.log_snippets)}")

check("entity_id is 16-char hex", len(normalized.entity_id) == 16)
check("title set", normalized.title == "OSD crash during deep scrub with BlueStore")
check("status set", normalized.status == "New")
check("priority set", normalized.priority == "Normal")
check("reporter set", normalized.reporter == "Jane Dev")
check("2 comments", len(normalized.comments) == 2)
check("1 relationship", len(normalized.relationships) == 1)
check("Component extracted", "bluestore" in normalized.components)
check("entity_type is 'issue'", normalized.entity_type == "issue")
check("schema_version is '1.0'", normalized.schema_version == "1.0")
check("Signals carried into normalized issue", len(normalized.stacktraces) > 0)

# ===== STEP 6: Verify round-trip consistency =====
print("\n===== STEP 6: Round-Trip Consistency Checks =====")
entity_id_2 = make_entity_id(raw_issue.source, raw_issue.source_id)
check("Entity ID is deterministic across calls", entity_id == entity_id_2)

# Verify that creating the same issue again yields the same entity_id
normalized_2 = NormalizedIssue(
    entity_id=make_entity_id(raw_issue.source, raw_issue.source_id),
    source=raw_issue.source,
    source_id=raw_issue.source_id,
    source_url=raw_issue.source_url,
)
check("Same source+id => same entity_id", normalized.entity_id == normalized_2.entity_id)

print("\n  Pipeline complete: Config -> Connector -> Fetch -> Signals -> NormalizedIssue")

# ---------------------------------------------------------------------------
print("\n" + "=" * 80)
print(f"WORKFLOW F SUMMARY: {PASS} passed, {FAIL} failed")
print("=" * 80)
sys.exit(1 if FAIL else 0)
