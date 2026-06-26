#!/usr/bin/env python3
"""Workflow C: Config validation edge cases.

Tests loading configs with missing files, invalid YAML, missing fields,
and environment variable behavior.
"""
import os
import sys
import tempfile
sys.path.insert(0, "src")

from ceph_issue_kb.config import load_config, ConnectorConfig, AuthConfig

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


print("=" * 80)
print("WORKFLOW C: Config Validation Edge Cases")
print("=" * 80)

# ---------------------------------------------------------------------------
# 1. Missing file
# ---------------------------------------------------------------------------
print("\n--- Test 1: Load config with missing file ---")
try:
    load_config("/tmp/absolutely_does_not_exist_ceph_kb_test.yaml")
    check("Missing file raises FileNotFoundError", False)
except FileNotFoundError as e:
    print(f"  FileNotFoundError: {e}")
    check("Missing file raises FileNotFoundError", True)
except Exception as e:
    print(f"  Unexpected: {type(e).__name__}: {e}")
    check("Missing file raises FileNotFoundError", False)

# ---------------------------------------------------------------------------
# 2. Invalid YAML
# ---------------------------------------------------------------------------
print("\n--- Test 2: Load config with invalid YAML ---")
with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
    f.write("connectors:\n  test:\n    - this is broken: [yaml: {{{\n")
    bad_yaml_path = f.name
try:
    load_config(bad_yaml_path)
    print("  No error raised - YAML was lenient")
    check("Invalid YAML handled", True)
except Exception as e:
    print(f"  {type(e).__name__}: {e}")
    check("Invalid YAML raises error", True)
finally:
    os.unlink(bad_yaml_path)

# ---------------------------------------------------------------------------
# 3. Missing 'connectors' key
# ---------------------------------------------------------------------------
print("\n--- Test 3: Config missing 'connectors' key ---")
with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
    f.write("sources:\n  a:\n    type: redmine\n")
    bad_key_path = f.name
try:
    load_config(bad_key_path)
    check("Missing 'connectors' key raises ValueError", False)
except ValueError as e:
    print(f"  ValueError: {e}")
    check("Missing 'connectors' key raises ValueError", True)
    check("Error message mentions 'connectors'", "connectors" in str(e))
except Exception as e:
    print(f"  Unexpected: {type(e).__name__}: {e}")
    check("Missing 'connectors' key raises ValueError", False)
finally:
    os.unlink(bad_key_path)

# ---------------------------------------------------------------------------
# 4. Empty connectors dict
# ---------------------------------------------------------------------------
print("\n--- Test 4: Config with empty connectors ---")
with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
    f.write("connectors:\n  empty-conn:\n    type: redmine\n    base_url: https://example.com\n")
    empty_conn_path = f.name
try:
    cfg = load_config(empty_conn_path)
    print(f"  Loaded {len(cfg.connectors)} connector(s)")
    cc = cfg.connectors["empty-conn"]
    print(f"  name={cc.name}, type={cc.connector_type}, enabled={cc.enabled}, "
          f"rate_limit={cc.rate_limit}, since={cc.since}")
    check("Minimal connector config loads", cc.connector_type == "redmine")
    check("Defaults applied: enabled=True", cc.enabled is True)
    check("Defaults applied: rate_limit=10", cc.rate_limit == 10)
    check("Defaults applied: since='2024-01-01'", cc.since == "2024-01-01")
finally:
    os.unlink(empty_conn_path)

# ---------------------------------------------------------------------------
# 5. Config with all fields explicitly set
# ---------------------------------------------------------------------------
print("\n--- Test 5: Config with all fields explicitly set ---")
with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
    f.write(
        "connectors:\n"
        "  full-conn:\n"
        "    type: redmine\n"
        "    enabled: false\n"
        "    base_url: https://custom.tracker.io/\n"
        "    auth:\n"
        "      method: api_key\n"
        "      key_env: MY_API_KEY\n"
        "    rate_limit: 42\n"
        "    since: '2020-06-15'\n"
        "    project: my-project\n"
        "    custom_field: custom_value\n"
    )
    full_path = f.name
