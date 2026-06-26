#!/usr/bin/env python3
"""Workflow A: Edge case signal extraction.

Tests the signal extractor against tricky real-world Ceph text samples.
"""
import sys
sys.path.insert(0, "src")

from ceph_issue_kb.signal_extractor import extract_signals

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
print("WORKFLOW A: Edge-Case Signal Extraction")
print("=" * 80)

# ---------------------------------------------------------------------------
# 1. Multi-line C++ stacktrace with ceph:: namespaces and hex addresses
# ---------------------------------------------------------------------------
print("\n--- Test 1: Multi-line C++ stacktrace with ceph:: namespaces ---")
cpp_stack = """\
Thread 1 (Thread 0x7f5b2e4a1700 (LWP 12345)):
#0  0x00007f5b2e4a1234 in __GI_raise (sig=sig@entry=6) at ../sysdeps/unix/sysv/linux/raise.c:51
#1  0x00007f5b2d3b5678 in ceph::buffer::list::rebuild() at src/common/buffer.cc:1234
#2  0x00007f5b2d3b9abc in ceph::BlueStore::_do_write(ceph::Transaction&) at src/os/bluestore/BlueStore.cc:5678
#3  0x00007f5b2d3bfdef in ceph::OSD::ShardedOpWQ::_process(uint32_t, ceph::heartbeat_handle_d*) at src/osd/OSD.cc:9012
#4  0x00007f5b2d1a0123 in ceph::ThreadPool::worker(ceph::ThreadPool::WorkThread*) at src/common/WorkQueue.cc:70
#5  0x00007f5b2c890456 in start_thread (arg=<optimized out>) at pthread_create.c:477
Segmentation fault (core dumped)
"""
signals = extract_signals(cpp_stack)
print(f"  Stacktraces found: {len(signals.stacktraces)}")
for i, s in enumerate(signals.stacktraces):
    print(f"    [{i}] {s[:100]}...")
check("C++ stacktrace frames detected", len(signals.stacktraces) >= 3)
check("Segfault detected", any("Segmentation fault" in s for s in signals.stacktraces))
check("core dumped detected", any("core dumped" in s for s in signals.stacktraces))

# ---------------------------------------------------------------------------
# 2. Nested Python traceback (exception chaining)
# ---------------------------------------------------------------------------
print("\n--- Test 2: Nested Python traceback with exception chaining ---")
nested_python_tb = """\
Traceback (most recent call last):
  File "/usr/lib/python3/dist-packages/ceph_volume/util/disk.py", line 89, in get_devices
    data = json.loads(out)
json.JSONDecodeError: Expecting value: line 1 column 1 (char 0)

During handling of the above exception, another exception occurred:

Traceback (most recent call last):
  File "/usr/lib/python3/dist-packages/ceph_volume/main.py", line 42, in main
    command.main()
  File "/usr/lib/python3/dist-packages/ceph_volume/devices/lvm/activate.py", line 310, in main
    activate(args)
  File "/usr/lib/python3/dist-packages/ceph_volume/devices/lvm/activate.py", line 280, in activate
    osd = prepare.Prepare.get_osd(args.osd_id)
RuntimeError: Unable to activate OSD
"""
signals = extract_signals(nested_python_tb)
print(f"  Stacktraces found: {len(signals.stacktraces)}")
for i, s in enumerate(signals.stacktraces):
    lines = s.split("\n")
    print(f"    [{i}] ({len(lines)} lines) {lines[0][:80]}...")
check("At least one Python traceback captured", len(signals.stacktraces) >= 1)
check("File references captured", any("File" in s for s in signals.stacktraces))

# ---------------------------------------------------------------------------
# 3. Multiple HEALTH_WARN and HEALTH_ERR on same line
# ---------------------------------------------------------------------------
print("\n--- Test 3: Multiple health statuses on same line ---")
multi_health = "HEALTH_WARN 3 pgs degraded; HEALTH_ERR 2 osds down; OSD_DOWN osd.3 osd.7; PG_DEGRADED; SLOW_OPS; RECENT_CRASH"
signals = extract_signals(multi_health)
print(f"  Health warnings found: {signals.health_warnings}")
check("HEALTH_WARN found", "HEALTH_WARN" in signals.health_warnings)
check("HEALTH_ERR found", "HEALTH_ERR" in signals.health_warnings)
check("OSD_DOWN found", "OSD_DOWN" in signals.health_warnings)
check("PG_DEGRADED found", "PG_DEGRADED" in signals.health_warnings)
check("SLOW_OPS found", "SLOW_OPS" in signals.health_warnings)
check("RECENT_CRASH found", "RECENT_CRASH" in signals.health_warnings)
check("All 6 health statuses found", len(signals.health_warnings) == 6)

# ---------------------------------------------------------------------------
# 4. Edge cases: empty string, no signals, whitespace only
# ---------------------------------------------------------------------------
print("\n--- Test 4: Edge cases - empty, no signals, whitespace ---")
signals_empty = extract_signals("")
print(f"  Empty string -> stacktraces={signals_empty.stacktraces}, health={signals_empty.health_warnings}, "
      f"cmds={signals_empty.commands_mentioned}")
check("Empty string: no stacktraces", signals_empty.stacktraces == [])
check("Empty string: no health warnings", signals_empty.health_warnings == [])
check("Empty string: no commands", signals_empty.commands_mentioned == [])
check("Empty string: no configs", signals_empty.configs_mentioned == [])
check("Empty string: no log snippets", signals_empty.log_snippets == [])
check("Empty string: no assertions", signals_empty.assertions == [])

