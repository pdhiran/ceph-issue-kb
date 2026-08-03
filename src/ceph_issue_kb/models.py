"""Data models for the Ceph Issue Intelligence Knowledge Base.

All issues from all sources normalize to NormalizedIssue.
Connectors return RawIssue; the normalizer converts to NormalizedIssue.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

SCHEMA_VERSION = "1.0"
KNOWLEDGE_BASE = "ceph-issue-kb"


@dataclass
class Comment:
    """A single comment on an issue."""

    author: str
    body: str
    created_at: str
    comment_id: str = ""


@dataclass
class Relationship:
    """A link between two issues (duplicate, related, blocks, fixed-by, etc.)."""

    relation_type: str
    target_source: str
    target_id: str
    target_url: str = ""


@dataclass
class RawIssue:
    """Raw issue data as returned by a connector, before normalization.

    Connectors populate source/source_id/source_url and dump everything
    else into ``data``.  The normalizer handles schema mapping.
    """

    source: str
    source_id: str
    source_url: str
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedIssue:
    """Canonical representation of an issue from any source."""

    entity_id: str
    source: str
    source_id: str
    source_url: str

    title: str = ""
    summary: str = ""
    description: str = ""
    comments: list[Comment] = field(default_factory=list)

    status: str = ""
    resolution: str = ""
    priority: str = ""
    severity: str = ""
    components: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)

    affected_versions: list[str] = field(default_factory=list)
    fixed_versions: list[str] = field(default_factory=list)
    release: str = ""

    reporter: str = ""
    assignee: str = ""

    created_at: str = ""
    updated_at: str = ""
    resolved_at: str | None = None

    stacktraces: list[str] = field(default_factory=list)
    assertions: list[str] = field(default_factory=list)
    health_warnings: list[str] = field(default_factory=list)
    commands_mentioned: list[str] = field(default_factory=list)
    configs_mentioned: list[str] = field(default_factory=list)
    log_snippets: list[str] = field(default_factory=list)
    error_messages: list[str] = field(default_factory=list)

    relationships: list[Relationship] = field(default_factory=list)
    keywords: list[str] = field(default_factory=list)

    entity_type: str = "issue"
    indexed_at: str = ""
    schema_version: str = SCHEMA_VERSION
    knowledge_base: str = KNOWLEDGE_BASE

    def __post_init__(self) -> None:
        if not self.indexed_at:
            self.indexed_at = datetime.now(timezone.utc).isoformat()


@dataclass
class SearchResult:
    """A single result from the search engine."""

    issue: NormalizedIssue
    score: float
    search_source: str  # "bm25", "semantic", "merged"


def make_entity_id(source: str, source_id: str) -> str:
    """Deterministic entity ID from source + source_id."""
    key = f"{source}:{source_id}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]
