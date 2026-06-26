# Ceph Issue Intelligence KB

Searchable knowledge base of **14,600+ Ceph issues** from IBM Ceph JIRA and Red Hat KB. Ships pre-built — clone, install, and immediately search known issues, workarounds, and fixes from your AI agent.

## Setup

### 1. Clone and install

```bash
git clone https://github.com/pdhiran/ceph-issue-kb.git
cd ceph-issue-kb
pip install -e .
```

### 2. Connect your agent

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

Restart Cursor. The MCP server starts automatically and pulls the latest issue data on startup — no manual `git pull` needed.

To disable auto-update, add `"--no-auto-update"` to `args`.

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

**IBM Bob** — Bob supports MCP over SSE natively:

```bash
python -m ceph_issue_kb.server.mcp_server --transport sse --host 0.0.0.0 --port 8083
```

Add to Bob's `.bob/mcp.json`:

```json
{
  "mcpServers": {
    "ceph-issue-kb": {
      "url": "http://localhost:8083/sse",
      "transport": "sse"
    }
  }
}
```

If running on a shared server, replace `localhost` with the hostname.

---

**REST API** — for LangChain, CrewAI, or CI pipelines:

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

# Health check
curl http://localhost:8200/health
```

See [BOB_INTEGRATION_GUIDE.md](BOB_INTEGRATION_GUIDE.md) for the full REST API reference and agent integration examples.

### 3. Use it

Once connected, agents automatically check for known Ceph issues. You can also ask directly:

- *"Is `FAILED ceph_assert(googly > 0)` a known issue?"*
- *"Find workarounds for OSD slow ops during recovery"*
- *"Search for issues related to HEALTH_WARN too many PGs per OSD"*
- *"What are the hot issues in the rgw component?"*
- *"Find issues with this stacktrace: `#0 in BlueStore::_do_write`"*

## Available Tools

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

## Active Sources

| Source | Issues | Description |
|--------|--------|-------------|
| IBM Ceph JIRA | 14,037 | Internal Ceph bug tracker and feature requests |
| Red Hat KB | 600 | Customer-facing knowledge base articles |

The knowledge base is updated daily by the maintainer with the latest bugs and articles. Your MCP server automatically pulls new data every 12 hours — no action needed on your part.

## VS Code Extension

Coming soon. See [ceph-doc-kb](https://github.com/pdhiran/ceph-doc-kb) and [ceph-command-kb](https://github.com/pdhiran/ceph-command-kb) for existing VS Code extensions that complement this knowledge base.

## Documentation

| Document | Description |
|----------|-------------|
| [SPEC.md](SPEC.md) | MCP platform contract and entity schema |
| [DEVELOPMENT.md](DEVELOPMENT.md) | Architecture, source tree, maintainer guide |
| [CREDENTIALS.md](CREDENTIALS.md) | Credential setup for re-indexing sources |
| [BOB_INTEGRATION_GUIDE.md](BOB_INTEGRATION_GUIDE.md) | REST API reference, agent integration, deployment |

## Development

```bash
pip install -e ".[dev]"
pytest
```

See [DEVELOPMENT.md](DEVELOPMENT.md) for architecture details and contributing.
