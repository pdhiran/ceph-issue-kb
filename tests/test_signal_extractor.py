"""Tests for the signal extractor."""

from ceph_issue_kb.signal_extractor import extract_signals


class TestExtractStacktraces:
    def test_python_traceback(self):
        text = (
            'Traceback (most recent call last):\n'
            '  File "/usr/lib/python3/ceph_volume/main.py", line 42\n'
            '    raise RuntimeError("test")\n'
            'RuntimeError: test'
        )
        signals = extract_signals(text)
        assert len(signals.stacktraces) > 0

    def test_cpp_stack_frame(self):
        text = (
            "#0  0x00007f1234 in ceph_abort()\n"
            "#1  0x00007f1235 in BlueStore::_do_read() at src/os/bluestore/BlueStore.cc:1234"
        )
        signals = extract_signals(text)
        assert len(signals.stacktraces) == 2

    def test_core_dump(self):
        text = "osd.5 (core dumped)"
        signals = extract_signals(text)
        assert len(signals.stacktraces) == 1

    def test_segfault(self):
        text = "Segmentation fault (core dumped)"
        signals = extract_signals(text)
        assert len(signals.stacktraces) >= 1


class TestExtractAssertions:
    def test_ceph_assert(self):
        text = "ceph_assert_fail: expected offset < length"
        signals = extract_signals(text)
        assert any("assert" in a.lower() for a in signals.assertions)

    def test_failed(self):
        text = "FAILED assertion: p->second.size() > 0"
        signals = extract_signals(text)
        assert len(signals.assertions) >= 1


class TestExtractHealthWarnings:
    def test_multiple_warnings(self):
        text = "cluster health:\nHEALTH_WARN\nPG_DEGRADED\nSLOW_OPS\nOSD_DOWN"
        signals = extract_signals(text)
        assert "HEALTH_WARN" in signals.health_warnings
        assert "PG_DEGRADED" in signals.health_warnings
        assert "SLOW_OPS" in signals.health_warnings
        assert "OSD_DOWN" in signals.health_warnings

    def test_no_warnings(self):
        text = "Cluster is running fine."
        signals = extract_signals(text)
        assert signals.health_warnings == []


class TestExtractCommands:
    def test_ceph_command(self):
        text = "Run: ceph osd deep-scrub osd.5"
        signals = extract_signals(text)
        assert any("ceph osd deep-scrub" in c for c in signals.commands_mentioned)

    def test_rados_command(self):
        text = "rados -p testpool put testobj /tmp/file"
        signals = extract_signals(text)
        assert any("rados" in c for c in signals.commands_mentioned)

    def test_sudo_prefix(self):
        text = "sudo cephadm shell --mount /var/log"
        signals = extract_signals(text)
        assert any("cephadm" in c for c in signals.commands_mentioned)


class TestExtractConfigs:
    def test_osd_config(self):
        text = "osd_deep_scrub_interval = 604800"
        signals = extract_signals(text)
        assert "osd_deep_scrub_interval" in signals.configs_mentioned

    def test_bluestore_config(self):
        text = "bluestore_cache_size_ssd = 3221225472"
        signals = extract_signals(text)
        assert "bluestore_cache_size_ssd" in signals.configs_mentioned

    def test_no_false_positives_for_short_names(self):
        text = "this is a normal sentence with some words"
        signals = extract_signals(text)
        assert signals.configs_mentioned == []


class TestExtractLogSnippets:
    def test_iso_timestamp(self):
        text = "2024-10-15T10:30:00.123456 7f1234 -1 osd.5 shutting down"
        signals = extract_signals(text)
        assert len(signals.log_snippets) == 1

    def test_syslog_timestamp(self):
        text = "Oct 15 10:30:00 node1 ceph-osd[1234]: starting osd.5"
        signals = extract_signals(text)
        assert len(signals.log_snippets) == 1


class TestExtractSignalsEmpty:
    def test_empty_string(self):
        signals = extract_signals("")
        assert signals.stacktraces == []

    def test_none_safe(self):
        signals = extract_signals("")
        assert signals.assertions == []
