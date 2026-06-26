"""Ready-made Python client for the ceph-issue-kb REST API.

Usage:
    from examples.agent_integration import CephIssueKBClient

    client = CephIssueKBClient("http://localhost:8200")
    result = client.is_known_issue("FAILED ceph_assert(googly > 0)")
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from urllib.error import URLError


class CephIssueKBClient:
    """Client for the ceph-issue-kb REST API. No external dependencies required."""

    def __init__(self, base_url: str = "http://localhost:8200", timeout: int = 30):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _post(self, path: str, payload: dict[str, Any]) -> Any:
        url = f"{self.base_url}{path}"
        data = json.dumps(payload).encode()
        req = Request(url, data=data, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        if params:
            filtered = {k: v for k, v in params.items() if v is not None}
            if filtered:
                url += "?" + urlencode(filtered)
        req = Request(url)
        with urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())

    # --- Health and discovery ---

    def health(self) -> dict:
        """Check server health, connector status, and index stats."""
        return self._get("/health")

    def capabilities(self) -> dict:
        """Get server capabilities and supported entity types."""
        return self._get("/capabilities")

    def is_healthy(self) -> bool:
        """Quick check if the server is reachable and index is loaded."""
        try:
            h = self.health()
            return h.get("status") == "ok"
        except (URLError, OSError):
            return False

    # --- Issue search ---

    def search_issues(
        self,
        query: str,
        source: str | None = None,
        component: str | None = None,
        version: str | None = None,
        status: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Search issues across all sources with optional filters."""
        data = self._post("/api/search_issues", {
            "query": query,
            "source": source,
            "component": component,
            "version": version,
            "status": status,
            "limit": limit,
        })
        return data.get("results", [])

    def find_similar_issue(
        self,
        description: str,
        stacktrace: str | None = None,
        component: str | None = None,
    ) -> list[dict]:
        """Find issues similar to a given problem description."""
        data = self._post("/api/find_similar_issue", {
            "description": description,
            "stacktrace": stacktrace,
            "component": component,
        })
        return data.get("results", [])

    def is_known_issue(
        self,
        error_message: str,
        version: str | None = None,
    ) -> dict:
        """Check if an error message matches a known issue."""
        return self._post("/api/is_known_issue", {
            "error_message": error_message,
            "version": version,
        })

    # --- Workarounds and fixes ---

    def find_workaround(self, issue_id_or_query: str) -> dict:
        """Find known workarounds for an issue (by ID or search query)."""
        return self._post("/api/find_workaround", {
            "query": issue_id_or_query,
        })

    def find_fix(self, issue_id_or_query: str) -> dict:
        """Find fixes, commits, PRs for an issue."""
        return self._post("/api/find_fix", {
            "query": issue_id_or_query,
        })

    # --- Signal-specific search ---

    def search_stacktrace(self, stacktrace: str) -> list[dict]:
        """Find issues with similar stacktraces."""
        data = self._post("/api/search_stacktrace", {
            "stacktrace": stacktrace,
        })
        return data.get("results", [])

    def search_health_warning(self, warning: str) -> list[dict]:
        """Find issues related to a specific health warning."""
        data = self._post("/api/search_health_warning", {
            "warning": warning,
        })
        return data.get("results", [])

    # --- Relationships ---

    def find_related_issues(self, issue_id: str) -> list[dict]:
        """Get related, duplicate, and linked issues."""
        data = self._post("/api/find_related_issues", {
            "issue_id": issue_id,
        })
        return data.get("results", [])

    # --- Component-level views ---

    def hot_issues(
        self,
        component: str | None = None,
        limit: int = 10,
    ) -> list[dict]:
        """Get the most active recent issues, optionally filtered by component."""
        data = self._get("/api/hot_issues", {
            "component": component,
            "limit": limit,
        })
        return data.get("results", [])

    def component_health(self, component: str) -> dict:
        """Get open criticals, regressions, and blockers for a component."""
        return self._get(f"/api/component_health/{component}")


# --- Convenience functions for agent frameworks ---

