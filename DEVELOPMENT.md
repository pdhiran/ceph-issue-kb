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
│  MCP Server (stdio) / REST API (HTTP)                    │
└─────────────────────────────────────────────────────────┘
```

## Source Tree

```
ceph-issue-kb/
├── pyproject.toml              # Package config, dependencies
├── connectors.yaml             # Connector configuration (sources, auth, rate limits)
├── index_issues.py             # CLI: fetch + normalize + index
│
├── src/ceph_issue_kb/
│   ├── __init__.py
│   ├── models.py               # NormalizedIssue, RawIssue, Comment, Relationship
│   ├── config.py               # connectors.yaml loader + validation
│   ├── signal_extractor.py     # Extract stacktraces, assertions, commands, etc.
│   │
│   ├── connectors/
│   │   ├── __init__.py         # Connector registry + factory
│   │   ├── base.py             # BaseConnector ABC
│   │   ├── auth.py             # AuthProvider (env-var credential resolution)
│   │   ├── redmine.py          # Ceph Tracker connector (Phase 1)
│   │   ├── jira.py             # IBM JIRA connector (Phase 2)
│   │   ├── bugzilla.py         # Red Hat Bugzilla connector (Phase 2)
│   │   └── rhkb.py             # Red Hat KB connector (Phase 2)
│   │
│   ├── indexer/
│   │   ├── __init__.py
│   │   ├── normalizer.py       # RawIssue → NormalizedIssue (Phase 3)
│   │   ├── embedder.py         # fastembed ONNX + FAISS (Phase 3)
│   │   └── builder.py          # Pipeline orchestrator (Phase 3)
│   │
│   ├── search/
│   │   ├── __init__.py
│   │   ├── engine.py           # BM25 + semantic search (Phase 3)
│   │   └── similarity.py       # Similarity scoring V1 (Phase 4)
│   │
│   └── server/
│       ├── __init__.py
│       ├── mcp_server.py       # MCP server (Phase 4)
│       └── rest_api.py         # REST API (Phase 4)
│
├── tests/
│   ├── fixtures/               # Sample Redmine API responses
│   │   ├── redmine_issue.json
│   │   └── redmine_issues_page.json
│   ├── test_config.py          # Config loading + validation
│   ├── test_connectors.py      # Auth, factory, RedmineConnector
│   ├── test_models.py          # Entity ID, dataclasses
│   └── test_signal_extractor.py # Signal extraction
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
└── .cursor/rules/              # Cursor AI rules
    └── issue-lookup.mdc
```

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

## Running Tests

```bash
pip install -e ".[dev]"
pytest tests/ -v
```

Current test coverage:
- `test_config.py` — 8 tests (AuthConfig, ConnectorConfig, load_config, validation)
- `test_connectors.py` — 11 tests (AuthProvider, factory, RedmineConnector with mocked HTTP)
- `test_models.py` — 7 tests (entity_id, NormalizedIssue, RawIssue, Comment, Relationship)
- `test_signal_extractor.py` — 21 tests (all signal types + edge cases)

## Dependencies

| Package | Purpose | Phase |
|---------|---------|-------|
| requests | HTTP client for connectors | 1 |
| pyyaml | Config file parsing | 1 |
| fastembed | ONNX embeddings | 3 |
| faiss-cpu | Vector similarity search | 3 |
| rank-bm25 | BM25 keyword search | 3 |
| mcp | MCP server protocol | 4 |
| starlette + uvicorn | REST API | 4 |

## Development Phases

| Phase | Deliverable | Status |
|-------|-------------|--------|
| 1 | Architecture + models + connector framework + auth + Ceph Tracker connector | Done |
| 2 | JIRA + Bugzilla + Red Hat KB connectors | Planned |
| 3 | Normalizer + signal extractor integration + search engine (BM25 + fastembed) | Planned |
| 4 | Similarity engine (V1) + MCP server + REST API | Planned |
| 5 | Tests + README + pre-built index + Cursor rule | Planned |
