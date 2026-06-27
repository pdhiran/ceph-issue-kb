"""KnowledgeBase facade — shared logic for MCP server and REST API.

Every public method returns a JSON-serialisable ``dict``.  Both the MCP
server and the REST API delegate to this class so the business logic
lives in one place.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ceph_issue_kb.models import KNOWLEDGE_BASE, SCHEMA_VERSION, NormalizedIssue
from ceph_issue_kb.search.engine import SearchEngine
from ceph_issue_kb.search.similarity import SimilarityEngine, fingerprint


@dataclass(frozen=True)
class _KBState:
    """Immutable snapshot of search + similarity engines.

    A single assignment of ``self._state`` is GIL-atomic, so concurrent
    readers always see a consistent pair of engines.
    """
    search: SearchEngine
    similarity: SimilarityEngine

# ---------------------------------------------------------------------------
# Comment-scanning patterns
# ---------------------------------------------------------------------------

_WORKAROUND_RE = re.compile(
    r"workaround|worked?\s+around|temporary\s+fix|try\s+this"
    r"|as\s+a\s+workaround|in\s+the\s+meantime|quick\s+fix",
    re.IGNORECASE,
)

_FIX_RE = re.compile(
    r"fix(?:ed|es)?\s+(?:by|in|with)|commit\s+[0-9a-f]{7,}"
    r"|PR\s*#?\d+|pull\s+request|merged?\s+(?:to|in(?:to)?)"
    r"|backport|cherry.?pick"
    r"|https?://github\.com/.+/(?:pull|commit)/",
    re.IGNORECASE,
)


def _error_dict(message: str) -> dict[str, str]:
    return {"error": message, "status": "error"}


def _issue_summary(issue: NormalizedIssue) -> dict[str, Any]:
    """Concise, JSON-safe representation of an issue for API responses."""
    return {
        "entity_id": issue.entity_id,
        "entity_type": issue.entity_type,
        "source": issue.source,
        "source_id": issue.source_id,
        "source_url": issue.source_url,
        "title": issue.title,
        "summary": issue.summary,
        "status": issue.status,
        "priority": issue.priority,
        "components": issue.components,
        "affected_versions": issue.affected_versions,
        "fixed_versions": issue.fixed_versions,
        "health_warnings": issue.health_warnings,
        "assertions": issue.assertions,
        "created_at": issue.created_at,
        "updated_at": issue.updated_at,
    }


# ---------------------------------------------------------------------------
# KnowledgeBase
# ---------------------------------------------------------------------------


class KnowledgeBase:
    """High-level API consumed by MCP and REST servers."""

    _MAX_LIMIT = 200

    def __init__(
        self,
        search_engine: SearchEngine,
        similarity_engine: SimilarityEngine | None = None,
        kb_path: Path | None = None,
    ) -> None:
        self._state = _KBState(
            search=search_engine,
            similarity=similarity_engine or SimilarityEngine(search_engine),
        )
        self._kb_path = kb_path

    @classmethod
    def load(cls, kb_path: str | Path) -> "KnowledgeBase":
        """Load a knowledge base from a directory on disk."""
        path = Path(kb_path)
        engine = SearchEngine.load(path)
        return cls(search_engine=engine, kb_path=path)

    @classmethod
    def empty(cls) -> "KnowledgeBase":
        """Return an empty KnowledgeBase (no issues loaded)."""
        engine = SearchEngine()
        return cls(search_engine=engine)

    def reload(self, kb_path: str | Path) -> None:
        """Hot-reload the knowledge base from *kb_path*.

        Both engines are wrapped in a frozen ``_KBState`` so the swap is
        a single GIL-atomic assignment — concurrent readers always see a
        consistent pair.
        """
        path = Path(kb_path)
        engine = SearchEngine.load(path)
        sim = SimilarityEngine(engine)
        self._state = _KBState(search=engine, similarity=sim)
        self._kb_path = path

    # -- Search ---------------------------------------------------------------

    def search_issues(
        self,
        query: str,
        source: str | None = None,
        component: str | None = None,
        version: str | None = None,
        status: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        if not query.strip():
            return {"results": [], "total": 0, "query": query, "message": "Empty query"}
        limit = max(1, min(int(limit), self._MAX_LIMIT))
        state = self._state
        results = state.search.search(
            query, source=source, component=component, status=status, limit=limit * 2,
        )

        if version:
            results = self._filter_version(results, version)

        items = [
            {**_issue_summary(r.issue), "score": round(r.score, 4)}
            for r in results[:limit]
        ]
        return {"results": items, "total": len(items), "query": query}

    # -- Similarity -----------------------------------------------------------

    def find_similar_issue(
        self,
        description: str,
        stacktrace: str | None = None,
        component: str | None = None,
    ) -> dict[str, Any]:
        state = self._state
        sim_results = state.similarity.find_similar(
            description, stacktrace=stacktrace, component=component, limit=10,
        )
        items = [
            {
                **_issue_summary(sr.issue),
                "similarity": round(sr.similarity, 4),
                "matched_signals": sr.matched_signals,
            }
            for sr in sim_results
        ]
        return {"results": items, "total": len(items)}

    # -- Triage helpers -------------------------------------------------------

    def is_known_issue(
        self,
        error_message: str,
        version: str | None = None,
    ) -> dict[str, Any]:
        state = self._state
        results = state.search.search(error_message, limit=20)
        if version:
            results = self._filter_version(results, version)

        open_results = [r for r in results if r.issue.status.lower() not in ("closed", "resolved")]
        if not open_results:
            return {"known": False, "message": "No matching open issue found"}

        top = open_results[0].issue
        workarounds = self._scan_comments(top, _WORKAROUND_RE)
        return {
            "known": True,
            "issue": _issue_summary(top),
            "workaround": workarounds[0]["text"] if workarounds else None,
            "total_matches": len(open_results),
        }

    def find_workaround(self, query: str) -> dict[str, Any]:
        issue = self._resolve_issue_or_search(query)
        if issue is None:
            return _error_dict(f"No issue found for: {query}")

        workarounds = self._scan_comments(issue, _WORKAROUND_RE)
        return {
            "issue": _issue_summary(issue),
            "workarounds": workarounds,
            "total": len(workarounds),
        }

    def find_fix(self, query: str) -> dict[str, Any]:
        issue = self._resolve_issue_or_search(query)
        if issue is None:
            return _error_dict(f"No issue found for: {query}")

        fixes = self._scan_comments(issue, _FIX_RE)

        fix_info: dict[str, Any] = {
            "issue": _issue_summary(issue),
            "fixes": fixes,
            "total": len(fixes),
        }
        if issue.resolution:
            fix_info["resolution"] = issue.resolution
        if issue.fixed_versions:
            fix_info["fixed_versions"] = issue.fixed_versions
        return fix_info

    # -- Relationship queries -------------------------------------------------

    def find_related_issues(self, issue_id: str) -> dict[str, Any]:
        state = self._state
        issue = state.search.get_issue(issue_id)
        if issue is None:
            return _error_dict(f"Issue not found: {issue_id}")

        related = []
        for rel in issue.relationships:
            target = state.search.get_issue(rel.target_id)
            entry: dict[str, Any] = {
                "relation_type": rel.relation_type,
                "target_id": rel.target_id,
                "target_source": rel.target_source,
                "target_url": rel.target_url,
            }
            if target:
                entry["issue"] = _issue_summary(target)
            related.append(entry)

        return {
            "issue": _issue_summary(issue),
            "related": related,
            "total": len(related),
        }

    # -- Specialised searches -------------------------------------------------

    def search_stacktrace(self, stacktrace: str) -> dict[str, Any]:
        state = self._state
        fp = fingerprint(stacktrace)
        results = state.search.search(stacktrace, limit=20)

        from ceph_issue_kb.search.similarity import _jaccard
        from ceph_issue_kb.models import SearchResult

        # TODO: O(n) full scan could be replaced with an inverted index built at load time
        seen_ids = {r.issue.entity_id for r in results}
        for issue in state.search.issues.values():
            if len(results) >= 50:
                break
            if issue.entity_id in seen_ids:
                continue
            for st in issue.stacktraces:
                if fingerprint(st) == fp or _jaccard(stacktrace, st) > 0.3:
                    results.append(SearchResult(issue=issue, score=1.0, search_source="stacktrace"))
                    seen_ids.add(issue.entity_id)
                    break

        items = [
            {**_issue_summary(r.issue), "score": round(r.score, 4)}
            for r in results[:10]
        ]
        return {"results": items, "total": len(items), "fingerprint": fp}

    def search_health_warning(self, warning: str) -> dict[str, Any]:
        state = self._state
        results = state.search.search(warning, limit=20)

        from ceph_issue_kb.models import SearchResult

        # TODO: O(n) full scan could be replaced with an inverted index built at load time
        warning_lower = warning.lower()
        seen_ids = {r.issue.entity_id for r in results}
        for issue in state.search.issues.values():
            if len(results) >= 50:
                break
            if issue.entity_id in seen_ids:
                continue
            hw_match = any(warning_lower in hw.lower() for hw in issue.health_warnings)
            if hw_match:
                results.append(SearchResult(issue=issue, score=0.9, search_source="health_warning"))
                seen_ids.add(issue.entity_id)

        items = [
            {**_issue_summary(r.issue), "score": round(r.score, 4)}
            for r in results[:10]
        ]
        return {"results": items, "total": len(items), "warning": warning}

    # -- Analytics ------------------------------------------------------------

    def hot_issues(
        self,
        component: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        limit = max(1, min(int(limit), self._MAX_LIMIT))
        state = self._state
        issues = list(state.search.issues.values())
        if component:
            comp_lower = component.lower()
            issues = [i for i in issues if comp_lower in [c.lower() for c in i.components]]

        issues.sort(key=lambda i: i.updated_at or "", reverse=True)

        items = [_issue_summary(i) for i in issues[:limit]]
        return {"results": items, "total": len(items)}

    def component_health(self, component: str) -> dict[str, Any]:
        state = self._state
        comp_lower = component.lower()
        issues = [
            i for i in state.search.issues.values()
            if comp_lower in [c.lower() for c in i.components]
        ]

        open_issues = [i for i in issues if i.status.lower() not in ("closed", "resolved")]
        criticals = [i for i in open_issues if i.priority.lower() in ("critical", "urgent")]
        blockers = [i for i in open_issues if "blocker" in [l.lower() for l in i.labels]]
        regressions = [i for i in open_issues if "regression" in [l.lower() for l in i.labels]]

        return {
            "component": component,
            "total_issues": len(issues),
            "open_issues": len(open_issues),
            "critical_issues": [_issue_summary(i) for i in criticals],
            "blockers": [_issue_summary(i) for i in blockers],
            "regressions": [_issue_summary(i) for i in regressions],
        }

    # -- Contract tools -------------------------------------------------------

    def capabilities(self) -> dict[str, Any]:
        state = self._state
        sources = sorted({issue.source for issue in state.search.issues.values()})
        return {
            "name": KNOWLEDGE_BASE,
            "schema_version": SCHEMA_VERSION,
            "entity_types": ["issue", "comment", "relationship"],
            "operations": [
                "search_issues",
                "find_similar_issue",
                "is_known_issue",
                "find_workaround",
                "find_fix",
                "find_related_issues",
                "search_stacktrace",
                "search_health_warning",
                "hot_issues",
                "component_health",
            ],
            "sources": sources,
            "entity_counts": {"issues": len(state.search.issues)},
        }

    def health(self) -> dict[str, Any]:
        state = self._state
        issue_count = len(state.search.issues)
        index_ok = issue_count > 0
        status = "ok" if index_ok else "degraded"

        return {
            "status": status,
            "total_issues": issue_count,
            "index_status": "loaded" if index_ok else "empty",
            "schema_version": SCHEMA_VERSION,
            "kb_path": str(self._kb_path) if self._kb_path else None,
        }

    # -- Internal helpers -----------------------------------------------------

    @staticmethod
    def _filter_version(results: list, version: str) -> list:
        version_lower = version.lower()
        return [
            r for r in results
            if any(version_lower in v.lower() for v in r.issue.affected_versions)
            or version_lower in (r.issue.release or "").lower()
        ]

    def _resolve_issue_or_search(self, query: str) -> NormalizedIssue | None:
        """If *query* looks like an entity ID, look it up; otherwise search."""
        state = self._state
        if re.fullmatch(r"[0-9a-f]{16}", query):
            return state.search.get_issue(query)
        results = state.search.search(query, limit=1)
        return results[0].issue if results else None

    @staticmethod
    def _scan_comments(
        issue: NormalizedIssue,
        pattern: re.Pattern,
    ) -> list[dict[str, str]]:
        matches: list[dict[str, str]] = []
        if pattern.search(issue.description or ""):
            matches.append({
                "text": (issue.description or "")[:500],
                "author": issue.reporter,
                "date": issue.created_at,
                "source": "description",
            })
        for comment in issue.comments:
            if pattern.search(comment.body):
                matches.append({
                    "text": comment.body[:500],
                    "author": comment.author,
                    "date": comment.created_at,
                    "source": "comment",
                })
        return matches
