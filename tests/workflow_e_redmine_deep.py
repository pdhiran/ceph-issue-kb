#!/usr/bin/env python3
"""Workflow E: Redmine connector deep test (fully mocked HTTP).

Tests fetch, search pagination, fetch_updates, health, error handling,
and rate limiting — all without real network calls.
"""
import json
import sys
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, "src")

import responses
from responses import matchers

from ceph_issue_kb.config import AuthConfig, ConnectorConfig
from ceph_issue_kb.connectors.redmine import RedmineConnector, PAGE_SIZE
from ceph_issue_kb.connectors.base import ConnectorError

FIXTURES = Path("tests/fixtures")
PASS = 0
FAIL = 0

def check(label, condition):
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [PASS] {label}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label}")


def make_config(rate_limit=100):
    return ConnectorConfig(
        name="ceph-tracker",
        connector_type="redmine",
        enabled=True,
        base_url="https://tracker.ceph.com",
        auth=AuthConfig(method="none"),
        rate_limit=rate_limit,
        since="2024-01-01",
        extra={"project": "ceph"},
    )


print("=" * 80)
print("WORKFLOW E: Redmine Connector Deep Test")
print("=" * 80)

# ---------------------------------------------------------------------------
# 1. Fetch with fixture data — verify all field mapping
# ---------------------------------------------------------------------------
print("\n--- Test 1: fetch() with fixture data - field mapping ---")
fixture = json.loads((FIXTURES / "redmine_issue.json").read_text())

@responses.activate
def test_fetch_mapping():
    responses.add(responses.GET, "https://tracker.ceph.com/issues/68051.json",
                  json=fixture, status=200)
    conn = RedmineConnector(make_config())
    raw = conn.fetch("68051")

    print(f"  source: {raw.source}")
    print(f"  source_id: {raw.source_id}")
    print(f"  source_url: {raw.source_url}")
    print(f"  data keys: {sorted(raw.data.keys())}")
    print(f"  subject: {raw.data.get('subject')}")
    print(f"  journals count: {len(raw.data.get('journals', []))}")
    print(f"  relations count: {len(raw.data.get('relations', []))}")

    check("source == 'ceph-tracker'", raw.source == "ceph-tracker")
    check("source_id == '68051'", raw.source_id == "68051")
    check("source_url correct", raw.source_url == "https://tracker.ceph.com/issues/68051")
    check("subject mapped", raw.data["subject"] == "OSD crash during deep scrub with BlueStore")
    check("status mapped", raw.data["status"]["name"] == "New")
    check("priority mapped", raw.data["priority"]["name"] == "Normal")
    check("author mapped", raw.data["author"]["name"] == "Jane Dev")
    check("2 journals", len(raw.data["journals"]) == 2)
    check("1 relation", len(raw.data["relations"]) == 1)
    check("journal[0] author", raw.data["journals"][0]["user"]["name"] == "John Reviewer")
    check("relation type", raw.data["relations"][0]["relation_type"] == "related")
    check("custom_fields present", "custom_fields" in raw.data)
    check("description present", "description" in raw.data)

test_fetch_mapping()

# ---------------------------------------------------------------------------
# 2. Search pagination - mock multiple pages
# ---------------------------------------------------------------------------
print("\n--- Test 2: search() pagination - multi-page ---")

@responses.activate
def test_search_pagination():
    page1 = {
        "issues": [{"id": i, "subject": f"Issue {i}"} for i in range(1, 4)],
        "total_count": 5, "offset": 0, "limit": 3,
    }
    page2 = {
        "issues": [{"id": i, "subject": f"Issue {i}"} for i in range(4, 6)],
        "total_count": 5, "offset": 3, "limit": 3,
    }
    responses.add(responses.GET, "https://tracker.ceph.com/issues.json",
                  json=page1, status=200)
    responses.add(responses.GET, "https://tracker.ceph.com/issues.json",
                  json=page2, status=200)

    for i in range(1, 6):
        detail = {"issue": {"id": i, "subject": f"Issue {i}", "journals": [], "relations": []}}
        responses.add(responses.GET, f"https://tracker.ceph.com/issues/{i}.json",
                      json=detail, status=200)

    conn = RedmineConnector(make_config())
    results = list(conn.search("test", limit=10))
    ids = [r.source_id for r in results]
    print(f"  Results: {len(results)} issues")
    print(f"  IDs: {ids}")
    check("5 total results from 2 pages", len(results) == 5)
    check("IDs match", ids == ["1", "2", "3", "4", "5"])

test_search_pagination()

