"""Normalize RawIssue from any connector into NormalizedIssue.

Single entry point: ``normalize(raw)`` dispatches to a source-specific
normalizer based on ``raw.source``.  Each normalizer maps raw connector
data to the canonical schema, extracts signals, and builds relationships.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from ceph_issue_kb.models import (
    Comment,
    NormalizedIssue,
    RawIssue,
    Relationship,
    make_entity_id,
)
from ceph_issue_kb.signal_extractor import ExtractedSignals, extract_signals

logger = logging.getLogger(__name__)

_SOURCE_NORMALIZERS: dict[str, "_NormalizerFn"] = {}

type _NormalizerFn = "callable[[RawIssue], NormalizedIssue]"

_SUMMARY_MAX_CHARS = 500

_HTML_TAG_RE = re.compile(r"<[^>]+>")


def normalize(raw: RawIssue) -> NormalizedIssue:
    """Convert a *raw* issue to a NormalizedIssue.

    Raises ``ValueError`` if the source type is unrecognised.
    """
    fn = _SOURCE_NORMALIZERS.get(raw.source)
    if fn is None:
        for key, handler in _SOURCE_NORMALIZERS.items():
            if key in raw.source:
                fn = handler
                break
    if fn is None:
        source_type = _guess_source_type(raw)
        fn = _SOURCE_NORMALIZERS.get(source_type)
    if fn is None:
        raise ValueError(
            f"No normalizer for source {raw.source!r}. "
            f"Known: {list(_SOURCE_NORMALIZERS)}"
        )
    return fn(raw)


def _guess_source_type(raw: RawIssue) -> str:
    """Heuristic fallback: look at the data shape to infer source type."""
    data = raw.data
    if "journals" in data:
        return "redmine"
    if "fields" in data:
        return "jira"
    if "product" in data and "component" in data:
        return "bugzilla"
    if "documentKind" in data or "kcsState" in data:
        return "rhkb"
    return ""


def _strip_html(text: str) -> str:
    return re.sub(r"\s+", " ", _HTML_TAG_RE.sub(" ", text)).strip()


def _make_summary(description: str) -> str:
    clean = _strip_html(description).strip()
    if len(clean) <= _SUMMARY_MAX_CHARS:
        return clean
    cut = clean[:_SUMMARY_MAX_CHARS]
    last_space = cut.rfind(" ")
    if last_space > _SUMMARY_MAX_CHARS // 2:
        cut = cut[:last_space]
    return cut + "..."


def _merge_signals(signals: ExtractedSignals, into: NormalizedIssue) -> None:
    """Merge extracted signals into the issue, deduplicating."""
    into.stacktraces = list(dict.fromkeys(into.stacktraces + signals.stacktraces))
    into.assertions = list(dict.fromkeys(into.assertions + signals.assertions))
    into.health_warnings = list(
        dict.fromkeys(into.health_warnings + signals.health_warnings)
    )
    into.commands_mentioned = list(
        dict.fromkeys(into.commands_mentioned + signals.commands_mentioned)
    )
    into.configs_mentioned = list(
        dict.fromkeys(into.configs_mentioned + signals.configs_mentioned)
    )
    into.log_snippets = list(dict.fromkeys(into.log_snippets + signals.log_snippets))


def _safe_str(val: Any, default: str = "") -> str:
    if val is None:
        return default
    return str(val)


# ---------------------------------------------------------------------------
# Redmine
# ---------------------------------------------------------------------------


def _normalize_redmine(raw: RawIssue) -> NormalizedIssue:
    data = raw.data
    entity_id = make_entity_id(raw.source, raw.source_id)

    description = _safe_str(data.get("description", ""))
    title = _safe_str(data.get("subject", ""))

    comments: list[Comment] = []
    for journal in data.get("journals", []):
        notes = _safe_str(journal.get("notes", ""))
        if not notes.strip():
            continue
        user = journal.get("user", {})
        comments.append(
            Comment(
                comment_id=str(journal.get("id", "")),
                author=_safe_str(user.get("name", "")),
                body=notes,
                created_at=_safe_str(journal.get("created_on", "")),
            )
        )

    relationships: list[Relationship] = []
    for rel in data.get("relations", []):
        target_id = str(rel.get("issue_to_id", ""))
        if str(rel.get("issue_id", "")) != raw.source_id:
            target_id = str(rel.get("issue_id", ""))
        relationships.append(
            Relationship(
                relation_type=_safe_str(rel.get("relation_type", "related")),
                target_source=raw.source,
                target_id=target_id,
                target_url=f"{raw.source_url.rsplit('/issues/', 1)[0]}/issues/{target_id}"
                if "/issues/" in raw.source_url
                else "",
            )
        )

    components: list[str] = []
    for cf in data.get("custom_fields", []):
        if _safe_str(cf.get("name", "")).lower() in ("component", "components"):
            val = cf.get("value", "")
            if isinstance(val, list):
                components.extend(str(v) for v in val if v)
            elif val:
                components.append(str(val))

    tracker = data.get("tracker", {})
    if tracker.get("name"):
        pass  # tracker is issue type, not component

    status_obj = data.get("status", {})
    priority_obj = data.get("priority", {})
    author_obj = data.get("author", {})
    assigned_obj = data.get("assigned_to", {})

    issue = NormalizedIssue(
        entity_id=entity_id,
        source=raw.source,
        source_id=raw.source_id,
        source_url=raw.source_url,
        title=title,
        summary=_make_summary(description),
        description=description,
        comments=comments,
        status=_safe_str(status_obj.get("name", "")).lower(),
        priority=_safe_str(priority_obj.get("name", "")).lower(),
        components=[c.lower() for c in components],
        reporter=_safe_str(author_obj.get("name", "")),
        assignee=_safe_str(assigned_obj.get("name", "")),
        created_at=_safe_str(data.get("created_on", "")),
        updated_at=_safe_str(data.get("updated_on", "")),
        relationships=relationships,
    )

    all_text = description + "\n" + "\n".join(c.body for c in comments)
    _merge_signals(extract_signals(all_text), issue)

    return issue


_SOURCE_NORMALIZERS["redmine"] = _normalize_redmine
_SOURCE_NORMALIZERS["ceph-tracker"] = _normalize_redmine


# ---------------------------------------------------------------------------
# JIRA
# ---------------------------------------------------------------------------


def _normalize_jira(raw: RawIssue) -> NormalizedIssue:
    data = raw.data
    fields = data.get("fields", {})
    entity_id = make_entity_id(raw.source, raw.source_id)

    description = _safe_str(fields.get("description", ""))
    title = _safe_str(fields.get("summary", ""))

    comments: list[Comment] = []
    comment_block = fields.get("comment", {})
    for c in comment_block.get("comments", []):
        author_obj = c.get("author", {})
        comments.append(
            Comment(
                comment_id=str(c.get("id", "")),
                author=_safe_str(author_obj.get("displayName", "")),
                body=_safe_str(c.get("body", "")),
                created_at=_safe_str(c.get("created", "")),
            )
        )

    relationships: list[Relationship] = []
    for link in fields.get("issuelinks", []):
        link_type = link.get("type", {})
        rel_type = _safe_str(link_type.get("name", "related")).lower()
        outward = link.get("outwardIssue", {})
        inward = link.get("inwardIssue", {})
        target = outward or inward
        target_key = _safe_str(target.get("key", ""))
        if target_key:
            base = raw.source_url.rsplit("/browse/", 1)[0] if "/browse/" in raw.source_url else ""
            relationships.append(
                Relationship(
                    relation_type=rel_type,
                    target_source=raw.source,
                    target_id=target_key,
                    target_url=f"{base}/browse/{target_key}" if base else "",
                )
            )

    components = [
        _safe_str(comp.get("name", "")).lower()
        for comp in fields.get("components", [])
        if comp.get("name")
    ]
    labels = [str(lbl) for lbl in fields.get("labels", [])]
    affected_versions = [
        _safe_str(v.get("name", "")) for v in fields.get("versions", []) if v.get("name")
    ]
    fixed_versions = [
        _safe_str(v.get("name", "")) for v in fields.get("fixVersions", []) if v.get("name")
    ]

    status_obj = fields.get("status", {})
    priority_obj = fields.get("priority", {})
    resolution_obj = fields.get("resolution") or {}
    reporter_obj = fields.get("reporter") or {}
    assignee_obj = fields.get("assignee") or {}

    issue = NormalizedIssue(
        entity_id=entity_id,
        source=raw.source,
        source_id=raw.source_id,
        source_url=raw.source_url,
        title=title,
        summary=_make_summary(description),
        description=description,
        comments=comments,
        status=_safe_str(status_obj.get("name", "")).lower(),
        resolution=_safe_str(resolution_obj.get("name", "")).lower(),
        priority=_safe_str(priority_obj.get("name", "")).lower(),
        components=components,
        labels=labels,
        affected_versions=affected_versions,
        fixed_versions=fixed_versions,
        reporter=_safe_str(reporter_obj.get("displayName", "")),
        assignee=_safe_str(assignee_obj.get("displayName", "")),
        created_at=_safe_str(fields.get("created", "")),
        updated_at=_safe_str(fields.get("updated", "")),
        relationships=relationships,
    )

    all_text = description + "\n" + "\n".join(c.body for c in comments)
    _merge_signals(extract_signals(all_text), issue)

    return issue


_SOURCE_NORMALIZERS["jira"] = _normalize_jira
_SOURCE_NORMALIZERS["ibm-jira"] = _normalize_jira


# ---------------------------------------------------------------------------
# Bugzilla
# ---------------------------------------------------------------------------


def _normalize_bugzilla(raw: RawIssue) -> NormalizedIssue:
    data = raw.data
    entity_id = make_entity_id(raw.source, raw.source_id)

    title = _safe_str(data.get("summary", ""))
    description = ""
    comments_raw = data.get("comments", [])
    comments: list[Comment] = []
    for i, c in enumerate(comments_raw):
        body = _safe_str(c.get("text", ""))
        if i == 0:
            description = body
        comments.append(
            Comment(
                comment_id=str(c.get("id", "")),
                author=_safe_str(c.get("creator", "")),
                body=body,
                created_at=_safe_str(c.get("creation_time", "")),
            )
        )

    relationships: list[Relationship] = []
    for blocked_id in data.get("blocks", []):
        relationships.append(
            Relationship(
                relation_type="blocks",
                target_source=raw.source,
                target_id=str(blocked_id),
                target_url=f"{raw.source_url.rsplit('/show_bug.cgi', 1)[0]}/show_bug.cgi?id={blocked_id}"
                if "/show_bug.cgi" in raw.source_url
                else "",
            )
        )
    for dep_id in data.get("depends_on", []):
        relationships.append(
            Relationship(
                relation_type="depends_on",
                target_source=raw.source,
                target_id=str(dep_id),
                target_url=f"{raw.source_url.rsplit('/show_bug.cgi', 1)[0]}/show_bug.cgi?id={dep_id}"
                if "/show_bug.cgi" in raw.source_url
                else "",
            )
        )

    component_val = data.get("component", "")
    components = [component_val.lower()] if component_val else []
    keywords = [str(k) for k in data.get("keywords", [])]

    target_release = data.get("target_release", [])
    fixed_versions = [str(v) for v in target_release] if isinstance(target_release, list) else []
    affected_versions = [_safe_str(data.get("version", ""))] if data.get("version") else []

    issue = NormalizedIssue(
        entity_id=entity_id,
        source=raw.source,
        source_id=raw.source_id,
        source_url=raw.source_url,
        title=title,
        summary=_make_summary(description),
        description=description,
        comments=comments,
        status=_safe_str(data.get("status", "")).lower(),
        resolution=_safe_str(data.get("resolution", "")).lower(),
        priority=_safe_str(data.get("priority", "")).lower(),
        severity=_safe_str(data.get("severity", "")).lower(),
        components=components,
        labels=keywords,
        affected_versions=affected_versions,
        fixed_versions=fixed_versions,
        reporter=_safe_str(data.get("creator", "")),
        assignee=_safe_str(data.get("assigned_to", "")),
        created_at=_safe_str(data.get("creation_time", "")),
        updated_at=_safe_str(data.get("last_change_time", "")),
        relationships=relationships,
    )

    all_text = description + "\n" + "\n".join(c.body for c in comments)
    _merge_signals(extract_signals(all_text), issue)

    return issue


_SOURCE_NORMALIZERS["bugzilla"] = _normalize_bugzilla
_SOURCE_NORMALIZERS["redhat-bugzilla"] = _normalize_bugzilla


# ---------------------------------------------------------------------------
# Red Hat Knowledge Base
# ---------------------------------------------------------------------------


def _normalize_rhkb(raw: RawIssue) -> NormalizedIssue:
    data = raw.data
    entity_id = make_entity_id(raw.source, raw.source_id)

    title = _safe_str(data.get("title", ""))
    abstract = _safe_str(data.get("abstract", ""))
    body_html = _safe_str(data.get("body", ""))
    body_text = _strip_html(body_html)
    description = body_text if body_text else abstract

    tags = [str(t).lower() for t in data.get("tags", [])]
    components = [t for t in tags if t in _CEPH_COMPONENTS]
    labels = tags

    version_val = data.get("version", "")
    affected_versions = [str(version_val)] if version_val else []

    status = "published" if data.get("kcsState") == "published" else _safe_str(data.get("kcsState", ""))

    issue = NormalizedIssue(
        entity_id=entity_id,
        source=raw.source,
        source_id=raw.source_id,
        source_url=raw.source_url,
        title=title,
        summary=_make_summary(abstract if abstract else description),
        description=description,
        status=status.lower(),
        components=components,
        labels=labels,
        affected_versions=affected_versions,
        created_at=_safe_str(data.get("publishedDate", "")),
        updated_at=_safe_str(data.get("lastModifiedDate", "")),
    )

    _merge_signals(extract_signals(description), issue)

    return issue


_SOURCE_NORMALIZERS["rhkb"] = _normalize_rhkb
_SOURCE_NORMALIZERS["redhat-kb"] = _normalize_rhkb

_CEPH_COMPONENTS = frozenset({
    "osd",
    "mon",
    "mds",
    "mgr",
    "rgw",
    "rbd",
    "rados",
    "cephfs",
    "bluestore",
    "cephadm",
    "dashboard",
    "nfs",
    "pg",
    "crush",
    "multisite",
    "recovery",
})