def make_langchain_tools(base_url: str = "http://localhost:8200"):
    """Create LangChain-compatible tool functions.

    Returns a list of LangChain Tool objects. Requires: pip install langchain
    """
    try:
        from langchain.tools import Tool
    except ImportError:
        raise ImportError("langchain not installed. Install with: pip install langchain")

    client = CephIssueKBClient(base_url)

    def is_known_issue_wrapper(error_message: str) -> str:
        result = client.is_known_issue(error_message)
        return json.dumps(result, indent=2)

    def search_issues_wrapper(query: str) -> str:
        results = client.search_issues(query, limit=5)
        if not results:
            return "No matching issues found."
        parts = []
        for r in results:
            parts.append(
                f"## {r['title']}\n"
                f"Source: {r['source']} ({r['source_url']})\n"
                f"Status: {r['status']} | Priority: {r['priority']}\n\n"
                f"{r.get('summary', '')[:300]}..."
            )
        return "\n\n---\n\n".join(parts)

    def find_workaround_wrapper(query: str) -> str:
        result = client.find_workaround(query)
        return json.dumps(result, indent=2)

    def search_health_warning_wrapper(warning: str) -> str:
        results = client.search_health_warning(warning)
        if not results:
            return f"No issues found for health warning: {warning}"
        lines = [f"Issues matching '{warning}':"]
        for r in results:
            lines.append(f"- [{r['priority']}] {r['title']} ({r['source_url']})")
        return "\n".join(lines)

    return [
        Tool(
            name="CheckKnownCephIssue",
            func=is_known_issue_wrapper,
            description=(
                "Check if a Ceph error message matches a known issue. "
                "Input: the error message or assertion text. "
                "Returns matching issue with status and links."
            ),
        ),
        Tool(
            name="SearchCephIssues",
            func=search_issues_wrapper,
            description=(
                "Search for known Ceph issues by keyword, error, or component. "
                "Input: search query. Returns matching issues with summaries."
            ),
        ),
        Tool(
            name="FindCephWorkaround",
            func=find_workaround_wrapper,
            description=(
                "Find workarounds for a known Ceph issue. "
                "Input: issue ID or search query. Returns resolution steps."
            ),
        ),
        Tool(
            name="SearchCephHealthWarning",
            func=search_health_warning_wrapper,
            description=(
                "Find issues related to a Ceph HEALTH_WARN or HEALTH_ERR code. "
                "Input: the health warning text. Returns matching issues."
            ),
        ),
    ]


def make_crewai_tools(base_url: str = "http://localhost:8200"):
    """Create CrewAI tool functions.

    Returns a list of CrewAI tool-decorated functions. Requires: pip install crewai-tools
    """
    try:
        from crewai_tools import tool
    except ImportError:
        raise ImportError("crewai-tools not installed. Install with: pip install crewai-tools")

    client = CephIssueKBClient(base_url)

    @tool("Check Known Ceph Issue")
    def check_known_issue(error_message: str) -> str:
        """Check if a Ceph error message matches a known issue in the tracker."""
        result = client.is_known_issue(error_message)
        return json.dumps(result, indent=2)

    @tool("Search Ceph Issues")
    def search_issues(query: str) -> str:
        """Search for known Ceph issues by keyword or error message."""
        results = client.search_issues(query, limit=5)
        return json.dumps(results, indent=2)

    @tool("Find Ceph Workaround")
    def find_workaround(query: str) -> str:
        """Find workarounds for a known Ceph issue."""
        result = client.find_workaround(query)
        return json.dumps(result, indent=2)

    @tool("Search Ceph Health Warning")
    def search_health_warning(warning: str) -> str:
        """Find issues related to a Ceph health warning code."""
        results = client.search_health_warning(warning)
        return json.dumps(results, indent=2)

    return [check_known_issue, search_issues, find_workaround, search_health_warning]


if __name__ == "__main__":
    client = CephIssueKBClient()

    if not client.is_healthy():
        print("ERROR: Cannot connect to ceph-issue-kb REST API at http://localhost:8200")
        print("Start it with: python3 -m ceph_issue_kb.server.rest_api")
        raise SystemExit(1)

    h = client.health()
    print(f"Connected: {h.get('total_issues', 0)} issues indexed")
    print(f"Connectors: {list(h.get('connectors', {}).keys())}")
    print()

    print("Check known issue: 'FAILED ceph_assert(googly > 0)'")
    result = client.is_known_issue("FAILED ceph_assert(googly > 0)")
    print(f"  Known: {result.get('known', False)}")
    if result.get("issue"):
        print(f"  Match: {result['issue']['title']}")
    print()

    print("Search: 'OSD slow ops during recovery'")
    results = client.search_issues("OSD slow ops during recovery", component="rados", limit=3)
    for r in results:
        print(f"  [{r.get('priority', '?')}] {r['title']} ({r['source']})")
    print()

    print("Component health: 'rgw'")
    health = client.component_health("rgw")
    print(f"  Open criticals: {health.get('open_criticals', 0)}")
    print(f"  Regressions: {health.get('regressions', 0)}")
