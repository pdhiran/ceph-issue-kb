"""Red Hat Knowledge Base connector.

Uses the Red Hat Hydra search API at
https://access.redhat.com/hydra/rest/search/kcs.

Authentication uses OAuth 2.0 bearer tokens obtained by exchanging a
Red Hat offline API token via SSO.  Generate an offline token at
https://access.redhat.com/management/api and set it in the environment
variable configured by ``token_env`` (default ``RH_OFFLINE_TOKEN``).

Legacy cookie-based auth is still accepted for backward compatibility
but is unlikely to work with the Hydra API.
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

_SSO_TOKEN_URL = (
    "https://sso.redhat.com/auth/realms/redhat-external"
    "/protocol/openid-connect/token"
)
_SSO_CLIENT_ID = "rhsm-api"
_HYDRA_SEARCH_PATH = "/hydra/rest/search/kcs"


class RHKBConnector(BaseConnector):
    """Connector for Red Hat Knowledge Base (Hydra search API).

    Requires a Red Hat offline API token for authentication.  Cookie-based
    auth is retained as a fallback but may not work with the Hydra endpoint.
    """

    def __init__(self, config: ConnectorConfig) -> None:
        super().__init__(config)
        self._session = requests.Session()
        self._session.headers.update({"Accept": "application/json"})

        self._bearer_token: str | None = None
        self._token_expiry: float = 0.0

        if self._credentials.method == "offline_token":
            self._offline_token: str | None = self._credentials.token
        else:
            self._offline_token = None
            hostname = urlparse(self.base_url).hostname or ""
            parts = hostname.split(".")
            cookie_domain = (
                "." + ".".join(parts[-2:]) if len(parts) >= 2 else hostname
            )
            cookie_name = config.extra.get("cookie_name", "rh_jwt")
            self._session.cookies.set(
                cookie_name, self._credentials.cookie, domain=cookie_domain,
            )

        self._min_interval = 1.0 / max(config.rate_limit, 1)
        self._last_request = 0.0

    def _ensure_bearer_token(self) -> None:
        """Exchange the offline token for a short-lived bearer token via SSO."""
        if self._offline_token is None:
            return
        if self._bearer_token and time.time() < self._token_expiry:
            return
        try:
            resp = requests.post(
                _SSO_TOKEN_URL,
                data={
                    "grant_type": "refresh_token",
                    "client_id": _SSO_CLIENT_ID,
                    "refresh_token": self._offline_token,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                timeout=30,
            )
            resp.raise_for_status()
            data = resp.json()
            self._bearer_token = data["access_token"]
            self._token_expiry = time.time() + data.get("expires_in", 300) - 60
            self._session.headers["Authorization"] = (
                f"Bearer {self._bearer_token}"
            )
            logger.debug("RH SSO token refreshed, expires in %ds",
                         data.get("expires_in", 0))
        except (requests.RequestException, KeyError, ValueError) as exc:
            raise ConnectorError(
                f"RH SSO token exchange failed: {exc}"
            ) from exc

    def _throttle(self) -> None:
        elapsed = time.monotonic() - self._last_request
        if elapsed < self._min_interval:
            time.sleep(self._min_interval - elapsed)
        self._last_request = time.monotonic()

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict:
        self._ensure_bearer_token()
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
        """Validate that the credentials work against the Hydra API."""
        self._get(_HYDRA_SEARCH_PATH, params={"q": "ceph", "rows": 1})
        logger.debug("RH KB authentication validated against Hydra API")

    def search(
        self, query: str, *, since: str | None = None, limit: int = 100
    ) -> Iterator[RawIssue]:
        """Search knowledge base articles matching *query*.

        Paginates through results up to *limit* total articles.
        """
        params: dict[str, Any] = {
            "q": query,
            "rows": min(limit, PAGE_SIZE),
            "fq": 'documentKind:"Solution"',
        }
        if since:
            params["fq"] = [
                'documentKind:"Solution"',
                f"lastModifiedDate:[{since}T00:00:00Z TO NOW]",
            ]
        yield from self._paginate(_HYDRA_SEARCH_PATH, params, limit=limit)

    def fetch(self, issue_id: str) -> RawIssue:
        """Fetch a single knowledge base article by ID.

        Uses the Hydra search with ``q=id:<id>`` to retrieve full article
        content including resolution, root cause, and diagnostic steps.
        """
        data = self._get(
            _HYDRA_SEARCH_PATH,
            params={
                "q": f"id:{issue_id}",
                "rows": 1,
                "fl": (
                    "id,title,abstract,documentKind,view_uri,product,"
                    "issue,solution_environment,solution_rootcause,"
                    "solution_resolution,solution_diagnosticsteps,"
                    "lastModifiedDate,createdDate"
                ),
            },
        )
        docs = data.get("response", {}).get("docs", [])
        if not docs:
            raise ConnectorError(f"RH KB article not found: {issue_id}")
        doc = docs[0]
        view_uri = doc.get("view_uri", f"{self.base_url}/solutions/{issue_id}")
        return RawIssue(
            source=self.name,
            source_id=str(doc.get("id", issue_id)),
            source_url=view_uri,
            data=doc,
        )

    def fetch_updates(self, since: str) -> Iterator[RawIssue]:
        """Yield articles updated since *since*.

        Uses the Hydra API ``lastModifiedDate`` Solr filter for date
        filtering, which is more reliable than the legacy ``start_date``.
        """
        default_query = self.config.extra.get("default_query", "ceph")
        params: dict[str, Any] = {
            "q": default_query,
            "rows": PAGE_SIZE,
            "fq": [
                'documentKind:"Solution"',
                f"lastModifiedDate:[{since}T00:00:00Z TO NOW]",
            ],
        }
        yield from self._paginate(_HYDRA_SEARCH_PATH, params, limit=None)

    def health(self) -> dict:
        """Check connectivity to the RH Knowledge Base Hydra API."""
        try:
            data = self._get(
                _HYDRA_SEARCH_PATH, params={"q": "ceph", "rows": 1}
            )
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
        """Paginate through Hydra search results using offset-based pagination."""
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
                view_uri = doc.get(
                    "view_uri", f"{self.base_url}/solutions/{article_id}"
                )
                yield RawIssue(
                    source=self.name,
                    source_id=article_id,
                    source_url=view_uri,
                    data=doc,
                )
                yielded += 1
            offset += len(docs)
            if offset >= num_found:
                break
        logger.info(
            "RH KB pagination complete: yielded %d articles "
            "(total available: %d)",
            yielded,
            num_found,
        )
