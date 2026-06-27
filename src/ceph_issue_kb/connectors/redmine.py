"""Ceph Tracker (Redmine) connector.

The Ceph Tracker at https://tracker.ceph.com is a public Redmine instance.
No authentication is required for read operations.

Pagination is handled internally — callers iterate until exhaustion.
Requests use exponential backoff on transient failures to handle the
tracker's aggressive rate limiting.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ceph_issue_kb.config import ConnectorConfig
from ceph_issue_kb.connectors.base import BaseConnector, ConnectorError
from ceph_issue_kb.models import RawIssue

logger = logging.getLogger(__name__)

PAGE_SIZE = 100
INCLUDE_FIELDS = "journals,relations"

_RETRY_STRATEGY = Retry(
    total=3,
    backoff_factor=2,
    status_forcelist=[429, 500, 502, 503, 504],
    allowed_methods=["GET"],
    raise_on_status=False,
)

_RETRY_DELAYS = (5, 15, 30)


class RedmineConnector(BaseConnector):
    """Connector for the Ceph Tracker (Redmine REST API)."""

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self.project = config.extra.get("project", "ceph")
        self._session = requests.Session()
        self._session.headers.update(
            {"Content-Type": "application/json", "Accept": "application/json"}
        )
        adapter = HTTPAdapter(max_retries=_RETRY_STRATEGY)
        self._session.mount("https://", adapter)
        self._session.mount("http://", adapter)
        self._min_interval = max(1.0 / max(config.rate_limit, 1), 0.5)
        self._last_request = 0.0

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.monotonic()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        url = self.base_url + path
        max_attempts = len(_RETRY_DELAYS) + 1
        last_exc: Exception | None = None
        for attempt in range(max_attempts):
            self._throttle()
            try:
                resp = self._session.get(url, params=params, timeout=30)
                resp.raise_for_status()
                return resp.json()
            except requests.ConnectionError as exc:
                last_exc = exc
                if attempt < len(_RETRY_DELAYS):
                    delay = _RETRY_DELAYS[attempt]
                    logger.warning(
                        "Connection error on %s (attempt %d/%d), "
                        "retrying in %ds: %s",
                        path, attempt + 1, max_attempts, delay, exc,
                    )
                    time.sleep(delay)
                    continue
                break
            except (requests.RequestException, ValueError) as exc:
                raise ConnectorError(
                    f"Redmine request failed: {path} — {exc}"
                ) from exc
        raise ConnectorError(
            f"Redmine request failed after {max_attempts} attempts: "
            f"{path} — {last_exc}"
        ) from last_exc

    def authenticate(self) -> None:
        """No-op for the public Ceph Tracker."""
        logger.debug("Ceph Tracker is public; skipping authentication")

    def search(
        self, query: str, *, since: str | None = None, limit: int = 100
    ) -> Iterator[RawIssue]:
        """Search issues by subject containing *query*.

        Paginates through results up to *limit* total issues.
        """
        params: dict[str, Any] = {
            "project_id": self.project,
            "subject": f"~{query}",
            "status_id": "*",
            "sort": "updated_on:desc",
            "limit": min(limit, PAGE_SIZE),
        }
        if since:
            params["updated_on"] = f">={since}"
        yield from self._paginate("/issues.json", params, limit=limit)

    def fetch(self, issue_id: str) -> RawIssue:
        """Fetch a single issue by its Redmine ID, including journals and relations."""
        data = self._get(
            f"/issues/{issue_id}.json", params={"include": INCLUDE_FIELDS}
        )
        issue = data.get("issue", data)
        return RawIssue(
            source=self.name,
            source_id=str(issue.get("id", issue_id)),
            source_url=f"{self.base_url}/issues/{issue_id}",
            data=issue,
        )

    def fetch_updates(self, since: str) -> Iterator[RawIssue]:
        """Yield all issues updated since *since* (ISO-8601 date).

        Paginates through the full result set.
        """
        params: dict[str, Any] = {
            "project_id": self.project,
            "status_id": "*",
            "updated_on": f">={since}",
            "sort": "updated_on:desc",
            "limit": PAGE_SIZE,
        }
        yield from self._paginate("/issues.json", params, limit=None)

    def health(self) -> dict:
        """Check connectivity to the Ceph Tracker."""
        try:
            data = self._get(
                "/issues.json", params={"project_id": self.project, "limit": 1}
            )
            total = data.get("total_count", 0)
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

    def _paginate(
        self, path: str, params: dict[str, Any], limit: int | None = None
    ) -> Iterator[RawIssue]:
        """Paginate through a Redmine list endpoint.

        Yields RawIssue objects.  For each page, re-fetches the full issue
        (with journals + relations) so we get comments and links.
        """
        params = dict(params)
        offset = 0
        yielded = 0
        while True:
            params["offset"] = offset
            data = self._get(path, params)
            issues = data.get("issues", [])
            total_count = data.get("total_count", 0)
            if not issues:
                break
            for issue_summary in issues:
                if limit is not None and yielded >= limit:
                    return
                issue_id = str(issue_summary["id"])
                try:
                    full_issue = self.fetch(issue_id)
                except ConnectorError:
                    logger.warning("Failed to fetch issue %s, skipping", issue_id)
                    continue
                yield full_issue
                yielded += 1
            offset += len(issues)
            if offset >= total_count:
                break
        logger.info(
            "Redmine pagination complete: yielded %d issues (total available: %d)",
            yielded,
            total_count,
        )
