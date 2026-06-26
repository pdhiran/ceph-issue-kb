# Ceph Issue Intelligence KB

Issue Intelligence Knowledge Base for Ceph engineering. Indexes issues from Ceph Tracker (Redmine), IBM Ceph JIRA, Red Hat Bugzilla, and Red Hat KB into a unified, searchable knowledge base. Answers: **"Has this problem been seen before?"**

## Quick Start

```bash
# Install
pip install -e .

# Fetch and index issues (Ceph Tracker — public, no auth required)
python3 index_issues.py --connector ceph-tracker --verbose

# With all sources (requires credentials)
export JIRA_USERNAME=your_user JIRA_API_TOKEN=your_token
export BUGZILLA_API_KEY=your_key
export RH_SSO_COOKIE=your_cookie
python3 index_issues.py --config connectors.yaml --since 2024-01-01 --verbose
```

## Architecture

- **Connector framework**: Plugin-based — each issue source implements `BaseConnector`. Adding a new source = one class + a config entry
- **Signal extraction**: Automatically extracts stacktraces, assertions, health warnings, Ceph commands, config params, and log snippets from issue text
- **Common schema**: Every issue from every source normalizes to `NormalizedIssue` with extracted signals
- **Two-tier search**: BM25 keyword match (exact error messages) + fastembed semantic search (conceptual similarity)
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

Restart Cursor. The MCP server starts automatically.

---

**Claude Desktop** — start the server, then add to `claude_desktop_config.json`:

```bash
python3 -m ceph_issue_kb.server.mcp_server --transport sse --port 8080
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
python3 -m ceph_issue_kb.server.mcp_server --transport sse --port 8080
```

Connect to `http://localhost:8080/sse` in the tool's MCP settings.

---

**IBM watsonx / IBM Bob / LangChain / CrewAI / CI pipelines** — use the REST API (Phase 4):

```bash
python3 -m ceph_issue_kb.server.rest_api --host 0.0.0.0 --port 8200
```

```bash
# Check if an error is known
curl -X POST http://localhost:8200/api/is_known_issue \
  -H "Content-Type: application/json" \
  -d '{"error_message": "FAILED ceph_assert(googly > 0)", "version": "19.2.0"}'

# Search issues
curl -X POST http://localhost:8200/api/search_issues \
  -H "Content-Type: application/json" \
  -d '{"query": "OSD slow ops", "component": "rados"}'

# Find workaround
curl -X POST http://localhost:8200/api/find_workaround \
  -H "Content-Type: application/json" \
  -d '{"query": "too many PGs per OSD"}'

# Health check
curl http://localhost:8200/health
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
