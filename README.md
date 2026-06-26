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

## MCP Server

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

### Tools

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
1. search_health_warning("too many PGs per OSD")         ← Issue KB
2. search_docs("PG autoscaler", component="rados")       ← Doc KB
3. verify_config("mon_max_pg_per_osd")                   ← Command KB
4. find_workaround("too many PGs per OSD")               ← Issue KB
5. Synthesizes: known issue, doc reference, config fix, prior workarounds
```

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

## Development

```bash
pip install -e ".[dev]"
pytest
```

See [DEVELOPMENT.md](DEVELOPMENT.md) for architecture details and contributing.
