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
    from mcp.types import Icon

    ceph_icon = Icon(
        src="data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCA0OCA0OCIgd2lkdGg9IjQ4IiBoZWlnaHQ9IjQ4Ij48Y2lyY2xlIGN4PSIyNCIgY3k9IjI0IiByPSIyMiIgZmlsbD0iI0VGNTAzQSIvPjx0ZXh0IHg9IjI0IiB5PSIzMiIgZm9udC1mYW1pbHk9IkFyaWFsLHNhbnMtc2VyaWYiIGZvbnQtc2l6ZT0iMjAiIGZvbnQtd2VpZ2h0PSJib2xkIiBmaWxsPSJ3aGl0ZSIgdGV4dC1hbmNob3I9Im1pZGRsZSI+STwvdGV4dD48L3N2Zz4=",
        mimeType="image/svg+xml",
    )

    mcp = FastMCP(
        "Ceph Issue Intelligence KB",
        instructions=(
            "Engineering issue intelligence for Ceph storage. "
            "Use this MCP when investigating test failures, debugging clusters, "
            "or searching for known issues, workarounds, and fixes across "
            "JIRA, Ceph Tracker, Red Hat Bugzilla, and Red Hat KB. "
            "Key tools: search_issues, get_issue, find_similar_issue, is_known_issue, "
            "find_workaround, search_stacktrace, search_health_warning."
        ),
        icons=[ceph_icon],
    )

    @mcp.tool()
    def get_issue(issue_id: str) -> dict[str, Any]:
        """Get full issue details including description, all comments, stacktraces, and relationships.

        Use after search_issues to read the complete issue. Pass an entity_id
        (16-char hex) or a source_id (e.g. "IBMCEPH-16205").
        """
        return kb.get_issue(issue_id)

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


def _silence_stderr_logging() -> None:
    """Suppress all logging to stderr for stdio transport.

    Cursor classifies any stderr output as [error] in the MCP output panel,
    making the server appear broken even when healthy.
    """
    logging.disable(logging.CRITICAL)
    for handler in logging.root.handlers[:]:
        logging.root.removeHandler(handler)
    logging.root.addHandler(logging.NullHandler())


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
        default=8083,
        help="Port for SSE transport (default: 8083)",
    )
    parser.add_argument(
        "--auto-update",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Auto-update index from GitHub Releases (default: enabled)",
    )
    parser.add_argument(
        "--update-interval",
        type=float,
        default=12,
        metavar="HOURS",
        help="Hours between periodic update checks (default: 12, 0=disable periodic)",
    )
    args = parser.parse_args(argv)

    if args.transport == "stdio":
        _silence_stderr_logging()

    kb_dir = _find_kb_path(args.kb_path)
    if kb_dir is None and args.auto_update:
        from ceph_issue_kb.server.auto_update import ensure_knowledge
        kb_dir = ensure_knowledge(Path.cwd())
    if kb_dir:
        logger.info("Loading knowledge base from %s", kb_dir)
        kb = KnowledgeBase.load(kb_dir)
    else:
        logger.warning("No knowledge base found — server will report degraded health")
        kb = KnowledgeBase.empty()

    if args.auto_update:
        from ceph_issue_kb.server.auto_update import start_auto_update
        # kb_dir is None when the first Release download failed — still watch
        # the repo so a later periodic check / .reload_trigger can load it.
        start_auto_update(
            kb, kb_dir or Path.cwd() / "knowledge",
            update_interval_hours=args.update_interval,
        )

    mcp = create_mcp_server(kb)

    if args.transport == "sse":
        mcp.settings.port = args.port

    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