try:
    cfg = load_config(full_path)
    cc = cfg.connectors["full-conn"]
    print(f"  type={cc.connector_type}, enabled={cc.enabled}, base_url={cc.base_url}")
    print(f"  auth.method={cc.auth.method}, auth.key_env={cc.auth.key_env}")
    print(f"  rate_limit={cc.rate_limit}, since={cc.since}")
    print(f"  extra={cc.extra}")
    check("type = 'redmine'", cc.connector_type == "redmine")
    check("enabled = False", cc.enabled is False)
    check("base_url trailing slash stripped", cc.base_url == "https://custom.tracker.io")
    check("auth.method = 'api_key'", cc.auth.method == "api_key")
    check("auth.key_env = 'MY_API_KEY'", cc.auth.key_env == "MY_API_KEY")
    check("rate_limit = 42", cc.rate_limit == 42)
    check("since = '2020-06-15'", cc.since == "2020-06-15")
    check("extra has project", cc.extra.get("project") == "my-project")
    check("extra has custom_field", cc.extra.get("custom_field") == "custom_value")
    check("Disabled connector not in enabled_connectors", "full-conn" not in cfg.enabled_connectors)
finally:
    os.unlink(full_path)

# ---------------------------------------------------------------------------
# 6. Multiple connectors: mix of enabled/disabled
# ---------------------------------------------------------------------------
print("\n--- Test 6: Multiple connectors - enabled/disabled filtering ---")
with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
    f.write(
        "connectors:\n"
        "  conn-a:\n"
        "    type: redmine\n"
        "    enabled: true\n"
        "    base_url: https://a.com\n"
        "  conn-b:\n"
        "    type: jira\n"
        "    enabled: false\n"
        "    base_url: https://b.com\n"
        "  conn-c:\n"
        "    type: bugzilla\n"
        "    enabled: true\n"
        "    base_url: https://c.com\n"
        "  conn-d:\n"
        "    type: rhkb\n"
        "    enabled: false\n"
        "    base_url: https://d.com\n"
    )
    multi_path = f.name
try:
    cfg = load_config(multi_path)
    print(f"  Total: {len(cfg.connectors)}")
    print(f"  Enabled: {list(cfg.enabled_connectors.keys())}")
    check("4 total connectors", len(cfg.connectors) == 4)
    check("2 enabled connectors", len(cfg.enabled_connectors) == 2)
    check("conn-a enabled", "conn-a" in cfg.enabled_connectors)
    check("conn-b disabled", "conn-b" not in cfg.enabled_connectors)
    check("conn-c enabled", "conn-c" in cfg.enabled_connectors)
    check("conn-d disabled", "conn-d" not in cfg.enabled_connectors)
finally:
    os.unlink(multi_path)

# ---------------------------------------------------------------------------
# 7. ConnectorConfig.from_dict with missing optional fields
# ---------------------------------------------------------------------------
print("\n--- Test 7: ConnectorConfig.from_dict with bare minimum ---")
cc = ConnectorConfig.from_dict("bare", {"type": "something"})
print(f"  name={cc.name}, type={cc.connector_type}, enabled={cc.enabled}")
print(f"  base_url={cc.base_url!r}, rate_limit={cc.rate_limit}, since={cc.since}")
print(f"  auth.method={cc.auth.method}, extra={cc.extra}")
check("Bare minimum loads", cc.connector_type == "something")
check("base_url defaults to ''", cc.base_url == "")
check("auth defaults to 'none'", cc.auth.method == "none")

# ---------------------------------------------------------------------------
# 8. AuthConfig edge cases
# ---------------------------------------------------------------------------
print("\n--- Test 8: AuthConfig edge cases ---")
auth_none = AuthConfig.from_dict(None)
check("AuthConfig.from_dict(None) -> method='none'", auth_none.method == "none")

auth_empty = AuthConfig.from_dict({})
check("AuthConfig.from_dict({}) -> method='none'", auth_empty.method == "none")

auth_cookie = AuthConfig.from_dict({"method": "cookie", "cookie_env": "MY_COOKIE"})
check("Cookie auth method", auth_cookie.method == "cookie")
check("Cookie env set", auth_cookie.cookie_env == "MY_COOKIE")

# ---------------------------------------------------------------------------
print("\n" + "=" * 80)
print(f"WORKFLOW C SUMMARY: {PASS} passed, {FAIL} failed")
print("=" * 80)
sys.exit(1 if FAIL else 0)
