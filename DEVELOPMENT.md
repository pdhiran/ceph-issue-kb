# Development Guide — ceph-issue-kb

## Architecture

The system has two distinct phases:

```
┌─────────────────────────────────────────────────────────┐
│                    INDEXING PHASE                         │
│  (offline, run by maintainer)                            │
│                                                          │
│  Connectors → RawIssue → SignalExtractor → Normalizer   │
│                                    ↓                     │
│                            NormalizedIssue                │
│                                    ↓                     │
│                         Embedder → FAISS + BM25          │
└─────────────────────────────────────────────────────────┘
                          │
                          ▼ knowledge/ directory
┌─────────────────────────────────────────────────────────┐
│                    SERVING PHASE                          │
│  (runtime, MCP server or REST API)                       │
│                                                          │
│  Query → BM25 Search → Semantic Search → Similarity      │
│                                                          │
│  MCP Server (stdio/SSE) / REST API (HTTP)                │
└─────────────────────────────────────────────────────────┘
```

**Indexing mode** runs offline — fetches issues from all configured connectors, extracts signals, normalizes, and builds search indices. Requires network access to issue trackers.

**Server mode** runs anywhere — loads pre-built indices from `knowledge/`. No issue tracker access required.

## Source Tree

```
ceph-issue-kb/
├── pyproject.toml              # Package config, dependencies
├── connectors.yaml             # Connector configuration (sources, auth, rate limits)
├── index_issues.py             # CLI: fetch + normalize + embed + store
│
├── src/ceph_issue_kb/
│   ├── __init__.py
│   ├── models.py               # NormalizedIssue, RawIssue, Comment, Relationship, SearchResult
│   ├── config.py               # connectors.yaml loader + validation
│   ├── signal_extractor.py     # Extract stacktraces, assertions, commands, etc.
│   │
│   ├── connectors/
│   │   ├── __init__.py         # Connector registry + factory
│   │   ├── base.py             # BaseConnector ABC
│   │   ├── auth.py             # AuthProvider (env-var credential resolution)
│   │   ├── redmine.py          # Ceph Tracker connector
│   │   ├── jira.py             # IBM JIRA connector
│   │   ├── bugzilla.py         # Red Hat Bugzilla connector
│   │   └── rhkb.py             # Red Hat KB connector
│   │
│   ├── indexer/
│   │   ├── __init__.py
│   │   ├── normalizer.py       # RawIssue → NormalizedIssue (per-source normalizers)
│   │   ├── embedder.py         # fastembed ONNX + FAISS index builder
│   │   └── builder.py          # Pipeline orchestrator (fetch → normalize → embed → store)
│   │
│   ├── search/
│   │   ├── __init__.py
│   │   ├── engine.py           # BM25 + semantic search with RRF merge
│   │   └── similarity.py       # Similarity scoring V1 (title + desc + metadata)
│   │
│   └── server/
│       ├── __init__.py
│       ├── kb.py               # KnowledgeBase facade (shared by MCP + REST)
│       ├── mcp_server.py       # MCP server (stdio/SSE)
│       └── rest_api.py         # REST API (Starlette + Uvicorn)
│
├── tests/
│   ├── fixtures/               # Sample API responses per connector
│   │   ├── redmine_issue.json
│   │   ├── redmine_issues_page.json
│   │   ├── jira_issue.json
│   │   ├── jira_search.json
│   │   ├── bugzilla_bug.json
│   │   ├── bugzilla_comments.json
│   │   ├── bugzilla_search.json
│   │   ├── rhkb_article.json
│   │   └── rhkb_search.json
│   ├── test_config.py          # Config loading + validation
│   ├── test_connectors.py      # Auth, factory, RedmineConnector
│   ├── test_models.py          # Entity ID, dataclasses
│   ├── test_signal_extractor.py # Signal extraction
│   ├── test_jira_connector.py  # JIRA connector with mocked HTTP
│   ├── test_bugzilla_connector.py # Bugzilla connector with mocked HTTP
│   ├── test_rhkb_connector.py  # Red Hat KB connector with mocked HTTP
│   ├── test_normalizer.py      # Per-source normalizers + signal integration
│   ├── test_embedder.py        # Embedder unit tests
│   ├── test_search.py          # Search engine (BM25, semantic, merge, filters)
│   ├── test_similarity.py      # Similarity engine + fingerprinting
│   ├── test_mcp_server.py      # MCP server tool registration + responses
│   ├── test_rest_api.py        # REST API endpoints + error handling
│   ├── test_builder.py         # Builder pipeline tests
│   ├── test_comprehensive_workflows.py  # End-to-end integration workflows
│   └── workflow_*.py           # Integration workflow scripts
│
├── examples/                   # Integration examples
│   └── agent_integration.py    # Python client, LangChain/CrewAI tools
│
├── knowledge/                  # Built indices (gitignored)
│   └── issues-2024-2025/
│       ├── ceph-tracker/
│       │   ├── issues.json
│       │   └── faiss.index
│       ├── ibm-jira/
│       ├── redhat-bugzilla/
│       ├── redhat-kb/
│       ├── merged_bm25_index.json
│       ├── relationships.json
│       └── metadata.json
│
├── SPEC.md                     # MCP contract documentation
├── DEVELOPMENT.md              # This file
├── README.md                   # Quick start and overview
├── BOB_INTEGRATION_GUIDE.md    # Agent integration guide
└── .cursor/rules/              # Cursor AI rules
    └── issue-lookup.mdc
```

