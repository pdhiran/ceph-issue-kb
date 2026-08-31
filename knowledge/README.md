# Issue index

The searchable issue index (`issues-2024-2025/` and related files) is **not** stored in git.

It is published as a GitHub Release asset (tag `knowledge`) and downloaded automatically when the MCP server or REST API starts (`ensure_knowledge`). A running MCP hot-reloads after `./update_index.sh` (`.reload_trigger`) or a new Release tarball — Cursor does not need a restart. See [UPDATING.md](../UPDATING.md).

- **Consumer:** start the MCP or REST server once. It downloads `knowledge.tar.gz` from the GitHub Release. No JIRA tokens. `--no-auto-update` skips this download **and** the `.reload_trigger` watcher.
- **Maintainer:** `./update_index.sh` (index + publish, then writes `.last_index_update`) or `./update_index.sh --publish-only` (upload current `knowledge/` without fetching; does **not** advance `.last_index_update`)
