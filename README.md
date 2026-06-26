# Ceph Issue Intelligence KB

Issue Intelligence Knowledge Base for Ceph engineering. Indexes issues from Ceph Tracker (Redmine), IBM Ceph JIRA, Red Hat Bugzilla, and Red Hat KB into a unified, searchable knowledge base. Answers: **"Has this problem been seen before?"**

## Quick Start

```bash
# Install (with dev dependencies for testing)
pip install -e ".[dev]"

# Run tests
pytest
```

Use the connector framework programmatically:

```python
from ceph_issue_kb.config import load_config
from ceph_issue_kb.connectors import get_connector

config = load_config("connectors.yaml")
connector = get_connector(config.connectors["ceph-tracker"])
connector.authenticate()

issue = connector.fetch("68051")
print(issue.source, issue.source_id, issue.data.get("subject"))
```

> **Note:** The MCP server, REST API, and `index_issues.py` CLI are coming in later phases. Phase 1 provides the connector framework, models, config loading, and signal extraction.

## Architecture

- **Connector framework**: Plugin-based — each issue source implements `BaseConnector`. Adding a new source = one class + a config entry
- **Signal extraction**: Automatically extracts stacktraces, assertions, health warnings, Ceph commands, config params, and log snippets from issue text
- **Common schema**: Every issue from every source normalizes to `NormalizedIssue` with extracted signals
- **Two-tier search**: BM25 keyword match (exact error messages) + fastembed semantic search (conceptual similarity)
- **Per-source storage**: Each connector's issues stored separately for scalability

## Connect Your Agent (Coming in Phase 4)

> **Note:** The MCP server and REST API are not yet implemented. The configuration below is a preview of what's coming in Phase 4. Today, use the connector framework programmatically (see Quick Start above).

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

```json
{
  "mcpServers": {
    "ceph-issue-kb": { "url": "http://localhost:8080/sse" }
  }
}
```

---

**IBM watsonx / IBM Bob / LangChain / CrewAI / CI pipelines** — use the REST API:

```bash
curl -X POST http://localhost:8200/api/search_issues \
  -H "Content-Type: application/json" \
  -d '{"query": "OSD slow ops", "component": "rados"}'
```

**Additional integration guides:**
- [BOB_INTEGRATION_GUIDE.md](BOB_INTEGRATION_GUIDE.md) — REST API reference, agent integration, deployment options
- [examples/agent_integration.py](examples/agent_integration.py) — Ready-to-use Python client, LangChain/CrewAI tools

> **VS Code Extension**: A VS Code extension for interactive issue search is planned for a future phase. See the [ceph-command-kb VS Code extension](https://github.com/pdhiran/ceph-command-kb/tree/main/vscode-extension) for the pattern.

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
1. search_health_warning("too many PGs per OSD")         <- Issue KB
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

| Connector | Source | Auth | Status |
|-----------|--------|------|--------|
| `RedmineConnector` | [Ceph Tracker](https://tracker.ceph.com) | None (public) | Phase 1 |
| `JiraConnector` | IBM Ceph JIRA | API token | Phase 2 |
| `BugzillaConnector` | Red Hat Bugzilla | API key | Phase 2 |
| `RHKBConnector` | Red Hat KB | Cookie/session | Phase 2 |

## Documentation

| Document | Description |
|----------|-------------|
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
