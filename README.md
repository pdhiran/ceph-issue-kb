# Ceph Issue Intelligence KB

Searchable knowledge base of **18,000+ Ceph issues** from IBM Ceph JIRA and Red Hat Knowledge Base. Clone, install, start the MCP — it downloads the pre-built index from GitHub Releases on first run.

Use this MCP when investigating **failures, crashes, HEALTH_WARN, stacktraces, workarounds, and duplicates**. Do not use it to invent CLI (that is **ceph-cmd-kb**) or to walk IBM upgrade docs (that is **ceph-doc-kb**).

## For agents (read this first)

| Do | Do not |
|---|---|
| `is_known_issue` on a raw assert / traceback before filing a bug | File a new JIRA without searching here first |
| `search_stacktrace` / `search_health_warning` for those shapes | Paste huge logs into `search_issues` — extract the assert or backtrace first |
| `get_issue` after you have an `entity_id` or `IBMCEPH-xxxxx` | Treat “no hit” as “not a product bug” — the index lags JIRA by up to a day |
| `find_workaround` / `find_fix` when the user needs a path forward | Use this for prio-list *tracking* workflow — **ceph-prio-hub** |

**Typical first calls**

1. `health()` — confirm index loaded (issue counts per source).
2. Exact error → `is_known_issue(error_message="...", version="19.2.0")`.
3. Fuzzy problem → `find_similar_issue(description="...", component="rados")`.
4. Then `get_issue(issue_id)` for comments, stacktraces, links.

Pass `version` on `is_known_issue` / `search_issues` when the cluster version is known (filters affected versions).

The served index is the GitHub Release asset, not git. Maintainers publish with `./update_index.sh`. Clients auto-download on MCP start.

## Ceph Engineering Intelligence Platform

| MCP | Cursor key | Use when | SSE | REST |
|-----|------------|----------|-----|------|
| **ceph-cmd-kb** | `ceph-cmd-kb` | Verify CLI, flags, configs | 8081 | 9090 |
| **ceph-doc-kb** | `ceph-doc-kb` | How-to, architecture, IBM procedures | 8082 | 8100 |
| **ceph-issue-kb** | `ceph-issue-kb` | Known bugs, workarounds, stacktraces | 8083 | 8200 |
| **ceph-prio-hub** | `ceph-prio-hub` | Customer prio-list / L3 tracking | 8080 | — |
| **cephci-kb** | `cephci-kb` | CephCI code, tests, workflows | 8084 | — |

After a hit here, verify any suggested CLI with **ceph-cmd-kb** and read procedure from **ceph-doc-kb**. For L3 customer tracking, hand off to **ceph-prio-hub**.

## Setup

```bash
git clone https://github.com/pdhiran/ceph-issue-kb.git
cd ceph-issue-kb
pip install -e ".[search,server]"
```

First MCP/REST start downloads `knowledge.tar.gz` from the `knowledge` GitHub Release if `knowledge/issues-2024-2025/` is missing.

Re-indexing from JIRA/RHKB (maintainers only) needs credentials — [CREDENTIALS.md](CREDENTIALS.md). Serving does **not**.

## Incorporate into an agent

### Cursor (stdio)

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

Optional: `"--kb-path", "/path/to/ceph-issue-kb/knowledge/issues-2024-2025"` if auto-detect is wrong.

`"--no-auto-update"` skips **all** of: first-run Release download (`ensure_knowledge`), periodic `git pull` + Release refresh, and the `.reload_trigger` watcher. Default interval is **12 hours** on MCP, REST, and `start_auto_update` (cmd-kb and doc-kb default to 1h; this tarball is large). `"--update-interval", "0"` is startup check only (trigger still watched). Override example: `"--update-interval", "6"`.

Restart Cursor after editing `mcp.json`.

### SSE

```bash
python3 -m ceph_issue_kb.server.mcp_server --transport sse --port 8083
```

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

### REST

```bash
python3 -m ceph_issue_kb.server.rest_api --host 0.0.0.0 --port 8200
```

```bash
curl -X POST http://localhost:8200/api/search_issues \
  -H "Content-Type: application/json" \
  -d '{"query": "OSD slow ops", "component": "rados"}'

curl -X POST http://localhost:8200/api/is_known_issue \
  -H "Content-Type: application/json" \
  -d '{"error_message": "FAILED ceph_assert(googly > 0)", "version": "19.2.0"}'

curl -X POST http://localhost:8200/api/find_workaround \
  -H "Content-Type: application/json" \
  -d '{"query": "too many PGs per OSD"}'

curl http://localhost:8200/health
```

