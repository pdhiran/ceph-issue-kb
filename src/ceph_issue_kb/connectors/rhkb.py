"""Red Hat Knowledge Base connector.

Uses the Red Hat Customer Portal search API at
https://access.redhat.com/rs/search. Authentication is cookie-based
(Red Hat SSO session cookie from environment variable).

Limitations:
- The RH KB search API is undocumented and may change without notice.
- Cookie-based auth requires periodic manual renewal (SSO sessions expire).
- Search results are limited in depth; the API may not return all matches.
- No reliable "updated since" filter — we approximate with date ranges.
- Rate limiting is strict; keep requests/second very low.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import urlparse

import requests

from ceph_issue_kb.config import ConnectorConfig
from ceph_issue_kb.connectors.base import BaseConnector, ConnectorError
from ceph_issue_kb.models import RawIssue

logger = logging.getLogger(__name__)

PAGE_SIZE = 20


class RHKBConnector(BaseConnector):
    """Connector for Red Hat Knowledge Base (search API).

    This is the most fragile connector. The API is undocumented, relies on
    cookie-based authentication that expires, and may change at any time.
    """

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._session = requests.Session()
        self._session.headers.update(
            {"Accept": "application/json"}
        )
        hostname = urlparse(self.base_url).hostname or ""
        parts = hostname.split(".")
        cookie_domain = "." + ".".join(parts[-2:]) if len(parts) >= 2 else hostname
        cookie_name = config.extra.get("cookie_name", "rh_jwt")
        self._session.cookies.set(
            cookie_name, self._credentials.cookie, domain=cookie_domain
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
                f"RH KB request failed: {path} — {exc}"
            ) from exc

    def authenticate(self) -> None:
        """Validate that the SSO cookie is still active."""
        self._get("/rs/search", params={"q": "ceph", "rows": 1})
        logger.debug("RH KB authentication (cookie) appears valid")

    def search(
        self, query: str, *, since: str | None = None, limit: int = 100
    ) -> Iterator[RawIssue]:
        """Search knowledge base articles matching *query*.

        Paginates through results up to *limit* total articles.
        """
        params: dict[str, Any] = {
            "q": query,
            "rows": min(limit, PAGE_SIZE),
            "documentKind": "Solution",
        }
        if since:
            params["start_date"] = since
        yield from self._paginate("/rs/search", params, limit=limit)

    def fetch(self, issue_id: str) -> RawIssue:
        """Fetch a single knowledge base article by ID.

        Article IDs are numeric (e.g. "1234567").
        """
        data = self._get(f"/rs/solutions/{issue_id}")
        return RawIssue(
            source=self.name,
            source_id=str(data.get("id", issue_id)),
            source_url=f"{self.base_url}/solutions/{issue_id}",
            data=data,
        )

    def fetch_updates(self, since: str) -> Iterator[RawIssue]:
        """Yield articles updated since *since*.

        Note: The RH KB API has limited date filtering. We use
        start_date as an approximation. Results may not be exhaustive.
        """
        default_query = self.config.extra.get("default_query", "ceph")
        params: dict[str, Any] = {
            "q": default_query,
            "rows": PAGE_SIZE,
            "documentKind": "Solution",
            "start_date": since,
        }
        yield from self._paginate("/rs/search", params, limit=None)

    def health(self) -> dict:
        """Check connectivity to the RH Knowledge Base."""
        try:
            data = self._get("/rs/search", params={"q": "ceph", "rows": 1})
            num_found = data.get("response", {}).get("numFound", 0)
            return {
                "ok": True,
                "source": self.name,
                "total_issues": num_found,
                "message": (
                    f"Connected; ~{num_found} articles matching 'ceph'"
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
        """Paginate through RH KB search results using offset-based pagination."""
        offset = 0
        yielded = 0
        while True:
            params["start"] = offset
            data = self._get(path, params)
            response = data.get("response", {})
            docs = response.get("docs", [])
            num_found = response.get("numFound", 0)
            if not docs:
                break
            for doc in docs:
                if limit is not None and yielded >= limit:
                    return
                article_id = str(doc.get("id", ""))
                uri = doc.get("uri", f"/solutions/{article_id}")
                yield RawIssue(
                    source=self.name,
                    source_id=article_id,
                    source_url=f"{self.base_url}{uri}",
                    data=doc,
                )
                yielded += 1
            offset += len(docs)
            if offset >= num_found:
                break
        logger.info(
            "RH KB pagination complete: yielded %d articles (total available: %d)",
            yielded,
            num_found,
        )
