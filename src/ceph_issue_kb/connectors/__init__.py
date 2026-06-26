"""Issue source connectors.

Each connector implements BaseConnector and returns RawIssue objects.
Connectors are registered in connectors.yaml and instantiated by
``get_connector()``.
"""

from __future__ import annotations

from ceph_issue_kb.config import ConnectorConfig
from ceph_issue_kb.connectors.base import BaseConnector, ConnectorError
from ceph_issue_kb.connectors.redmine import RedmineConnector

_CONNECTOR_TYPES: dict[str, type[BaseConnector]] = {
    "redmine": RedmineConnector,
}


def get_connector(config: ConnectorConfig) -> BaseConnector:
    """Instantiate the right connector class for *config*."""
    cls = _CONNECTOR_TYPES.get(config.connector_type)
    if cls is None:
        raise ConnectorError(
            f"Unknown connector type: {config.connector_type}"
            f". Available: {list(_CONNECTOR_TYPES)}"
        )
    return cls(config)