# ---------------------------------------------------------------------------
# 3. search() with limit less than total
# ---------------------------------------------------------------------------
print("\n--- Test 3: search() with limit < total ---")

@responses.activate
def test_search_with_limit():
    page = {
        "issues": [{"id": i, "subject": f"Issue {i}"} for i in range(1, 6)],
        "total_count": 20, "offset": 0, "limit": 5,
    }
    responses.add(responses.GET, "https://tracker.ceph.com/issues.json",
                  json=page, status=200)
    for i in range(1, 4):
        detail = {"issue": {"id": i, "subject": f"Issue {i}", "journals": [], "relations": []}}
        responses.add(responses.GET, f"https://tracker.ceph.com/issues/{i}.json",
                      json=detail, status=200)

    conn = RedmineConnector(make_config())
    results = list(conn.search("test", limit=3))
    print(f"  Requested limit=3, got {len(results)} results")
    check("Limit=3 returns exactly 3", len(results) == 3)

test_search_with_limit()

# ---------------------------------------------------------------------------
# 4. fetch_updates() with since parameter
# ---------------------------------------------------------------------------
print("\n--- Test 4: fetch_updates() with since parameter ---")

@responses.activate
def test_fetch_updates():
    page = {
        "issues": [
            {"id": 100, "subject": "Updated issue 100"},
            {"id": 101, "subject": "Updated issue 101"},
        ],
        "total_count": 2, "offset": 0, "limit": 100,
    }
    responses.add(responses.GET, "https://tracker.ceph.com/issues.json",
                  json=page, status=200)
    for i in [100, 101]:
        detail = {"issue": {"id": i, "subject": f"Updated issue {i}", "journals": [], "relations": []}}
        responses.add(responses.GET, f"https://tracker.ceph.com/issues/{i}.json",
                      json=detail, status=200)

    conn = RedmineConnector(make_config())
    results = list(conn.fetch_updates("2024-10-01"))
    print(f"  Updates since 2024-10-01: {len(results)} issues")
    print(f"  IDs: {[r.source_id for r in results]}")
    check("2 updated issues returned", len(results) == 2)

    calls = [c.request for c in responses.calls]
    list_call = calls[0]
    print(f"  List request URL: {list_call.url}")
    check("updated_on param in request", "updated_on" in list_call.url)

test_fetch_updates()

# ---------------------------------------------------------------------------
# 5. health() endpoint
# ---------------------------------------------------------------------------
print("\n--- Test 5: health() endpoint ---")

@responses.activate
def test_health_ok():
    responses.add(responses.GET, "https://tracker.ceph.com/issues.json",
                  json={"issues": [], "total_count": 12345, "offset": 0, "limit": 1},
                  status=200)
    conn = RedmineConnector(make_config())
    h = conn.health()
    print(f"  Health response: {h}")
    check("health ok=True", h["ok"] is True)
    check("health total_issues=12345", h["total_issues"] == 12345)
    check("health has source", h["source"] == "ceph-tracker")
    check("health message mentions 'Connected'", "Connected" in h["message"])

test_health_ok()

@responses.activate
def test_health_failure():
    responses.add(responses.GET, "https://tracker.ceph.com/issues.json", status=503)
    conn = RedmineConnector(make_config())
    h = conn.health()
    print(f"  Failed health response: {h}")
    check("health ok=False on 503", h["ok"] is False)
    check("health total_issues=0 on failure", h["total_issues"] == 0)

test_health_failure()

# ---------------------------------------------------------------------------
# 6. Error handling: HTTP 404, 500, timeout, malformed JSON
# ---------------------------------------------------------------------------
print("\n--- Test 6: Error handling ---")

@responses.activate
def test_http_404():
    responses.add(responses.GET, "https://tracker.ceph.com/issues/99999.json", status=404)
    conn = RedmineConnector(make_config())
    try:
        conn.fetch("99999")
        check("HTTP 404 raises ConnectorError", False)
    except ConnectorError as e:
        print(f"  404 error: {e}")
        check("HTTP 404 raises ConnectorError", True)

test_http_404()

@responses.activate
def test_http_500():
    responses.add(responses.GET, "https://tracker.ceph.com/issues/11111.json", status=500)
    conn = RedmineConnector(make_config())
    try:
        conn.fetch("11111")
        check("HTTP 500 raises ConnectorError", False)
    except ConnectorError as e:
        print(f"  500 error: {e}")
        check("HTTP 500 raises ConnectorError", True)

test_http_500()

