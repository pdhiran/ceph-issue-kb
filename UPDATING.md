# Updating the issue knowledge base

This is the maintainer help for **ceph-issue-kb**. Agents and humans: use this page when rebuilding or refreshing the served index. Do not invent a different workflow.

The **served** index is the GitHub Release asset (`knowledge` tag, `knowledge.tar.gz`), not git LFS. Clients download it on MCP start and on the auto-update timer.

Cursor does **not** need a restart after an index update. The MCP hot-reloads BM25 + FAISS in-process, or (only if `.py` files changed) Cursor respawns the MCP subprocess.

## Canonical command (maintainer)

Needs `.env` with `JIRA_USERNAME`, `JIRA_API_TOKEN`, and `RH_OFFLINE_TOKEN` (see [CREDENTIALS.md](CREDENTIALS.md)). Needs `gh` authenticated for publish.

```bash
cd /path/to/ceph-issue-kb
./update_index.sh                 # since last successful run, or last 1 day
./update_index.sh 7               # last 7 days
./update_index.sh 2026-08-01      # explicit ISO date
./update_index.sh --reset         # clear .last_index_update
./update_index.sh --publish-only  # pack + upload current knowledge/ (no fetch)
```

## What `./update_index.sh` does

1. Resolves `--since`.
2. Runs `python3 index_issues.py --config connectors.yaml --since DATE --verbose` (merge by `entity_id`, rebuild BM25 + FAISS).
3. Touches `.reload_trigger` (local MCP hot-reload).
4. Sanitizes, packs `knowledge/` → `knowledge.tar.gz`, uploads to GitHub Release `knowledge` (replaces the previous asset).
5. Advances `.last_index_update` **only after a successful publish**.

Refuses to publish if the issue count is below `MIN_ISSUES` (default 10000) so an empty incremental merge cannot ship.

## How a running MCP picks up the new index (no Cursor restart)

| Event | What the MCP does | Cursor |
|---|---|---|
| Maintainer `./update_index.sh` on **this** machine | Trigger watcher (~5s) reloads `knowledge/` from disk | Stays open |
| Consumer MCP (any machine) | Periodic `git pull` of code + download of Release tarball if ETag changed, then `kb.reload` | Stays open |
| `git pull` of any `*.py` | MCP `os._exit(0)`; Cursor respawns the subprocess | Stays open |
| No git remote (local-only) | Release download skipped; trigger watcher still runs so local `./update_index.sh` hot-reloads | Stays open |
| `knowledge/.indexing_lock` present | Release install waits / skips so it cannot clobber an in-progress index | — |

Default interval is **12 hours** (cmd-kb / doc-kb default to 1h; this Release tarball is large). Override: `--update-interval HOURS`. Disable: `--no-auto-update`.

MCP **consumers** do not need JIRA tokens.

### Cursor MCP config

```json
{
  "command": "python",
  "args": ["-m", "ceph_issue_kb.server.mcp_server", "--auto-update", "--update-interval", "12"]
}
```

## Manual indexer

```bash
python3 index_issues.py --config connectors.yaml --since 2026-08-01 --verbose
python3 index_issues.py --connector ibm-jira --since 2026-08-01 --verbose
python3 index_issues.py --full-rebuild --verbose   # overwrites; not for daily delta
touch .reload_trigger
```

Layout: `knowledge/issues-2024-2025/<source>/issues.json` plus shared BM25/FAISS artefacts. Not stored in git — see [knowledge/README.md](knowledge/README.md).

## Files that must stay untracked

`.reload_trigger`, `.last_index_update`, `.env`, and `knowledge/` contents (except README) stay out of git.

## Troubleshooting

| Symptom | Check |
|---|---|
| Local rebuild, MCP still old | Confirm trigger file in repo root; `--no-auto-update` not set |
| Consumer never gets new issues | Release upload succeeded? MCP can reach `github.com`? ETag / `.release_etag` |
| Publish refused | Issue count below `MIN_ISSUES`; restore `knowledge/` or `MIN_ISSUES=0` |
| Indexing lock stuck | Stale `knowledge/.indexing_lock` older than 6h is ignored |