Full REST and agent wrappers: [BOB_INTEGRATION_GUIDE.md](BOB_INTEGRATION_GUIDE.md).

## Tool catalog

| Tool | Args | When to call |
|------|------|----------------|
| `search_issues` | `query`, optional `source`, `component`, `version`, `status`, `limit=10` | Broad search. `source`: connector name (e.g. ibm-jira). |
| `get_issue` | `issue_id` (`entity_id` hex or `IBMCEPH-16205`) | Full description + comments |
| `find_similar_issue` | `description`, optional `stacktrace`, `component` | Semantic similar-bug |
| `is_known_issue` | `error_message`, optional `version` | Exact-ish known-issue check |
| `find_workaround` | `query` (issue id or text) | Known workarounds |
| `find_fix` | `query` | Commits / PRs / fix notes |
| `find_related_issues` | `issue_id` | Duplicate / linked |
| `search_stacktrace` | `stacktrace` | Backtrace similarity |
| `search_health_warning` | `warning` | HEALTH_WARN / HEALTH_ERR text |
| `hot_issues` | optional `component`, `limit=10` | Recently updated |
| `component_health` | `component` | Open criticals / regressions / blockers |
| `capabilities` | (none) | Sources and entity types |
| `health` | (none) | Connector/index counts |

### Active sources

| Source | Role |
|--------|------|
| IBM Ceph JIRA (`ibm-jira`) | Internal tracker (majority of the index) |
| Red Hat KB (`redhat-kb`) | Customer-facing articles |

Connectors exist for Ceph Tracker (Redmine) and Bugzilla but are **disabled** in `connectors.yaml` until those pipelines are turned on.

### Agent workflow: crash during a test

1. Extract assert line or `#0` frames from the log — do not dump the whole journal.
2. `is_known_issue(error_message=<assert>, version=<cluster>)`
3. If weak: `search_stacktrace(stacktrace=<frames>)` then `find_similar_issue`.
4. `get_issue` on the best `source_id`.
5. `find_workaround` / `find_fix` if the user needs a path.
6. If filing IBMCEPH: still follow the bug-filing skill; mention this hit as related.

### Agent workflow: HEALTH_WARN

1. `search_health_warning(warning="too many PGs per OSD")`
2. `find_workaround` on the same text.
3. Confirm remediation CLI with **ceph-cmd-kb**.

## Updating the knowledge base

Same `--since YYYY-MM-DD` contract used by the other KBs. This repo is the original: fetch issues **updated since that date**, **merge** by `entity_id`, rebuild BM25 + FAISS.

```bash
# Maintainer: credentials in .env (JIRA_USERNAME, JIRA_API_TOKEN, RH_OFFLINE_TOKEN)
python3 index_issues.py --config connectors.yaml --since 2026-08-01 --verbose

# Single connector
python3 index_issues.py --connector ibm-jira --since 2026-08-01 --verbose

# Full rebuild (overwrites; do not use for daily delta)
python3 index_issues.py --full-rebuild --verbose

# Smart wrapper: last-run tracker + pack + GitHub Release upload
./update_index.sh                 # since last successful run, or last 1 day
./update_index.sh 7               # last 7 days
./update_index.sh 2026-08-01      # explicit date
./update_index.sh --reset
./update_index.sh --publish-only  # upload current knowledge/ without fetching; does not advance .last_index_update
```

`./update_index.sh` refuses to publish if the issue count is below a floor (avoids shipping an empty incremental merge). After a successful **index + publish** it writes `.last_index_update` (1-day overlap) and touches `.reload_trigger` so a running MCP hot-reloads **without restarting Cursor**. `--publish-only` touches `.reload_trigger` but **does not** write `.last_index_update`, so the next incremental `--since` is not skipped.

MCP servers that only **consume** the Release do not need JIRA tokens; they pick up new tarballs on auto-update (`git pull` + Release download). A `.py` change exits the MCP subprocess so Cursor respawns it.

Full maintainer help: [UPDATING.md](UPDATING.md).

Layout: `knowledge/issues-2024-2025/<source>/issues.json` plus shared BM25/FAISS artefacts. Not stored in git — see [knowledge/README.md](knowledge/README.md).

## Architecture

```
JIRA / RHKB connectors → normalize + sanitize → merge by entity_id
        → BM25 + per-source FAISS → knowledge/
                → MCP / REST (KnowledgeBase facade)
```

See [DEVELOPMENT.md](DEVELOPMENT.md), [SPEC.md](SPEC.md), [CREDENTIALS.md](CREDENTIALS.md).

## Development

```bash
pip install -e ".[all]"
pytest
```
