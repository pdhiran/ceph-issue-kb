# Engineering Intelligence MCP Contract — ceph-issue-kb

Version: 1.0

This document describes how `ceph-issue-kb` implements the Engineering Intelligence MCP platform contract, enabling multi-KB orchestration with other Ceph knowledge bases (`ceph-cmd-kb`, `ceph-doc-kb`).

## Platform Vision

```
┌─────────────────────────────────────────────────────┐
│                   Agent / LLM                        │
│         (orchestrates multiple KBs)                  │
└──────┬──────────────┬──────────────┬────────────────┘
       │              │              │
       ▼              ▼              ▼
┌──────────┐   ┌──────────┐   ┌──────────┐
│ ceph-cmd │   │ ceph-doc │   │ceph-issue│
│    KB    │   │    KB    │   │    KB    │
│(commands)│   │  (docs)  │   │ (issues) │
└──────────┘   └──────────┘   └──────────┘
```

Each KB exposes a consistent interface so agents can discover capabilities, check health, and query any KB without special-casing.

## Contract Implementation

### Mandatory Tools

| Tool | Description | Response |
|------|-------------|----------|
| `capabilities()` | Declare entity types, operations, sources | `{name, schema_version, entity_types, operations, sources, entity_counts}` |
| `health()` | Report connector status, index health | `{status, connectors, total_issues, index_status, schema_version}` |

`status` must be one of: `"ok"`, `"degraded"`, `"error"`.

### Recommended Tools

These are the abstract platform contract tools. They define the interface that any Engineering Intelligence KB should implement when applicable. The `ceph-issue-kb` project implements these as the domain-specific tools listed in the "Domain-Specific Tools" section below (e.g., `search` becomes `search_issues`, `related` becomes `find_related_issues`).

| Tool | Description |
|------|-------------|
| `search(query, filters)` | Generic search across all entity types |
| `lookup(entity_id)` | Retrieve a single entity by its stable ID |
| `related(entity_id)` | Return entities related to the given entity |
| `metadata(entity_id)` | Return metadata, provenance, and confidence for an entity |

### Entity Types

| Type | Description | ID Scheme |
|------|-------------|-----------|
| `issue` | A normalized issue from any source | `sha256(source:source_id)[:16]` |
| `comment` | A comment on an issue | Nested within issue |
| `relationship` | A link between issues | Nested within issue |

### Entity ID Generation

All entity IDs are 16-character hex strings derived from a SHA-256 hash of a stable key:

```python
entity_id = hashlib.sha256(f"{source}:{source_id}".encode()).hexdigest()[:16]
```

This ensures IDs are:
- **Stable** — same issue always produces same ID
- **Unique** — collision probability negligible at this length
- **Reproducible** — can be regenerated from source metadata
- **Cross-source safe** — same issue ID in different trackers produces different entity IDs

Entity IDs enable cross-KB references. A Documentation KB can reference `issue:22b7cddd3f1fc2b7` without knowing the issue title or which KB owns it.

### Version Awareness

Issues are indexed by date range rather than Ceph version, since issues span multiple versions:

```
knowledge/
  issues-2024-2025/     # Issues from 2024-2025
  issues-2023-2024/     # Older issues
```

The MCP server loads the latest available index by default.

## Entity Schema

Every entity includes these common fields alongside domain-specific fields:

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `entity_id` | string | Yes | Stable deterministic 16-char hex ID |
| `entity_type` | string | Yes | `"issue"`, `"comment"`, `"relationship"` |
| `source` | string | Yes | Connector name (e.g., `"ceph-tracker"`) |
| `source_id` | string | Yes | ID in the source system |
| `source_url` | string | Yes | URL to the issue in the source tracker |
| `title` | string | Yes | Issue title |
| `summary` | string | No | First ~500 chars of description |
| `status` | string | No | Issue status (open, closed, resolved, etc.) |
| `priority` | string | No | Priority level |
| `components` | list | No | Affected Ceph components |
| `affected_versions` | list | No | Ceph versions affected |
| `keywords` | list | No | Searchable keywords |
| `relationships` | list | No | Cross-entity references |

## Domain-Specific Tools

| Tool | Purpose | Cross-KB Complement |
|------|---------|---------------------|
| `search_issues(query, source, component, version, status, limit)` | Search across all sources | Provides issue context for commands/docs |
| `find_similar_issue(description, stacktrace, component)` | Find similar issues | Dedup across sources |
| `is_known_issue(error_message, version)` | Check if error is known | The #1 daily triage use case |
| `find_workaround(issue_id_or_query)` | Find known workarounds | Actionable resolution |
| `find_fix(issue_id_or_query)` | Find fixes, commits, PRs | Links to actual code changes |
| `find_related_issues(issue_id)` | Related/duplicate/linked issues | Cross-source linking |
| `search_stacktrace(stacktrace)` | Find issues with similar stacks | Failure fingerprinting |
| `search_health_warning(warning)` | Issues for a health warning | Bridges health status → issues |
| `hot_issues(component, limit)` | Most active recent issues | Situational awareness |
| `component_health(component)` | Open criticals, regressions | Risk assessment |