@responses.activate
def test_malformed_json():
    responses.add(responses.GET, "https://tracker.ceph.com/issues/22222.json",
                  body="<html>not json</html>", status=200,
                  content_type="text/html")
    conn = RedmineConnector(make_config())
    try:
        conn.fetch("22222")
        check("Malformed JSON raises ConnectorError", False)
    except ConnectorError as e:
        print(f"  Malformed JSON error: {e}")
        check("Malformed JSON raises ConnectorError", True)
    except Exception as e:
        print(f"  Unexpected error type: {type(e).__name__}: {e}")
        check("Malformed JSON raises ConnectorError (got unexpected type)", False)

test_malformed_json()

@responses.activate
def test_timeout():
    import requests as req
    responses.add(responses.GET, "https://tracker.ceph.com/issues/33333.json",
                  body=req.exceptions.Timeout("Connection timed out"))
    conn = RedmineConnector(make_config())
    try:
        conn.fetch("33333")
        check("Timeout raises ConnectorError", False)
    except ConnectorError as e:
        print(f"  Timeout error: {e}")
        check("Timeout raises ConnectorError", True)

test_timeout()

@responses.activate
def test_connection_error():
    import requests as req
    responses.add(responses.GET, "https://tracker.ceph.com/issues/44444.json",
                  body=req.exceptions.ConnectionError("DNS resolution failed"))
    conn = RedmineConnector(make_config())
    try:
        conn.fetch("44444")
        check("ConnectionError raises ConnectorError", False)
    except ConnectorError as e:
        print(f"  Connection error: {e}")
        check("ConnectionError raises ConnectorError", True)

test_connection_error()

# ---------------------------------------------------------------------------
# 7. Rate limiting logic
# ---------------------------------------------------------------------------
print("\n--- Test 7: Rate limiting / throttle logic ---")

def test_rate_limit():
    cfg = make_config(rate_limit=1)
    conn = RedmineConnector(cfg)
    print(f"  rate_limit={cfg.rate_limit}, min_interval={conn._min_interval}s")
    check("min_interval >= 1s for rate_limit=1", conn._min_interval >= 1.0)

    cfg_fast = make_config(rate_limit=100)
    conn_fast = RedmineConnector(cfg_fast)
    print(f"  rate_limit={cfg_fast.rate_limit}, min_interval={conn_fast._min_interval}s")
    check("min_interval = 1.0 for rate_limit=100 (floor)", conn_fast._min_interval == 1.0)

    cfg_slow = make_config(rate_limit=1)
    conn_slow = RedmineConnector(cfg_slow)
    conn_slow._last_request = time.monotonic()
    t0 = time.monotonic()
    conn_slow._throttle()
    elapsed = time.monotonic() - t0
    print(f"  Throttle waited {elapsed:.3f}s (expected ~1.0s)")
    check("Throttle enforces minimum interval", elapsed >= 0.9)

test_rate_limit()

# ---------------------------------------------------------------------------
# 8. Pagination skips failed fetches gracefully
# ---------------------------------------------------------------------------
print("\n--- Test 8: Pagination skips failed individual fetches ---")

@responses.activate
def test_pagination_skip_failures():
    page = {
        "issues": [
            {"id": 200, "subject": "Good issue"},
            {"id": 201, "subject": "Bad issue"},
            {"id": 202, "subject": "Another good issue"},
        ],
        "total_count": 3, "offset": 0, "limit": 100,
    }
    responses.add(responses.GET, "https://tracker.ceph.com/issues.json",
                  json=page, status=200)
    responses.add(responses.GET, "https://tracker.ceph.com/issues/200.json",
                  json={"issue": {"id": 200, "subject": "Good issue", "journals": [], "relations": []}},
                  status=200)
    responses.add(responses.GET, "https://tracker.ceph.com/issues/201.json",
                  status=500)
    responses.add(responses.GET, "https://tracker.ceph.com/issues/202.json",
                  json={"issue": {"id": 202, "subject": "Another good issue", "journals": [], "relations": []}},
                  status=200)

    conn = RedmineConnector(make_config())
    results = list(conn.search("test", limit=10))
    ids = [r.source_id for r in results]
    print(f"  Results (should skip 201): {ids}")
    check("Got 2 results (skipped failed fetch)", len(results) == 2)
    check("201 not in results", "201" not in ids)
    check("200 and 202 present", "200" in ids and "202" in ids)

test_pagination_skip_failures()

# ---------------------------------------------------------------------------
print("\n" + "=" * 80)
print(f"WORKFLOW E SUMMARY: {PASS} passed, {FAIL} failed")
print("=" * 80)
sys.exit(1 if FAIL else 0)
