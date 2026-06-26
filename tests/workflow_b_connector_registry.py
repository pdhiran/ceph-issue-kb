#!/usr/bin/env python3
"""Workflow B: Connector registry and factory tests.

Tests creating connectors from config, registry behavior, and attribute mapping.
"""
import sys
sys.path.insert(0, "src")

from ceph_issue_kb.config import AuthConfig, ConnectorConfig, load_config
from ceph_issue_kb.connectors import get_connector, _CONNECTOR_TYPES, ConnectorError
from ceph_issue_kb.connectors.redmine import RedmineConnector
from ceph_issue_kb.connectors.base import BaseConnector

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
print("WORKFLOW B: Connector Registry and Factory")
print("=" * 80)

# ---------------------------------------------------------------------------
# 1. Load real config and get all enabled connectors
# ---------------------------------------------------------------------------
print("\n--- Test 1: Load real config and get enabled connectors ---")
cfg = load_config("connectors.yaml")
print(f"  Total connectors in config: {len(cfg.connectors)}")
print(f"  Enabled connectors: {list(cfg.enabled_connectors.keys())}")
print(f"  Disabled connectors: {[k for k, v in cfg.connectors.items() if not v.enabled]}")
check("Config has connectors", len(cfg.connectors) > 0)
check("At least one enabled", len(cfg.enabled_connectors) > 0)
check("ceph-tracker is enabled", "ceph-tracker" in cfg.enabled_connectors)

# ---------------------------------------------------------------------------
# 2. Create connector from each enabled config
# ---------------------------------------------------------------------------
print("\n--- Test 2: Create connector from each enabled config ---")
for name, cc in cfg.enabled_connectors.items():
    try:
        connector = get_connector(cc)
        print(f"  Created {name}: {type(connector).__name__}")
        check(f"{name} creates valid connector", isinstance(connector, BaseConnector))
    except Exception as e:
        print(f"  ERROR creating {name}: {e}")
        check(f"{name} creates valid connector", False)

# ---------------------------------------------------------------------------
# 3. Try to get a disabled connector (jira type not implemented)
# ---------------------------------------------------------------------------
print("\n--- Test 3: Attempt to create connector for disabled/unimplemented type ---")
disabled_configs = {k: v for k, v in cfg.connectors.items() if not v.enabled}
for name, cc in disabled_configs.items():
    print(f"  Trying disabled connector '{name}' (type={cc.connector_type})...")
    try:
        connector = get_connector(cc)
        print(f"    Result: Created {type(connector).__name__} (type was implemented)")
        check(f"{name} factory call succeeded", True)
    except ConnectorError as e:
        print(f"    Result: ConnectorError raised (expected for unimplemented type)")
        print(f"    Message: {e}")
        check(f"{name} raises ConnectorError for unknown type", "Unknown connector type" in str(e))
    except Exception as e:
        print(f"    Result: Unexpected error: {type(e).__name__}: {e}")
        check(f"{name} raises expected error", False)

# ---------------------------------------------------------------------------
# 4. Test the registry
# ---------------------------------------------------------------------------
print("\n--- Test 4: Connector type registry ---")
print(f"  Registered types: {list(_CONNECTOR_TYPES.keys())}")
check("'redmine' in registry", "redmine" in _CONNECTOR_TYPES)
check("RedmineConnector is the redmine class", _CONNECTOR_TYPES["redmine"] is RedmineConnector)
check("All registry values are BaseConnector subclasses",
      all(issubclass(cls, BaseConnector) for cls in _CONNECTOR_TYPES.values()))

# ---------------------------------------------------------------------------
# 5. Verify RedmineConnector attributes match config
# ---------------------------------------------------------------------------
print("\n--- Test 5: RedmineConnector attribute verification ---")
redmine_cfg = cfg.connectors["ceph-tracker"]
connector = get_connector(redmine_cfg)
print(f"  connector.name      = {connector.name!r}  (expected: {redmine_cfg.name!r})")
print(f"  connector.base_url  = {connector.base_url!r}  (expected: {redmine_cfg.base_url!r})")
print(f"  connector.rate_limit= {connector.rate_limit!r}  (expected: {redmine_cfg.rate_limit!r})")
print(f"  connector.project   = {connector.project!r}  (expected: {redmine_cfg.extra.get('project')!r})")
check("name matches config", connector.name == redmine_cfg.name)
check("base_url matches config", connector.base_url == redmine_cfg.base_url)
check("rate_limit matches config", connector.rate_limit == redmine_cfg.rate_limit)
check("project matches config extra", connector.project == redmine_cfg.extra.get("project"))

# ---------------------------------------------------------------------------
# 6. Factory with a completely bogus type
# ---------------------------------------------------------------------------
print("\n--- Test 6: Factory with nonexistent connector type ---")
bogus_cfg = ConnectorConfig(
    name="fake", connector_type="nosql_magic", enabled=True,
    base_url="https://example.com", auth=AuthConfig(method="none"),
)
try:
    get_connector(bogus_cfg)
    check("Bogus type raises ConnectorError", False)
except ConnectorError as e:
    print(f"  ConnectorError: {e}")
    check("Bogus type raises ConnectorError", True)
    check("Error message lists available types", "redmine" in str(e))

# ---------------------------------------------------------------------------
# 7. Verify connector stores config reference
# ---------------------------------------------------------------------------
print("\n--- Test 7: Connector stores config reference ---")
connector = get_connector(redmine_cfg)
check("connector.config is the passed config", connector.config is redmine_cfg)
check("connector._credentials exists", hasattr(connector, "_credentials"))
print(f"  credentials.method = {connector._credentials.method}")
check("credentials.method is 'none' for public tracker", connector._credentials.method == "none")

# ---------------------------------------------------------------------------
print("\n" + "=" * 80)
print(f"WORKFLOW B SUMMARY: {PASS} passed, {FAIL} failed")
print("=" * 80)
sys.exit(1 if FAIL else 0)