## Response Format

All tool responses are JSON. Error responses use:

```json
{
  "error": "Description of what went wrong",
  "status": "error"
}
```

Search results follow a consistent schema:

```json
{
  "entity_id": "22b7cddd3f1fc2b7",
  "entity_type": "issue",
  "source": "ceph-tracker",
  "source_id": "68051",
  "source_url": "https://tracker.ceph.com/issues/68051",
  "title": "Dashboard module fails to connect",
  "summary": "First ~500 chars of description...",
  "status": "open",
  "priority": "high",
  "components": ["dashboard"],
  "affected_versions": ["19.2.0"],
  "stacktraces": ["Traceback (most recent call last):\n  ..."],
  "assertions": ["ceph_abort_msg(\"not implemented\")"],
  "health_warnings": ["HEALTH_WARN"],
  "commands_mentioned": ["ceph osd pool set rbd size 3"],
  "configs_mentioned": ["mon_max_pg_per_osd"],
  "similarity": 0.87,
  "matched_signals": ["same assertion", "same component"]
}
```

## Signal Extraction

During normalization, the following signals are automatically extracted from issue description + comments:

| Signal | Detection Method | Use Case |
|--------|-----------------|----------|
| **Stacktraces** | Python tracebacks, C++ frames, core dumps, segfaults | Failure fingerprinting |
| **Assertions** | `assert`, `FAILED`, `abort`, `ceph_assert_fail` | Duplicate detection |
| **Health warnings** | `HEALTH_WARN`, `HEALTH_ERR`, `PG_*`, `OSD_*`, `MON_*` | Health status → issues |
| **Commands** | `ceph`, `rbd`, `rados`, `cephadm`, `radosgw-admin` | Cross-ref with cmd-kb |
| **Configs** | `osd_*`, `mon_*`, `rgw_*`, etc. (known prefix match) | Cross-ref with cmd-kb |
| **Log snippets** | ISO/syslog timestamped lines | Context preservation |

## Orchestration Pattern

An agent using all three KBs follows this pattern:

```
1. User reports: "OSD.5 is slow, seeing HEALTH_WARN"

2. Agent → ceph-issue-kb.search_health_warning("HEALTH_WARN")
   → finds 3 matching issues, one with same OSD pattern

3. Agent → ceph-issue-kb.find_workaround(issue_id)
   → "Set osd_recovery_max_active = 1"

4. Agent → ceph-cmd-kb.verify_config("osd_recovery_max_active")
   → Confirmed: exists, type=int, runtime-updatable

5. Agent → ceph-doc-kb.search_docs("OSD recovery tuning", component="rados")
   → Documentation on recovery parameters

6. Agent synthesizes: known issue + verified config fix + doc reference
```

## Similarity Engine (V1)

V1 uses 3 signals for similarity scoring:

| Signal | Weight | Method |
|--------|--------|--------|
| Title similarity | 0.3 | BM25 + cosine on embeddings |
| Description/stacktrace similarity | 0.5 | Cosine on embeddings |
| Metadata overlap | 0.2 | Component, version, health warning match |

## Adding a New Connector

1. Create a new class extending `BaseConnector` in `src/ceph_issue_kb/connectors/`
2. Implement: `authenticate()`, `search()`, `fetch()`, `fetch_updates()`, `health()`
3. Register the type in `connectors/__init__.py`
4. Add config entry to `connectors.yaml`

No core code changes required.

## Adding a New KB to the Platform

Any new KB should:

1. Implement `capabilities()` and `health()` tools
2. Use 16-char hex entity IDs via `make_entity_id()`
3. Support source-scoped or version-scoped indices
4. Provide at least one search tool
5. Return results in the standard entity schema
6. Document entity types and their relationships to other KBs
7. Use `schema_version: "1.0"` in capabilities and health responses

## Versioning

- `schema_version`: The contract version (this document). Currently `"1.0"`.
- KB version: The date range of indexed issues (e.g., `"issues-2024-2025"`).
- These are independent. A KB can update its data without changing the schema version.

## Future Extensions

The following will be added to the contract when the corresponding capabilities are built:

- Cross-KB relationship resolution (lookup entity by ID across all KBs)
- Event streaming (subscribe to KB updates)
- Batch operations (check multiple error messages in one call)
- Confidence scoring (how reliable is this match)
- Provenance tracking (which connector, when indexed, data freshness)

These will be added as optional capabilities, preserving backwards compatibility.
