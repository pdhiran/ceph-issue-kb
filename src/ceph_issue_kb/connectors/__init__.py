"""Issue source connectors.

Each connector implements BaseConnector and returns RawIssue objects.
Connectors are registered in connectors.yaml and instantiated by
``get_connector()``.
"""

from __future__ import annotations

from ceph_issue_kb.config import ConnectorConfig
from ceph_issue_kb.connectors.base import BaseConnector, ConnectorError
from ceph_issue_kb.connectors.bugzilla import BugzillaConnector
from ceph_issue_kb.connectors.jira import JiraConnector
from ceph_issue_kb.connectors.redmine import RedmineConnector
from ceph_issue_kb.connectors.rhkb import RHKBConnector

_CONNECTOR_TYPES: dict[str, type[BaseConnector]] = {
    "redmine": RedmineConnector,
    "jira": JiraConnector,
    "bugzilla": BugzillaConnector,
    "rhkb": RHKBConnector,
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
