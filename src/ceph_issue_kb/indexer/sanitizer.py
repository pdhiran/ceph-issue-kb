"""Redact customer-sensitive data from normalized issues.

This module provides ``sanitize_issue()`` which strips private IPs,
internal domain names, customer names, support case numbers, and
non-company email addresses from all text fields of a NormalizedIssue.

The redaction is deterministic: the same input always produces the same
``[REDACTED-*-<hash>]`` token, so downstream deduplication and caching
remain stable.
"""
from __future__ import annotations

import hashlib
import re

from ceph_issue_kb.models import NormalizedIssue

# ---------------------------------------------------------------------------
# Allowlists
# ---------------------------------------------------------------------------

_ALLOWED_DOMAINS = frozenset({
    "redhat.com", "ibm.com", "ceph.io", "ceph.com",
    "suse.com", "suse.de", "github.com", "github.io",
    "bugzilla.redhat.com", "access.redhat.com", "tracker.ceph.com",
    "atlassian.net", "atlassian.com", "googleapis.com",
    "quay.io", "registry.redhat.io", "openshift.com",
    "openssh.com", "libssh.org", "kernel.org", "gnu.org",
    "openssl.org", "fedoraproject.org", "centos.org", "ubuntu.com",
    "debian.org", "python.org", "golang.org", "apache.org",
    "linuxfoundation.org",
    "example.com", "example.org", "example.net",
    "lists.sourceforge.net", "lists.podman.io",
    "sourceforge.net", "nongnu.org",
})

_ALLOWED_IP_PREFIXES = (
    "127.", "0.0.0.0", "255.255.255.",
    "10.0.0.", "10.0.1.", "10.0.2.",
    "192.168.0.", "192.168.1.",
)

_ALLOWED_HOSTNAMES = frozenset({
    "localhost", "localhost.localdomain",
    "localhost4", "localhost4.localdomain4",
    "localhost6", "localhost6.localdomain6",
})

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

_RE_PRIVATE_IP = re.compile(
    r"\b("
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2[0-9]|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r")\b"
)

_RE_INTERNAL_DOMAIN = re.compile(
    r"[a-zA-Z0-9._-]+"
    r"\.(?:corp|internal|intra|priv|private|lan)\."
    r"[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)

_RE_LOCAL_DOMAIN = re.compile(
    r"[a-zA-Z0-9._-]{3,}\.(?:localdomain|site|home\.arpa)\b",
    re.IGNORECASE,
)

_RE_CUSTOMER_NAME = re.compile(
    r"(?i)(customer\s+(?:affected|name|is|account)\s*[-:]\s*)"
    r"([A-Z][A-Za-z\s&.,()]{2,40})"
)

_RE_CASE_NUMBER = re.compile(
    r"(?i)((?:case|ticket)\s*(?:number|no|id|#)?\s*[:# ]?\s*)(\d{7,8})\b"
)

_RE_EMAIL = re.compile(
    r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z][a-zA-Z0-9.-]*\.[a-zA-Z]{2,}\b"
)

_RE_NOT_EMAIL = re.compile(
    r"\.service$"
    r"|@\d+\."
    r"|@osd\."
    r"|@nvmeof\."
    r"|@mon\."
    r"|@mgr\."
    r"|@mds\."
    r"|@rgw\."
    r"|@nfs\."
    r"|@smb\."
    r"|@iscsi\."
    r"|@rbd-mirror\."
    r"|@ceph"
    r"|@node-exporter\."
    r"|@prometheus\."
    r"|@tty"
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hash8(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()[:8]


def _is_allowed_domain(domain: str) -> bool:
    d = domain.lower()
    return any(d.endswith(a) or d.endswith("." + a) for a in _ALLOWED_DOMAINS)


def _is_allowed_ip(ip: str) -> bool:
    return any(ip.startswith(p) for p in _ALLOWED_IP_PREFIXES)


def _is_allowed_hostname(hostname: str) -> bool:
    return hostname.lower().split(".")[0] in _ALLOWED_HOSTNAMES


# ---------------------------------------------------------------------------
# Text sanitization
# ---------------------------------------------------------------------------


def sanitize_text(text: str) -> str:
    """Apply all redaction rules to a single string. Idempotent."""
    text = _RE_INTERNAL_DOMAIN.sub(
        lambda m: m.group(0) if _is_allowed_domain(m.group(0)) or _is_allowed_hostname(m.group(0))
        else f"[REDACTED-HOST-{_hash8(m.group(0))}]",
        text,
    )
    text = _RE_LOCAL_DOMAIN.sub(
        lambda m: m.group(0) if _is_allowed_hostname(m.group(0))
        else f"[REDACTED-HOST-{_hash8(m.group(0))}]",
        text,
    )
    text = _RE_PRIVATE_IP.sub(
        lambda m: m.group(1) if _is_allowed_ip(m.group(1))
        else f"[REDACTED-IP-{_hash8(m.group(1))}]",
        text,
    )
    text = _RE_CUSTOMER_NAME.sub(
        lambda m: f"{m.group(1)}[REDACTED-CUSTOMER-{_hash8(m.group(2).strip())}]",
        text,
    )
    text = _RE_CASE_NUMBER.sub(
        lambda m: f"{m.group(1)}[REDACTED-CASE-{_hash8(m.group(2))}]",
        text,
    )
    def _email_replacer(m: re.Match) -> str:
        email = m.group(0)
        if _RE_NOT_EMAIL.search(email):
            return email
        domain = email.split("@", 1)[1]
        if domain[0].isdigit():
            return email
        if _is_allowed_domain(domain):
            return email
        return f"[REDACTED-EMAIL-{_hash8(email)}]"

    text = _RE_EMAIL.sub(_email_replacer, text)
    return text


# ---------------------------------------------------------------------------
# Issue-level sanitization
# ---------------------------------------------------------------------------


def sanitize_issue(issue: NormalizedIssue) -> NormalizedIssue:
    """Return a copy of *issue* with all customer-sensitive text redacted.

    Structural fields (entity_id, source, URLs, timestamps) are preserved.
    """
    def _s(val: str) -> str:
        return sanitize_text(val) if val else val

    issue.title = _s(issue.title)
    issue.summary = _s(issue.summary)
    issue.description = _s(issue.description)

    for comment in issue.comments:
        comment.body = _s(comment.body)
        comment.author = _s(comment.author)

    issue.stacktraces = [_s(s) for s in issue.stacktraces]
    issue.assertions = [_s(s) for s in issue.assertions]
    issue.health_warnings = [_s(s) for s in issue.health_warnings]
    issue.commands_mentioned = [_s(s) for s in issue.commands_mentioned]
    issue.configs_mentioned = [_s(s) for s in issue.configs_mentioned]
    issue.log_snippets = [_s(s) for s in issue.log_snippets]
    issue.keywords = [_s(s) for s in issue.keywords]

    return issue