## Knowledge Base On-Disk Layout

```
knowledge/issues-{date_range}/
├── metadata.json               # IndexMetadata: date range, connector stats, model info
├── merged_bm25_index.json      # Cross-source BM25 index for keyword search
├── relationships.json          # Cross-source issue relationships (duplicates, related)
├── ceph-tracker/
│   ├── issues.json             # [{entity_id, title, description, signals, ...}]
│   └── faiss.index             # FAISS IndexFlatIP (cosine on L2-normalized vectors)
├── ibm-jira/
│   ├── issues.json
│   └── faiss.index
├── redhat-bugzilla/
│   ├── issues.json
│   └── faiss.index
└── redhat-kb/
    ├── issues.json
    └── faiss.index
```

Each connector's issues are stored in their own subdirectory. This allows:
- Independent indexing and updating per source
- Source-specific search scoping
- Incremental updates without reindexing all sources
- Clear provenance for every issue

The `merged_bm25_index.json` spans all sources for cross-source keyword search. The `relationships.json` tracks cross-source links (e.g., a Ceph Tracker issue linked to a JIRA ticket).

## Connector Framework

### BaseConnector ABC

Every connector implements:

| Method | Return | Description |
|--------|--------|-------------|
| `authenticate()` | `None` | Validate credentials (no-op for public APIs) |
| `search(query, since, limit)` | `Iterator[RawIssue]` | Search with internal pagination |
| `fetch(issue_id)` | `RawIssue` | Fetch single issue with comments + relations |
| `fetch_updates(since)` | `Iterator[RawIssue]` | All issues updated since date |
| `health()` | `dict` | Connectivity + stats check |

Key design decisions:
- **Iterator-based pagination** — connectors handle API pagination internally; callers just iterate
- **Connectors return raw data only** — no normalization in connectors; that's the normalizer's job
- **Rate limiting built in** — each connector enforces its configured rate limit
- **Auth from env vars** — credentials never in code or config files

### Adding a New Connector

```python
# src/ceph_issue_kb/connectors/my_source.py
from ceph_issue_kb.connectors.base import BaseConnector

class MySourceConnector(BaseConnector):
    def authenticate(self) -> None: ...
    def search(self, query, *, since=None, limit=100): ...
    def fetch(self, issue_id): ...
    def fetch_updates(self, since): ...
    def health(self) -> dict: ...
```

Register in `connectors/__init__.py`:
```python
_CONNECTOR_TYPES["my_source"] = MySourceConnector
```

Add to `connectors.yaml`:
```yaml
connectors:
  my-source:
    type: my_source
    enabled: true
    base_url: https://my-source.example.com
    auth:
      method: api_key
      key_env: MY_SOURCE_API_KEY
```

No core code changes required.

## Signal Extraction

The `SignalExtractor` uses regex patterns optimized for Ceph-specific text:

| Signal | Patterns |
|--------|----------|
| Stacktraces | Python tracebacks, C++ `#N 0xaddr in func`, core dumps, segfaults, `ceph_abort` |
| Assertions | Lines containing `assert`, `FAILED`, `abort`, `ceph_assert_fail` |
| Health warnings | 25+ Ceph health status codes: `HEALTH_WARN`, `OSD_DOWN`, `PG_DEGRADED`, etc. |
| Commands | `ceph`, `rbd`, `rados`, `radosgw-admin`, `cephadm`, `ceph-volume`, etc. |
| Configs | Known-prefix match: `osd_*`, `mon_*`, `rgw_*`, `bluestore_*`, etc. |
| Log snippets | ISO timestamps, syslog timestamps, time-only prefixes |

Containment-based deduplication prevents overlapping regex matches from producing duplicate entries.

## Authentication

```
connectors.yaml          Environment Variables
┌──────────────┐         ┌────────────────────┐
│ auth:        │         │ JIRA_USERNAME=...  │
│   method:    │────────►│ JIRA_API_TOKEN=... │
│   token_env: │         │ BUGZILLA_API_KEY=. │
└──────────────┘         └────────────────────┘
         │
         ▼
┌──────────────┐
│ AuthProvider  │ → Credentials dataclass
└──────────────┘
```

Supported methods: `none`, `api_token`, `api_key`, `cookie`

## REST API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/search_issues` | POST | Search issues across all sources |
| `/api/find_similar_issue` | POST | Find issues similar to a description |
| `/api/is_known_issue` | POST | Check if an error matches a known issue |
| `/api/find_workaround` | POST | Find known workarounds |
| `/api/find_fix` | POST | Find fixes, commits, PRs |
| `/api/find_related_issues` | POST | Get related/duplicate/linked issues |
| `/api/search_stacktrace` | POST | Find issues with similar stacktraces |
| `/api/search_health_warning` | POST | Find issues for a health warning |
| `/api/hot_issues` | GET | Most active recent issues |
| `/api/component_health/{component}` | GET | Open criticals, regressions, blockers |
| `/health` | GET | Server health + connector status |
| `/capabilities` | GET | Server capabilities and entity types |

