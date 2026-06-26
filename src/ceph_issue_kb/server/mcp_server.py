"""MCP server for the Ceph Issue Intelligence Knowledge Base.

Uses FastMCP from the ``mcp`` SDK.  Exposes all domain-specific tools
defined in SPEC.md.

Run with::

    python -m ceph_issue_kb.server.mcp_server                    # stdio (Cursor)
    python -m ceph_issue_kb.server.mcp_server --transport sse     # SSE  (Claude Desktop / network)
    python -m ceph_issue_kb.server.mcp_server --kb-path /data/kb  # custom KB path
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path
from typing import Any

from mcp.server.fastmcp import FastMCP

from ceph_issue_kb.server.kb import KnowledgeBase

logger = logging.getLogger(__name__)


def _find_kb_path(explicit: str | None = None) -> Path | None:
    """Resolve the knowledge-base directory, checking common locations.

    Returns a directory that either:
    - Contains ``issues.json`` directly (single-source), or
    - Contains subdirectories with ``issues.json`` (multi-source).
    """
    candidates = [
        Path(explicit) if explicit else None,
        Path("knowledge"),
        Path(__file__).resolve().parents[3] / "knowledge",
    ]
    for p in candidates:
        if p is None or not p.is_dir():
            continue

        # If this directory itself has issues.json, use it directly.
        if (p / "issues.json").exists():
            return p

        # Check for multi-source layout: subdirectories with issues.json.
        source_dirs = [
            sub for sub in sorted(p.iterdir())
            if sub.is_dir() and (sub / "issues.json").exists()
        ]
        if source_dirs:
            return p

        # Check one level deeper (e.g., knowledge/ -> issues-2024-2025/).
        subdirs = sorted(p.iterdir())
        for sub in reversed(subdirs):
            if not sub.is_dir():
                continue
            if (sub / "issues.json").exists():
                return sub
            inner_sources = [
                s for s in sorted(sub.iterdir())
                if s.is_dir() and (s / "issues.json").exists()
            ]
            if inner_sources:
                return sub

    return None


def create_mcp_server(kb: KnowledgeBase) -> FastMCP:
    """Build and return a FastMCP server wired to *kb*.

    Separated from ``main()`` so tests can create a server with a mock KB.
    """
    mcp = FastMCP("ceph-issue-kb")

    @mcp.tool()
    def search_issues(
        query: str,
        source: str | None = None,
        component: str | None = None,
        version: str | None = None,
        status: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Search issues across all sources with optional filters."""
        return kb.search_issues(
            query, source=source, component=component,
            version=version, status=status, limit=limit,
        )

    @mcp.tool()
    def find_similar_issue(
        description: str,
        stacktrace: str | None = None,
        component: str | None = None,
    ) -> dict[str, Any]:
        """Find issues similar to a given problem description."""
        return kb.find_similar_issue(
            description, stacktrace=stacktrace, component=component,
        )

    @mcp.tool()
    def is_known_issue(
        error_message: str,
        version: str | None = None,
    ) -> dict[str, Any]:
        """Check if an error message matches a known open issue."""
        return kb.is_known_issue(error_message, version=version)

    @mcp.tool()
    def find_workaround(query: str) -> dict[str, Any]:
        """Search for known workarounds (pass an issue ID or free-text query)."""
        return kb.find_workaround(query)

    @mcp.tool()
    def find_fix(query: str) -> dict[str, Any]:
        """Search for known fixes, commits, and PRs (pass an issue ID or free-text query)."""
        return kb.find_fix(query)

    @mcp.tool()
    def find_related_issues(issue_id: str) -> dict[str, Any]:
        """Get related, duplicate, or linked issues."""
        return kb.find_related_issues(issue_id)

    @mcp.tool()
    def search_stacktrace(stacktrace: str) -> dict[str, Any]:
        """Find issues with similar stacktraces."""
        return kb.search_stacktrace(stacktrace)

    @mcp.tool()
    def search_health_warning(warning: str) -> dict[str, Any]:
        """Find issues related to a Ceph health warning."""
        return kb.search_health_warning(warning)

    @mcp.tool()
    def hot_issues(
        component: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Most recently updated issues, optionally filtered by component."""
        return kb.hot_issues(component=component, limit=limit)

    @mcp.tool()
    def component_health(component: str) -> dict[str, Any]:
        """Open criticals, regressions, and blockers for a Ceph component."""
        return kb.component_health(component)

    @mcp.tool()
    def capabilities() -> dict[str, Any]:
        """Server capabilities: entity types, operations, and sources."""
        return kb.capabilities()

    @mcp.tool()
    def health() -> dict[str, Any]:
        """Index status and connectivity health check."""
        return kb.health()

    return mcp


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Ceph Issue KB MCP server")
    parser.add_argument(
        "--kb-path",
        default=None,
        help="Path to the knowledge base directory (default: auto-detect)",
    )
    parser.add_argument(
        "--transport",
        default="stdio",
        choices=["stdio", "sse"],
        help="MCP transport (default: stdio)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for SSE transport (default: 8080)",
    )
    parser.add_argument(
        "--auto-update",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Auto-pull latest knowledge base from git on startup (default: enabled)",
    )
    parser.add_argument(
        "--update-interval",
        type=float,
        default=12,
        metavar="HOURS",
        help="Hours between periodic KB update checks (default: 12, 0=disable periodic)",
    )
    args = parser.parse_args(argv)

    kb_dir = _find_kb_path(args.kb_path)
    if kb_dir:
        logger.info("Loading knowledge base from %s", kb_dir)
        kb = KnowledgeBase.load(kb_dir)
    else:
        logger.warning("No knowledge base found — server will report degraded health")
        kb = KnowledgeBase.empty()

    if args.auto_update:
        from ceph_issue_kb.server.auto_update import start_auto_update
        start_auto_update(
            kb, kb_dir,
            update_interval_hours=args.update_interval,
        )

    mcp = create_mcp_server(kb)

    if args.transport == "sse":
        mcp.settings.port = args.port

    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
