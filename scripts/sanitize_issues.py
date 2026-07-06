#!/usr/bin/env python3
"""Sanitize customer-sensitive data from knowledge-base JSON files.

Modes
-----
  python scripts/sanitize_issues.py               # redact in-place (default rules)
  python scripts/sanitize_issues.py --check        # exit 1 if dirty (for pre-commit)
  python scripts/sanitize_issues.py --dry-run      # show what would change
  python scripts/sanitize_issues.py --stats        # counts only, no changes
  python scripts/sanitize_issues.py --all          # enable ALL rules including IPs

Default rules redact customer-identifying data:
  - Internal/corporate domain names (.corp.*, .internal.*, etc.)
  - Customer names ("Customer affected - ...")
  - Non-company email addresses

Optional rules (enabled with --all or individual flags):
  - Private IPv4 addresses (10.x, 172.16-31.x, 192.168.x)
  - Support case/ticket numbers

Each run is idempotent: already-redacted text is left unchanged.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

KNOWLEDGE_ROOT = Path(__file__).resolve().parent.parent / "knowledge"

# ---------------------------------------------------------------------------
# Allowlists – domains/IPs that must NOT be redacted
# ---------------------------------------------------------------------------

ALLOWED_DOMAINS = frozenset({
    "redhat.com",
    "ibm.com",
    "ceph.io",
    "ceph.com",
    "suse.com",
    "suse.de",
    "github.com",
    "github.io",
    "bugzilla.redhat.com",
    "access.redhat.com",
    "tracker.ceph.com",
    "atlassian.net",
    "atlassian.com",
    "googleapis.com",
    "quay.io",
    "registry.redhat.io",
    "openshift.com",
    "openssh.com",
    "libssh.org",
    "kernel.org",
    "gnu.org",
    "openssl.org",
    "fedoraproject.org",
    "centos.org",
    "ubuntu.com",
    "debian.org",
    "python.org",
    "golang.org",
    "apache.org",
    "linuxfoundation.org",
    "example.com",
    "example.org",
    "example.net",
    "lists.sourceforge.net",
    "lists.podman.io",
    "sourceforge.net",
    "nongnu.org",
})

ALLOWED_IP_PREFIXES = (
    "127.",
    "0.0.0.0",
    "255.255.255.",
    "10.0.0.",
    "10.0.1.",
    "10.0.2.",
    "192.168.0.",
    "192.168.1.",
)

# Hostnames commonly seen in docs/examples that are not customer data
ALLOWED_HOSTNAMES = frozenset({
    "localhost",
    "localhost.localdomain",
    "localhost4",
    "localhost4.localdomain4",
    "localhost6",
    "localhost6.localdomain6",
})

# ---------------------------------------------------------------------------
# Compiled patterns
# ---------------------------------------------------------------------------

# RFC 1918 private IPs
_RE_PRIVATE_IP = re.compile(
    r"\b("
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"|172\.(?:1[6-9]|2[0-9]|3[01])\.\d{1,3}\.\d{1,3}"
    r"|192\.168\.\d{1,3}\.\d{1,3}"
    r")\b"
)

# Internal/corporate domain names  (host.dept.corp.customer.com etc.)
_RE_INTERNAL_DOMAIN = re.compile(
    r"[a-zA-Z0-9._-]+"
    r"\.(?:corp|internal|intra|priv|private|lan)\."
    r"[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}",
    re.IGNORECASE,
)

# Bare .local / .localdomain hostnames (not localhost*)
_RE_LOCAL_DOMAIN = re.compile(
    r"[a-zA-Z0-9._-]{3,}\.(?:localdomain|site|home\.arpa)\b",
    re.IGNORECASE,
)

# "Customer affected - <Name>"  /  "Customer name: <Name>"
_RE_CUSTOMER_NAME = re.compile(
    r"(?i)(customer\s+(?:affected|name|is|account)\s*[-:]\s*)"
    r"([A-Z][A-Za-z\s&.,()]{2,40})"
)

# Support case / ticket numbers (7-8 digits following "case"/"ticket")
_RE_CASE_NUMBER = re.compile(
    r"(?i)((?:case|ticket)\s*(?:number|no|id|#)?\s*[:# ]?\s*)(\d{7,8})\b"
)

# Email addresses – exclude systemd unit names (e.g. ceph-osd@0.service)
# and other @-patterns that aren't real emails
_RE_EMAIL = re.compile(
    r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z][a-zA-Z0-9.-]*\.[a-zA-Z]{2,}\b"
)

# Patterns to exclude from email detection: systemd units, Ceph daemon names
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


def _stable_hash(value: str, length: int = 8) -> str:
    """Deterministic short hash so the same input always produces the same redacted token."""
    return hashlib.sha256(value.encode()).hexdigest()[:length]


def _is_allowed_domain(domain: str) -> bool:
    domain_lower = domain.lower()
    return any(domain_lower.endswith(d) or domain_lower.endswith("." + d) for d in ALLOWED_DOMAINS)


def _is_allowed_ip(ip: str) -> bool:
    return any(ip.startswith(p) for p in ALLOWED_IP_PREFIXES)


def _is_allowed_hostname(hostname: str) -> bool:
    return hostname.lower().split(".")[0] in ALLOWED_HOSTNAMES


# ---------------------------------------------------------------------------
# Redaction functions
# ---------------------------------------------------------------------------


def _redact_private_ips(text: str) -> str:
    def replacer(m: re.Match) -> str:
        ip = m.group(1)
        if _is_allowed_ip(ip):
            return ip
        return f"[REDACTED-IP-{_stable_hash(ip)}]"
    return _RE_PRIVATE_IP.sub(replacer, text)


def _redact_internal_domains(text: str) -> str:
    def replacer(m: re.Match) -> str:
        domain = m.group(0)
        if _is_allowed_domain(domain) or _is_allowed_hostname(domain):
            return domain
        return f"[REDACTED-HOST-{_stable_hash(domain)}]"
    return _RE_INTERNAL_DOMAIN.sub(replacer, text)


def _redact_local_domains(text: str) -> str:
    def replacer(m: re.Match) -> str:
        domain = m.group(0)
        if _is_allowed_hostname(domain):
            return domain
        return f"[REDACTED-HOST-{_stable_hash(domain)}]"
    return _RE_LOCAL_DOMAIN.sub(replacer, text)


def _redact_customer_names(text: str) -> str:
    def replacer(m: re.Match) -> str:
        prefix = m.group(1)
        name = m.group(2).strip()
        return f"{prefix}[REDACTED-CUSTOMER-{_stable_hash(name)}]"
    return _RE_CUSTOMER_NAME.sub(replacer, text)


def _redact_case_numbers(text: str) -> str:
    def replacer(m: re.Match) -> str:
        prefix = m.group(1)
        number = m.group(2)
        return f"{prefix}[REDACTED-CASE-{_stable_hash(number)}]"
    return _RE_CASE_NUMBER.sub(replacer, text)


def _redact_emails(text: str) -> str:
    def replacer(m: re.Match) -> str:
        email = m.group(0)
        if _RE_NOT_EMAIL.search(email):
            return email
        domain = email.split("@", 1)[1]
        if domain[0].isdigit():
            return email
        if _is_allowed_domain(domain):
            return email
        return f"[REDACTED-EMAIL-{_stable_hash(email)}]"
    return _RE_EMAIL.sub(replacer, text)


_DEFAULT_RULES = frozenset({"internal-domain", "local-domain", "customer-name", "email"})
_ALL_RULES = _DEFAULT_RULES | {"private-ip", "case-number"}

_RULE_FUNCTIONS = {
    "internal-domain": _redact_internal_domains,
    "local-domain": _redact_local_domains,
    "private-ip": _redact_private_ips,
    "customer-name": _redact_customer_names,
    "case-number": _redact_case_numbers,
    "email": _redact_emails,
}


def sanitize_text(text: str, *, rules: frozenset[str] | None = None) -> str:
    """Apply redaction rules to a string. Idempotent.

    *rules* defaults to ``_DEFAULT_RULES`` (domains, names, emails).
    Pass ``_ALL_RULES`` to include IPs and case numbers.
    """
    active = rules or _DEFAULT_RULES
    for rule_id, fn in _RULE_FUNCTIONS.items():
        if rule_id in active:
            text = fn(text)
    return text


# ---------------------------------------------------------------------------
# JSON traversal
# ---------------------------------------------------------------------------

# Fields that should NEVER be sanitized (identifiers, URLs to known services, etc.)
_SKIP_FIELDS = frozenset({
    "entity_id",
    "source",
    "source_id",
    "source_url",
    "entity_type",
    "indexed_at",
    "created_at",
    "updated_at",
    "resolved_at",
    "comment_id",
    "relation_type",
    "target_source",
    "target_id",
    "target_url",
})


def _sanitize_value(value, key: str | None = None, *, rules: frozenset[str] | None = None):
    """Recursively sanitize JSON values."""
    if key and key in _SKIP_FIELDS:
        return value

    if isinstance(value, str):
        return sanitize_text(value, rules=rules)
    if isinstance(value, list):
        return [_sanitize_value(item, rules=rules) for item in value]
    if isinstance(value, dict):
        return {k: _sanitize_value(v, key=k, rules=rules) for k, v in value.items()}
    return value


# ---------------------------------------------------------------------------
# Detection (for --check mode)
# ---------------------------------------------------------------------------

_DETECTION_PATTERNS = [
    ("private-ip", _RE_PRIVATE_IP),
    ("internal-domain", _RE_INTERNAL_DOMAIN),
    ("local-domain", _RE_LOCAL_DOMAIN),
    ("customer-name", _RE_CUSTOMER_NAME),
    ("case-number", _RE_CASE_NUMBER),
    ("email", _RE_EMAIL),
]


def detect_sensitive_data(
    text: str, *, rules: frozenset[str] | None = None
) -> list[tuple[str, str]]:
    """Return list of (rule_id, matched_value) for sensitive data found."""
    active = rules or _DEFAULT_RULES
    findings: list[tuple[str, str]] = []
    for rule_id, pattern in _DETECTION_PATTERNS:
        if rule_id not in active:
            continue
        for m in pattern.finditer(text):
            value = m.group(0)
            if rule_id == "private-ip" and _is_allowed_ip(m.group(1)):
                continue
            if rule_id in ("internal-domain", "local-domain"):
                if _is_allowed_domain(value) or _is_allowed_hostname(value):
                    continue
            if rule_id == "email":
                if _RE_NOT_EMAIL.search(value):
                    continue
                domain = value.split("@", 1)[1]
                if domain[0].isdigit():
                    continue
                if _is_allowed_domain(domain):
                    continue
            if "[REDACTED-" in value:
                continue
            findings.append((rule_id, value))
    return findings


# ---------------------------------------------------------------------------
# File processing
# ---------------------------------------------------------------------------


def find_issue_files(root: Path | None = None) -> list[Path]:
    """Find all issues.json files under the knowledge root."""
    search_root = root or KNOWLEDGE_ROOT
    return sorted(search_root.rglob("issues.json"))


def process_file(
    path: Path,
    *,
    check_only: bool = False,
    dry_run: bool = False,
    stats_only: bool = False,
    rules: frozenset[str] | None = None,
) -> dict[str, int]:
    """Process a single issues.json file. Returns counts per rule."""
    raw = path.read_text(encoding="utf-8")
    data = json.loads(raw)

    if check_only or stats_only:
        counts: dict[str, int] = {}
        for issue in data:
            for _key, value in _iter_string_fields(issue):
                for rule_id, _match in detect_sensitive_data(str(value), rules=rules):
                    counts[rule_id] = counts.get(rule_id, 0) + 1
        return counts

    sanitized = _sanitize_value(data, rules=rules)
    new_text = json.dumps(sanitized, indent=2, default=str, ensure_ascii=False)

    if raw.strip() == new_text.strip():
        return {}

    if not dry_run:
        path.write_text(new_text + "\n", encoding="utf-8")
        logger.info("Sanitized %s", path)

    counts = {}
    for issue in data:
        for _key, value in _iter_string_fields(issue):
            for rule_id, _match in detect_sensitive_data(str(value), rules=rules):
                counts[rule_id] = counts.get(rule_id, 0) + 1
    return counts


def _iter_string_fields(obj, prefix: str = "") -> list[tuple[str, str]]:
    """Yield (dotted_key, value) for all string leaves in a nested structure."""
    results: list[tuple[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            full_key = f"{prefix}.{k}" if prefix else k
            if k in _SKIP_FIELDS:
                continue
            results.extend(_iter_string_fields(v, full_key))
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            results.extend(_iter_string_fields(item, f"{prefix}[{i}]"))
    elif isinstance(obj, str):
        results.append((prefix, obj))
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Sanitize customer-sensitive data from knowledge-base JSON files."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Check mode: exit 1 if any sensitive data is found (for pre-commit).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be redacted without modifying files.",
    )
    parser.add_argument(
        "--stats",
        action="store_true",
        help="Print detection counts only, no modifications.",
    )
    parser.add_argument(
        "--path",
        type=Path,
        default=None,
        help="Specific directory or file to scan (default: knowledge/).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Enable ALL redaction rules (including private IPs and case numbers).",
    )
    parser.add_argument(
        "--redact-ips",
        action="store_true",
        help="Also redact private IPv4 addresses (10.x, 172.16-31.x, 192.168.x).",
    )
    parser.add_argument(
        "--redact-cases",
        action="store_true",
        help="Also redact support case/ticket numbers.",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging.",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s: %(message)s",
    )

    rules = set(_DEFAULT_RULES)
    if args.all:
        rules = set(_ALL_RULES)
    else:
        if args.redact_ips:
            rules.add("private-ip")
        if args.redact_cases:
            rules.add("case-number")
    active_rules = frozenset(rules)

    if args.path and args.path.is_file():
        files = [args.path]
    else:
        files = find_issue_files(args.path)

    if not files:
        logger.info("No issues.json files found.")
        return 0

    total_counts: dict[str, int] = {}
    dirty_files: list[Path] = []

    for fpath in files:
        logger.info("Scanning %s ...", fpath.relative_to(KNOWLEDGE_ROOT.parent))
        counts = process_file(
            fpath,
            check_only=args.check,
            dry_run=args.dry_run,
            stats_only=args.stats,
            rules=active_rules,
        )
        if counts:
            dirty_files.append(fpath)
            for rule_id, count in counts.items():
                total_counts[rule_id] = total_counts.get(rule_id, 0) + count

    if total_counts:
        print("\n--- Sensitive data findings ---")
        for rule_id, count in sorted(total_counts.items()):
            print(f"  {rule_id}: {count}")
        print(f"\n  Total: {sum(total_counts.values())} findings in {len(dirty_files)} file(s)")

        if args.check:
            print(
                "\nFAILED: Unsanitized customer data detected.\n"
                "Run 'python scripts/sanitize_issues.py' to redact before committing."
            )
            return 1

        if args.dry_run:
            print("\n(dry-run mode: no files were modified)")
        elif not args.stats:
            print(f"\nRedacted {sum(total_counts.values())} items across {len(dirty_files)} file(s).")
    else:
        print("No sensitive data found. All clean.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
