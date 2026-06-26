# Ceph Issue Intelligence KB

Issue intelligence knowledge base for Ceph engineering. Indexes **14,670+ issues from 2 sources** (IBM Ceph JIRA, Red Hat KB) into a unified, searchable knowledge base with **12 MCP tools** and a REST API. Answers: **"Has this problem been seen before?"**

## Quick Start

```bash
# Install
pip install -e ".[all]"

# Run tests
pytest

# Build the issue index (requires credentials — see CREDENTIALS.md)
export JIRA_USERNAME=your_user JIRA_API_TOKEN=your_token
export RH_OFFLINE_TOKEN=your_token
python3 index_issues.py --config connectors.yaml --since 2024-01-01 --verbose

# Index a single source
python3 index_issues.py --connector ibm-jira --verbose
```

Use the connector framework programmatically:

```python
from ceph_issue_kb.config import load_config
from ceph_issue_kb.connectors import get_connector

config = load_config("connectors.yaml")
connector = get_connector(config.connectors["ibm-jira"])
connector.authenticate()

issue = connector.fetch("IBMCEPH-12345")
print(issue.source, issue.source_id, issue.data.get("summary"))
```

## Architecture

- **Connector framework**: Plugin-based — each issue source implements `BaseConnector`. Adding a new source = one class + a config entry
- **Signal extraction**: Automatically extracts stacktraces, assertions, health warnings, Ceph commands, config params, and log snippets from issue text
- **Common schema**: Every issue from every source normalizes to `NormalizedIssue` with extracted signals
- **Two-tier search**: BM25 keyword match (exact error messages) + fastembed semantic search (conceptual similarity)
- **Similarity engine**: Weighted scoring across title, description/stacktrace, and metadata overlap
- **Per-source storage**: Each connector's issues stored separately for scalability

## Connect Your Agent

Choose the integration that matches your agent:

---

**Cursor** — add to `~/.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "ceph-issue-kb": {
      "command": "python3",
      "args": ["-m", "ceph_issue_kb.server.mcp_server"],
      "cwd": "/path/to/ceph-issue-kb"
    }
  }
}
```

---

**Claude Desktop** — start the server, then add to `claude_desktop_config.json`:

```bash
python -m ceph_issue_kb.server.mcp_server --transport sse --port 8080
```

```json
{
  "mcpServers": {
    "ceph-issue-kb": { "url": "http://localhost:8080/sse" }
  }
}
```

---

**Continue / Cline / Windsurf** — start the server and point to the SSE endpoint:

```bash
python -m ceph_issue_kb.server.mcp_server --transport sse --port 8080
```

Connect to `http://localhost:8080/sse` in the tool's MCP settings.

---

**IBM watsonx / IBM Bob / LangChain / CrewAI / CI pipelines** — use the REST API:

```bash
python -m ceph_issue_kb.server.rest_api --host 0.0.0.0 --port 8200
```

```bash
# Search issues
curl -X POST http://localhost:8200/api/search_issues \
  -H "Content-Type: application/json" \
  -d '{"query": "OSD slow ops", "component": "rados"}'

# Check if an error is a known issue
curl -X POST http://localhost:8200/api/is_known_issue \
  -H "Content-Type: application/json" \
  -d '{"error_message": "FAILED ceph_assert(googly > 0)", "version": "19.2.0"}'

# Find workaround
curl -X POST http://localhost:8200/api/find_workaround \
  -H "Content-Type: application/json" \
  -d '{"query": "too many PGs per OSD"}'

# Search by health warning
curl -X POST http://localhost:8200/api/search_health_warning \
  -H "Content-Type: application/json" \
  -d '{"warning": "HEALTH_WARN too many PGs per OSD"}'

# Component health
curl http://localhost:8200/api/component_health/rgw

# Health check
curl http://localhost:8200/health
```

**Additional integration guides:**
- [BOB_INTEGRATION_GUIDE.md](BOB_INTEGRATION_GUIDE.md) — REST API reference, agent integration, deployment options
- [examples/agent_integration.py](examples/agent_integration.py) — Ready-to-use Python client, LangChain/CrewAI tools

### Use it

Once connected, agents automatically check for known Ceph issues. You can also ask directly:

- *"Is `FAILED ceph_assert(googly > 0)` a known issue?"*
- *"Find workarounds for OSD slow ops during recovery"*
- *"Search for issues related to HEALTH_WARN too many PGs per OSD"*
- *"What are the hot issues in the rgw component?"*
- *"Find issues with this stacktrace: `#0 in BlueStore::_do_write`"*

## MCP Tools

| Tool | Description |
|------|-------------|
| `search_issues` | Search issues across all sources with optional filters |
| `find_similar_issue` | Find issues similar to a given problem description |
| `is_known_issue` | Check if an error message matches a known issue |
| `find_workaround` | Search for known workarounds |
| `find_fix` | Search for known fixes, commits, PRs |
| `find_related_issues` | Get related/duplicate/linked issues |
| `search_stacktrace` | Find issues with similar stacktraces |
| `search_health_warning` | Find issues related to a health warning |
| `hot_issues` | Most active recent issues by component |
| `component_health` | Open criticals, regressions, blockers for a component |
| `capabilities` | Server capabilities and entity types |
| `health` | Connector status, issue counts, index status |

## Three MCPs Working Together

```
User: "We're seeing 'HEALTH_WARN too many PGs per OSD' after adding OSDs"

Agent:
1. search_health_warning("too many PGs per OSD")         <- Issue KB (JIRA + RH KB)
2. search_docs("PG autoscaler", component="rados")       <- Doc KB
3. verify_config("mon_max_pg_per_osd")                   <- Command KB
4. find_workaround("too many PGs per OSD")               <- Issue KB
5. Synthesizes: known issue, doc reference, config fix, prior workarounds
```

## Agent Integration

Python client for LLM agents (no external dependencies):

```python
from examples.agent_integration import CephIssueKBClient

client = CephIssueKBClient("http://localhost:8200")
result = client.is_known_issue("FAILED ceph_assert(googly > 0)")
```

LangChain and CrewAI wrappers included. See [BOB_INTEGRATION_GUIDE.md](BOB_INTEGRATION_GUIDE.md).

## Connectors

### Active

| Connector | Source | Issues | Auth |
|-----------|--------|--------|------|
| `JiraConnector` | IBM Ceph JIRA | 14,037 | API token |
| `RHKBConnector` | Red Hat KB | 633 | Offline token |

### Future Connectors

The following connectors are implemented but not yet enabled. The code exists in `src/ceph_issue_kb/connectors/` for when these sources are activated.

| Connector | Source | Status |
|-----------|--------|--------|
| `RedmineConnector` | [Ceph Tracker](https://tracker.ceph.com) | Deferred — upstream Redmine API needs pagination work |
| `BugzillaConnector` | Red Hat Bugzilla | Deferred — pending access and indexing pipeline |

## Documentation

| Document | Description |
|----------|-------------|
| [CREDENTIALS.md](CREDENTIALS.md) | Step-by-step credential setup for all sources |
| [SPEC.md](SPEC.md) | MCP platform contract and entity schema |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Architecture, source tree, maintainer guide |
| [BOB_INTEGRATION_GUIDE.md](BOB_INTEGRATION_GUIDE.md) | REST API reference, agent integration, deployment |
| [examples/agent_integration.py](examples/agent_integration.py) | Python client, LangChain/CrewAI tools |

## Development

```bash
pip install -e ".[dev]"
pytest
```

See [DEVELOPMENT.md](DEVELOPMENT.md) for architecture details and contributing.