Start the server:
```bash
python3 -m ceph_issue_kb.server.rest_api
# Binds to 127.0.0.1:8200 (configurable)
```

## Running as a Persistent Service

### systemd

```ini
[Unit]
Description=Ceph Issue KB REST API
After=network.target

[Service]
Type=simple
User=ceph-kb
WorkingDirectory=/opt/ceph-issue-kb
ExecStart=/opt/ceph-issue-kb/.venv/bin/python3 -m ceph_issue_kb.server.rest_api --host 0.0.0.0 --port 8200
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### Docker

```bash
docker run -d -p 8200:8200 \
  -e JIRA_USERNAME=... -e JIRA_API_TOKEN=... \
  -v /path/to/knowledge:/app/knowledge \
  ceph-issue-kb
```

## Running Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

Test coverage (359 tests total):
- `test_config.py` — 8 tests (AuthConfig, ConnectorConfig, load_config, validation)
- `test_connectors.py` — 16 tests (AuthProvider, factory, RedmineConnector with mocked HTTP)
- `test_models.py` — 8 tests (entity_id, NormalizedIssue, RawIssue, Comment, Relationship, SearchResult)
- `test_signal_extractor.py` — 18 tests (all signal types + edge cases)
- `test_jira_connector.py` — 19 tests (JiraConnector with mocked HTTP)
- `test_bugzilla_connector.py` — 16 tests (BugzillaConnector with mocked HTTP)
- `test_rhkb_connector.py` — 14 tests (RHKBConnector with mocked HTTP)
- `test_normalizer.py` — 49 tests (per-source normalizers + signal integration)
- `test_embedder.py` — 7 tests (Embedder unit tests)
- `test_search.py` — 17 tests (BM25, semantic, RRF merge, filters, save/load)
- `test_similarity.py` — 24 tests (similarity engine, fingerprinting, Jaccard)
- `test_mcp_server.py` — 33 tests (MCP server tool registration + responses)
- `test_rest_api.py` — 22 tests (REST API endpoints, error handling)
- `test_builder.py` — 5 tests (builder pipeline with mocked connectors)
- `test_comprehensive_workflows.py` — 103 tests (end-to-end integration workflows)
- `workflow_*.py` — integration workflow scripts

## Key Design Decisions

1. **Per-source storage** — each connector's issues stored in separate directories. Enables independent indexing, source-scoped search, and clear provenance.
2. **Connector-normalizer separation** — connectors return raw data; normalization is a separate pipeline stage. Allows re-normalization without re-fetching.
3. **Signal extraction at index time** — stacktraces, assertions, health warnings extracted during normalization, not at query time. Enables signal-specific search indices.
4. **Two-tier search** — BM25 for exact error message matches (the most common triage pattern), semantic for conceptual similarity. BM25 alone misses paraphrased issues; semantic alone misses exact error strings.
5. **16-char hex entity IDs** — `sha256(source:source_id)[:16]`. Stable, reproducible, cross-source safe. Collision probability is negligible at this scale.
6. **Iterator-based pagination** — connectors handle pagination internally. Callers never deal with page tokens or offsets.
7. **Auth from environment variables** — credentials referenced by env var name in config, never stored in code or YAML values.

## Dependencies

| Package | Purpose |
|---------|---------|
| requests | HTTP client for connectors |
| pyyaml | Config file parsing |
| fastembed | ONNX embeddings (optional: `[search]`) |
| faiss-cpu | Vector similarity search (optional: `[search]`) |
| rank-bm25 | BM25 keyword search (optional: `[search]`) |
| mcp | MCP server protocol (optional: `[server]`) |
| starlette + uvicorn | REST API (optional: `[server]`) |
| numpy | Embedding vector operations |

## Maintainer Guide

### Rebuilding the Issue Index

```bash
# Full rebuild from all sources
python3 index_issues.py --config connectors.yaml --verbose

# Single source only
python3 index_issues.py --connector ceph-tracker --verbose

# Incremental update (issues since a date)
python3 index_issues.py --config connectors.yaml --since 2025-01-01 --verbose

# Custom output directory
python3 index_issues.py --output-dir knowledge/issues-2025-2026 --verbose
```

### Adding a New Ceph Version Range

1. Update `connectors.yaml` with the target date range
2. Run `index_issues.py` with `--since` to fetch new issues
3. The new index is stored alongside existing ones in `knowledge/`
4. The server auto-selects the latest index

### Updating Connectors

When an issue tracker changes its API:
1. Update the connector class in `src/ceph_issue_kb/connectors/`
2. Run tests: `pytest tests/test_connectors.py -v`
3. Re-fetch affected issues: `python3 index_issues.py --connector <name> --verbose`
