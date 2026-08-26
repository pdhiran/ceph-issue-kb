# Issue index

The searchable issue index (`issues-2024-2025/` and related files) is **not** stored in git.

It is published as a GitHub Release asset (tag `knowledge`) and downloaded automatically when the MCP server or REST API starts.

- Maintainer: `./update_index.sh` (index + publish) or `./update_index.sh --publish-only`
- Manual download: start the MCP server once, or run `./update_index.sh --publish-only` after an index build
