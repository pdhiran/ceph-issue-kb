"""Abstract base class for issue connectors.

Every connector implements this contract.  Connectors return raw data
only — normalization is handled by ``indexer.normalizer``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator

from ceph_issue_kb.config import ConnectorConfig
from ceph_issue_kb.connectors.auth import AuthProvider, Credentials
from ceph_issue_kb.models import RawIssue


class ConnectorError(Exception):
    """Raised when a connector encounters an unrecoverable error."""


class BaseConnector(ABC):
    """Abstract interface that every issue source connector must implement.

    Subclasses must call ``super().__init__(config)`` to resolve credentials
    and store common state.
    """

    def __init__(self, config: ConnectorConfig) -> None:
        self.config = config
        self.name = config.name
        self.base_url = config.base_url
        self.rate_limit = config.rate_limit
        self._credentials: Credentials = AuthProvider().resolve(config.auth)

    @abstractmethod
    def authenticate(self) -> None:
        """Validate that credentials work (e.g. test API call).

        For public APIs this can be a no-op.
        """
        ...

    @abstractmethod
    def search(
        self, query: str, *, since: str | None = None, limit: int = 100
    ) -> Iterator[RawIssue]:
        """Search for issues matching *query*.

        Must handle API pagination internally — callers iterate until
        exhaustion or reach *limit*.
        """
        ...

    @abstractmethod
    def fetch(self, issue_id: str) -> RawIssue:
        """Fetch a single issue by its source-native ID."""
        ...

    @abstractmethod
    def fetch_updates(self, since: str) -> Iterator[RawIssue]:
        """Yield issues updated since *since* (ISO-8601 date string).

        Must handle API pagination internally.
        """
        ...

    @abstractmethod
    def health(self) -> dict:
        """Return connector health information.

        At minimum: ``{"ok": bool, "source": str, "message": str}``.
        """
        ...
