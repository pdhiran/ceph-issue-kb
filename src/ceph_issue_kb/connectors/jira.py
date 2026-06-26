"""JIRA connector for IBM/Ceph Atlassian instances.

Uses JIRA REST API v2 with Basic auth (username + API token).
Pagination uses the startAt + maxResults pattern.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import requests

from ceph_issue_kb.config import ConnectorConfig
from ceph_issue_kb.connectors.base import BaseConnector, ConnectorError
from ceph_issue_kb.models import RawIssue

logger = logging.getLogger(__name__)

PAGE_SIZE = 50


class JiraConnector(BaseConnector):
    """Connector for Atlassian JIRA (REST API v2)."""

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self.project = config.extra.get("project", "")
        self._session = requests.Session()
        self._session.auth = (self._credentials.username, self._credentials.token)
        self._session.headers.update(
            {"Content-Type": "application/json", "Accept": "application/json"}
        )
        self._min_interval = 1.0 / max(config.rate_limit, 1)
        self._last_request = 0.0

    @staticmethod
    def _escape_jql(value: str) -> str:
        """Escape a string for safe use inside a JQL quoted literal."""
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.monotonic()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        url = self.base_url + path
        self._throttle()
        try:
            resp = self._session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except (requests.RequestException, ValueError) as exc:
            raise ConnectorError(f"JIRA request failed: {path} — {exc}") from exc

    def authenticate(self) -> None:
        """Validate credentials by fetching current user info."""
        self._get("/rest/api/2/myself")
        logger.debug("JIRA authentication successful")

    def search(
        self, query: str, *, since: str | None = None, limit: int = 100
    ) -> Iterator[RawIssue]:
        """Search issues via JQL text search.

        Paginates through results up to *limit* total issues.
        """
        project = self._escape_jql(self.project)
        q = self._escape_jql(query)
        jql = f'project = "{project}" AND text ~ "{q}"'
        if since:
            jql += f' AND updated >= "{since}"'
        jql += " ORDER BY updated DESC"
        yield from self._paginate(jql, limit=limit)

    def fetch(self, issue_id: str) -> RawIssue:
        """Fetch a single issue by its JIRA key (e.g. IBMCEPH-1234)."""
        data = self._get(
            f"/rest/api/2/issue/{issue_id}",
            params={"expand": "renderedFields", "fields": "*all"},
        )
        key = data.get("key", issue_id)
        return RawIssue(
            source=self.name,
            source_id=key,
            source_url=f"{self.base_url}/browse/{key}",
            data=data,
        )

    def fetch_updates(self, since: str) -> Iterator[RawIssue]:
        """Yield all issues updated since *since* (ISO-8601 date).

        Paginates through the full result set.
        """
        project = self._escape_jql(self.project)
        jql = (
            f'project = "{project}" AND updated >= "{since}" '
            f"ORDER BY updated DESC"
        )
        yield from self._paginate(jql, limit=None)

    def health(self) -> dict:
        """Check connectivity to JIRA."""
        try:
            project = self._escape_jql(self.project)
            jql = f'project = "{project}" ORDER BY updated DESC'
            data = self._get(
                "/rest/api/2/search",
                params={"jql": jql, "maxResults": 0},
            )
            total = data.get("total", 0)
            return {
                "ok": True,
                "source": self.name,
                "total_issues": total,
                "message": f"Connected; {total} issues in project '{self.project}'",
            }
        except ConnectorError as exc:
            return {
                "ok": False,
                "source": self.name,
                "total_issues": 0,
                "message": str(exc),
            }

    def _paginate(self, jql: str, limit: int | None = None) -> Iterator[RawIssue]:
        """Paginate through JIRA search results using startAt/maxResults."""
        start_at = 0
        yielded = 0
        max_results = min(limit, PAGE_SIZE) if limit is not None else PAGE_SIZE
        while True:
            data = self._get(
                "/rest/api/2/search",
                params={
                    "jql": jql,
                    "startAt": start_at,
                    "maxResults": max_results,
                    "expand": "renderedFields",
                    "fields": "*all",
                },
            )
            issues = data.get("issues", [])
            total = data.get("total", 0)
            if not issues:
                break
            for issue in issues:
                if limit is not None and yielded >= limit:
                    return
                key = issue.get("key", "")
                yield RawIssue(
                    source=self.name,
                    source_id=key,
                    source_url=f"{self.base_url}/browse/{key}",
                    data=issue,
                )
                yielded += 1
            start_at += len(issues)
            if start_at >= total:
                break
        logger.info(
            "JIRA pagination complete: yielded %d issues (total available: %d)",
            yielded,
            total,
        )
