"""Extract structured signals from issue text (description + comments).

Signals extracted:
- Stacktraces (Python, C++, core dumps)
- Assertions (assert failures, aborts)
- Health warnings (HEALTH_WARN, HEALTH_ERR)
- Ceph commands (ceph, rbd, rados, cephadm, etc.)
- Config parameters (e.g. osd_pool_default_size)
- Log snippets (timestamped lines, log-level-prefixed lines)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_STACKTRACE_PATTERNS = [
    re.compile(
        r"(?:^Traceback \(most recent call last\):$.*?^\w+(?:Error|Exception).*$)",
        re.MULTILINE | re.DOTALL,
    ),
    re.compile(r"^\s+File \".*\", line \d+.*$", re.MULTILINE),
    re.compile(r"^\s*#\d+\s+0x[0-9a-f]+\s+in\s+\S+.*$", re.MULTILINE),
    re.compile(r"^\s*#\d+\s+\S+\s+\(.*\)\s+at\s+\S+:\d+$", re.MULTILINE),
    re.compile(r"core dumped", re.IGNORECASE),
    re.compile(r"Segmentation fault", re.IGNORECASE),
    re.compile(r"(?:ceph_abort|ceph_assert|__ceph_assert_fail).*", re.IGNORECASE),
]

_ASSERTION_PATTERN = re.compile(
    r"^.*(?:assert|FAILED|abort|ABORT|ceph_abort|ceph_assert_fail|__ceph_assert_fail).*$",
    re.MULTILINE,
)

_HEALTH_WARNING_PATTERN = re.compile(
    r"\b(?:HEALTH_WARN|HEALTH_ERR|HEALTH_OK"
    r"|PG_DEGRADED|PG_RECOVERY_FULL|PG_BACKFILL_FULL|PG_INCOMPLETE"
    r"|OSD_DOWN|OSD_NEARFULL|OSD_FULL|OSD_BACKFILLFULL"
    r"|MON_DOWN|MON_CLOCK_SKEW"
    r"|MDS_DAMAGE|MDS_CACHE_OVERSIZED"
    r"|TOO_MANY_PGS|TOO_FEW_PGS"
    r"|POOL_NEARFULL|POOL_BACKFILLFULL"
    r"|OBJECT_MISPLACED|OBJECT_UNFOUND"
    r"|SLOW_OPS|REQUEST_SLOW"
    r"|RECENT_CRASH|RECENT_MGR_CRASH)\b"
)

_CEPH_COMMAND_PATTERN = re.compile(
    r"(?:^|\s)((?:sudo\s+)?(?:ceph|rbd|rados|radosgw-admin|cephadm"
    r"|ceph-volume|ceph-fuse|ceph-bluestore-tool|crushtool"
    r"|ceph-objectstore-tool|ceph-kvstore-tool|ceph-monstore-tool)"
    r"\s+[^\n;|&]{3,})"
)

_CONFIG_PARAM_PATTERN = re.compile(r"\b([a-z][a-z0-9]*(?:_[a-z0-9]+){2,})\b")

_CONFIG_PREFIXES = frozenset(
    {
        "osd_",
        "mon_",
        "mds_",
        "mgr_",
        "rgw_",
        "rbd_",
        "client_",
        "auth_",
        "bluestore_",
        "filestore_",
        "journal_",
        "crush_",
        "erasure_",
        "ms_",
        "cluster_",
        "debug_",
        "log_",
    }
)

_LOG_LINE_PATTERN = re.compile(
    r"^(?:\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}"
    r"|[A-Z][a-z]{2}\s+\d{1,2}\s+\d{2}:\d{2}:\d{2}"
    r"|\d{2}:\d{2}:\d{2}\.\d+).*$",
    re.MULTILINE,
)


@dataclass
class ExtractedSignals:
    """All structured signals extracted from a body of text."""

    stacktraces: list[str] = field(default_factory=list)
    assertions: list[str] = field(default_factory=list)
    health_warnings: list[str] = field(default_factory=list)
    commands_mentioned: list[str] = field(default_factory=list)
    configs_mentioned: list[str] = field(default_factory=list)
    log_snippets: list[str] = field(default_factory=list)


def extract_signals(text: str) -> ExtractedSignals:
    """Extract all structured signals from *text*.

    Call this on the concatenation of an issue's description and comments.
    """
    return ExtractedSignals(
        stacktraces=_extract_stacktraces(text),
        assertions=_extract_assertions(text),
        health_warnings=_extract_health_warnings(text),
        commands_mentioned=_extract_commands(text),
        configs_mentioned=_extract_configs(text),
        log_snippets=_extract_log_snippets(text),
    )


def _extract_stacktraces(text: str) -> list[str]:
    traces: list[str] = []
    for pat in _STACKTRACE_PATTERNS:
        for m in pat.finditer(text):
            snippet = m.group(0).strip()
            if snippet and not any(snippet in t or t in snippet for t in traces):
                traces.append(snippet)
    return traces


def _extract_assertions(text: str) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for m in _ASSERTION_PATTERN.finditer(text):
        line = m.group(0).strip()
        if line not in seen:
            seen.add(line)
            results.append(line)
    return results


def _extract_health_warnings(text: str) -> list[str]:
    return sorted(set(_HEALTH_WARNING_PATTERN.findall(text)))


def _extract_commands(text: str) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for m in _CEPH_COMMAND_PATTERN.finditer(text):
        cmd = m.group(1).strip()
        if cmd not in seen:
            seen.add(cmd)
            results.append(cmd)
    return results


def _extract_configs(text: str) -> list[str]:
    """Extract likely Ceph config parameters.

    Filters to strings that start with a known Ceph config prefix
    to reduce false positives.
    """
    candidates = set(_CONFIG_PARAM_PATTERN.findall(text))
    return sorted(c for c in candidates if any(c.startswith(p) for p in _CONFIG_PREFIXES))


def _extract_log_snippets(text: str, max_snippets: int = 50) -> list[str]:
    seen: set[str] = set()
    results: list[str] = []
    for m in _LOG_LINE_PATTERN.finditer(text):
        line = m.group(0).strip()
        if len(line) > 20 and line not in seen:
            seen.add(line)
            results.append(line)
        if len(results) >= max_snippets:
            break
    return results
