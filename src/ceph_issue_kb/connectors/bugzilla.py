"""Bugzilla connector for Red Hat Bugzilla.

Uses Bugzilla REST API with API key authentication.
Pagination uses the offset + limit pattern.
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

PAGE_SIZE = 100


class BugzillaConnector(BaseConnector):
    """Connector for Bugzilla (REST API)."""

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self.product = config.extra.get("product", "")
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Content-Type": "application/json",
                "Accept": "application/json",
                "X-BUGZILLA-API-KEY": self._credentials.api_key,
            }
        )
        self._min_interval = 1.0 / max(config.rate_limit, 1)
        self._last_request = 0.0

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
            raise ConnectorError(
                f"Bugzilla request failed: {path} — {exc}"
            ) from exc

    def authenticate(self) -> None:
        """Validate API key by fetching the authenticated user."""
        data = self._get("/rest/whoami")
        if not data.get("id"):
            raise ConnectorError("Bugzilla authentication failed: no user ID in response")
        logger.debug("Bugzilla authentication check complete: %s", data)

    def search(
        self, query: str, *, since: str | None = None, limit: int = 100
    ) -> Iterator[RawIssue]:
        """Search bugs by product and query string.

        Paginates through results up to *limit* total bugs.
        """
        params: dict[str, Any] = {
            "product": self.product,
            "quicksearch": query,
            "order": "changeddate DESC",
            "limit": min(limit, PAGE_SIZE),
        }
        if since:
            params["last_change_time"] = since
        yield from self._paginate("/rest/bug", params, limit=limit)

    def fetch(self, issue_id: str) -> RawIssue:
        """Fetch a single bug by its numeric ID, including comments."""
        data = self._get(f"/rest/bug/{issue_id}")
        bugs = data.get("bugs", [])
        if not bugs:
            raise ConnectorError(f"Bug {issue_id} not found")
        bug = bugs[0]

        comments_data = self._get(f"/rest/bug/{issue_id}/comment")
        bug_comments = comments_data.get("bugs", {})
        comments = bug_comments.get(str(issue_id), {}).get("comments", [])
        bug["comments"] = comments

        return RawIssue(
            source=self.name,
            source_id=str(bug.get("id", issue_id)),
            source_url=f"{self.base_url}/show_bug.cgi?id={issue_id}",
            data=bug,
        )

    def fetch_updates(self, since: str) -> Iterator[RawIssue]:
        """Yield all bugs updated since *since* (ISO-8601 date).

        Paginates through the full result set.
        """
        params: dict[str, Any] = {
            "product": self.product,
            "last_change_time": since,
            "order": "changeddate DESC",
            "limit": PAGE_SIZE,
        }
        yield from self._paginate("/rest/bug", params, limit=None)

    def health(self) -> dict:
        """Check connectivity to Bugzilla."""
        try:
            data = self._get(
                "/rest/bug",
                params={"product": self.product, "limit": 1},
            )
            bugs = data.get("bugs", [])
            total = data.get("total_matches", len(bugs))
            return {
                "ok": True,
                "source": self.name,
                "total_issues": total,
                "message": (
                    f"Connected; product '{self.product}' accessible "
                    f"({total} bug(s) found)"
                ),
            }
        except ConnectorError as exc:
            return {
                "ok": False,
                "source": self.name,
                "total_issues": 0,
                "message": str(exc),
            }

    def _paginate(
        self, path: str, params: dict[str, Any], limit: int | None = None
    ) -> Iterator[RawIssue]:
        """Paginate through Bugzilla search results using offset/limit."""
        params = dict(params)
        offset = 0
        yielded = 0
        while True:
            params["offset"] = offset
            data = self._get(path, params)
            bugs = data.get("bugs", [])
            if not bugs:
                break

            bug_ids = [str(bug.get("id", "")) for bug in bugs]
            comments_path = f"/rest/bug/{','.join(bug_ids)}/comment"
            comments_data = self._get(comments_path)
            all_comments = comments_data.get("bugs", {})

            for bug in bugs:
                if limit is not None and yielded >= limit:
                    return
                bug_id = str(bug.get("id", ""))
                comments = all_comments.get(bug_id, {}).get("comments", [])
                bug["comments"] = comments
                yield RawIssue(
                    source=self.name,
                    source_id=bug_id,
                    source_url=f"{self.base_url}/show_bug.cgi?id={bug_id}",
                    data=bug,
                )
                yielded += 1
            offset += len(bugs)
            if len(bugs) < params.get("limit", PAGE_SIZE):
                break
        logger.info(
            "Bugzilla pagination complete: yielded %d bugs", yielded
        )