signals_nosig = extract_signals("This is a perfectly normal sentence about nothing in particular.")
print(f"  No signals -> stacktraces={signals_nosig.stacktraces}, health={signals_nosig.health_warnings}")
check("Normal text: no stacktraces", signals_nosig.stacktraces == [])
check("Normal text: no health warnings", signals_nosig.health_warnings == [])
check("Normal text: no commands", signals_nosig.commands_mentioned == [])

signals_ws = extract_signals("   \n\n\t\t  \n   ")
print(f"  Whitespace only -> all empty: stacktraces={signals_ws.stacktraces}")
check("Whitespace: no stacktraces", signals_ws.stacktraces == [])
check("Whitespace: no health warnings", signals_ws.health_warnings == [])

# ---------------------------------------------------------------------------
# 5. OSD map flags in text
# ---------------------------------------------------------------------------
print("\n--- Test 5: OSD map flags mentioned in text ---")
osd_flags_text = """\
The cluster has the following flags set:
ceph osd set noout
ceph osd set norebalance
ceph osd set noscrub
ceph osd set nodeep-scrub
The osd_pool_default_size is 3 and osd_pool_default_min_size is 2.
bluestore_cache_autotune is enabled.
"""
signals = extract_signals(osd_flags_text)
print(f"  Commands found: {signals.commands_mentioned}")
print(f"  Configs found: {signals.configs_mentioned}")
check("'ceph osd set noout' detected as command",
      any("ceph osd set noout" in c for c in signals.commands_mentioned))
check("'ceph osd set norebalance' detected",
      any("norebalance" in c for c in signals.commands_mentioned))
check("osd_pool_default_size as config", "osd_pool_default_size" in signals.configs_mentioned)
check("osd_pool_default_min_size as config", "osd_pool_default_min_size" in signals.configs_mentioned)
check("bluestore_cache_autotune as config", "bluestore_cache_autotune" in signals.configs_mentioned)

# ---------------------------------------------------------------------------
# 6. cephadm commands
# ---------------------------------------------------------------------------
print("\n--- Test 6: cephadm commands ---")
cephadm_text = """\
To bootstrap the cluster run:
sudo cephadm bootstrap --mon-ip 10.0.0.1 --cluster-network 10.0.1.0/24
Then enter the cephadm shell:
cephadm shell -- ceph status
Also you can add hosts:
cephadm shell -- ceph orch host add node2
"""
signals = extract_signals(cephadm_text)
print(f"  Commands found: {signals.commands_mentioned}")
check("cephadm bootstrap detected",
      any("cephadm bootstrap" in c for c in signals.commands_mentioned))
check("cephadm shell detected",
      any("cephadm shell" in c for c in signals.commands_mentioned))

# ---------------------------------------------------------------------------
# 7. RGW-specific commands
# ---------------------------------------------------------------------------
print("\n--- Test 7: RGW-specific commands ---")
rgw_text = """\
Create the RGW user:
radosgw-admin user create --uid=testuser --display-name="Test User"
Check bucket stats:
radosgw-admin bucket stats --bucket=mybucket
Check RGW config:
rgw_dns_name = s3.example.com
rgw_thread_pool_size = 512
"""
signals = extract_signals(rgw_text)
print(f"  Commands found: {signals.commands_mentioned}")
print(f"  Configs found: {signals.configs_mentioned}")
check("radosgw-admin user create detected",
      any("radosgw-admin user create" in c for c in signals.commands_mentioned))
check("radosgw-admin bucket stats detected",
      any("radosgw-admin bucket stats" in c for c in signals.commands_mentioned))
check("rgw_dns_name as config", "rgw_dns_name" in signals.configs_mentioned)
check("rgw_thread_pool_size as config", "rgw_thread_pool_size" in signals.configs_mentioned)

# ---------------------------------------------------------------------------
# 8. Combined stress: all signal types in one block
# ---------------------------------------------------------------------------
print("\n--- Test 8: Combined text with all signal types ---")
combined = """\
2024-11-01T09:15:30.123456 7f5b2e4a1234 -1 osd.5 shutting down
HEALTH_WARN too many PGs per OSD
HEALTH_ERR 1 osd down
#0  0x00007f5b2e4a1234 in ceph_abort() at src/common/assert.cc:100
ceph_assert_fail: expected offset < length in BlueStore::read
ceph osd pool set testpool size 3
osd_heartbeat_grace = 20
bluestore_min_alloc_size_ssd = 4096
Traceback (most recent call last):
  File "/opt/ceph/mgr/dashboard/controllers/osd.py", line 55, in list
    result = CephService.send_command('mon', 'osd dump')
RuntimeError: mon command failed
"""
signals = extract_signals(combined)
print(f"  Stacktraces: {len(signals.stacktraces)}")
print(f"  Assertions: {len(signals.assertions)}")
print(f"  Health warnings: {signals.health_warnings}")
print(f"  Commands: {signals.commands_mentioned}")
print(f"  Configs: {signals.configs_mentioned}")
print(f"  Log snippets: {len(signals.log_snippets)}")
check("Has stacktraces", len(signals.stacktraces) >= 2)
check("Has assertions", len(signals.assertions) >= 1)
check("Has health warnings", len(signals.health_warnings) >= 2)
check("Has commands", len(signals.commands_mentioned) >= 1)
check("Has configs", len(signals.configs_mentioned) >= 1)
check("Has log snippets", len(signals.log_snippets) >= 1)

# ---------------------------------------------------------------------------
print("\n" + "=" * 80)
print(f"WORKFLOW A SUMMARY: {PASS} passed, {FAIL} failed")
print("=" * 80)
sys.exit(1 if FAIL else 0)
