"""Tests for the background auto-update module."""

from __future__ import annotations

import subprocess
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ceph_issue_kb.server.auto_update import (
    _do_update,
    _find_repo_root,
    _git_pull,
    _has_remote,
    _periodic_loop,
    start_auto_update,
    stop_auto_update,
)


# -- _find_repo_root -------------------------------------------------------


class TestFindRepoRoot:
    def test_finds_git_dir(self, tmp_path):
        (tmp_path / ".git").mkdir()
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert _find_repo_root(nested) == tmp_path

    def test_returns_none_without_git(self, tmp_path):
        assert _find_repo_root(tmp_path) is None

    def test_direct_git_dir(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert _find_repo_root(tmp_path) == tmp_path


# -- _has_remote ------------------------------------------------------------


class TestHasRemote:
    def test_returns_true_when_remote_exists(self, tmp_path):
        (tmp_path / ".git").mkdir()
        with patch("ceph_issue_kb.server.auto_update.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="origin\n", returncode=0)
            assert _has_remote(tmp_path) is True

    def test_returns_false_when_no_remote(self, tmp_path):
        with patch("ceph_issue_kb.server.auto_update.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(stdout="", returncode=0)
            assert _has_remote(tmp_path) is False

    def test_returns_false_on_exception(self, tmp_path):
        with patch("ceph_issue_kb.server.auto_update.subprocess.run", side_effect=OSError):
            assert _has_remote(tmp_path) is False


# -- _git_pull --------------------------------------------------------------


class TestGitPull:
    def test_already_up_to_date(self, tmp_path):
        with patch("ceph_issue_kb.server.auto_update.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="Already up to date.", returncode=0,
            )
            changed, msg = _git_pull(tmp_path)
            assert changed is False
            assert "Already up to date" in msg

    def test_new_changes(self, tmp_path):
        with patch("ceph_issue_kb.server.auto_update.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="Updating abc..def\n3 files changed", returncode=0,
            )
            changed, msg = _git_pull(tmp_path)
            assert changed is True

    def test_pull_failure(self, tmp_path):
        with patch("ceph_issue_kb.server.auto_update.subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout="", stderr="fatal: not a git repo", returncode=128,
            )
            changed, msg = _git_pull(tmp_path)
            assert changed is False
            assert "failed" in msg

    def test_timeout(self, tmp_path):
        with patch(
            "ceph_issue_kb.server.auto_update.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="git pull", timeout=120),
        ):
            changed, msg = _git_pull(tmp_path)
            assert changed is False
            assert "timed out" in msg

    def test_generic_exception(self, tmp_path):
        with patch(
            "ceph_issue_kb.server.auto_update.subprocess.run",
            side_effect=OSError("disk error"),
        ):
            changed, msg = _git_pull(tmp_path)
            assert changed is False
            assert "error" in msg.lower()


# -- _do_update -------------------------------------------------------------


class TestDoUpdate:
    def _mock_kb(self, issue_count=5):
        kb = MagicMock()
        kb._search.issues = {str(i): MagicMock() for i in range(issue_count)}
        return kb

    def test_no_change(self, tmp_path):
        kb = self._mock_kb()
        with patch(
            "ceph_issue_kb.server.auto_update._git_pull",
            return_value=(False, "Already up to date"),
        ):
            _do_update(kb, tmp_path, tmp_path)
        kb.reload.assert_not_called()

    def test_pull_changed_triggers_reload(self, tmp_path):
        kb = self._mock_kb()
        with patch(
            "ceph_issue_kb.server.auto_update._git_pull",
            return_value=(True, "Updated"),
        ):
            _do_update(kb, tmp_path, tmp_path)
        kb.reload.assert_called_once_with(tmp_path)

    def test_exception_does_not_propagate(self, tmp_path):
        kb = self._mock_kb()
        with patch(
            "ceph_issue_kb.server.auto_update._git_pull",
            side_effect=RuntimeError("boom"),
        ):
            _do_update(kb, tmp_path, tmp_path)


# -- _periodic_loop ---------------------------------------------------------


class TestPeriodicLoop:
    def test_fires_update_then_stops(self, tmp_path):
        kb = MagicMock()
        kb._search.issues = {}
        stop = threading.Event()
        call_count = 0

        def counting_pull(*_a, **_kw):
            nonlocal call_count
            call_count += 1
            stop.set()
            return False, "Already up to date"

        with patch("ceph_issue_kb.server.auto_update._git_pull", side_effect=counting_pull):
            _periodic_loop(kb, tmp_path, tmp_path, 0.01, stop)

        assert call_count >= 1

    def test_immediate_stop(self, tmp_path):
        """Stop event already set — loop body never runs."""
        kb = MagicMock()
        kb._search.issues = {}
        stop = threading.Event()
        stop.set()

        with patch("ceph_issue_kb.server.auto_update._git_pull") as mock_pull:
            _periodic_loop(kb, tmp_path, tmp_path, 0.01, stop)

        mock_pull.assert_not_called()


# -- start_auto_update / stop_auto_update -----------------------------------


class TestStartAutoUpdate:
    def test_skips_when_no_kb_path(self):
        kb = MagicMock()
        start_auto_update(kb, None)

    def test_skips_when_not_git_repo(self, tmp_path):
        kb = MagicMock()
        start_auto_update(kb, tmp_path)

    def test_skips_when_no_remote(self, tmp_path):
        (tmp_path / ".git").mkdir()
        kb = MagicMock()
        with patch("ceph_issue_kb.server.auto_update._has_remote", return_value=False):
            start_auto_update(kb, tmp_path)

    def test_starts_startup_thread(self, tmp_path):
        (tmp_path / ".git").mkdir()
        kb = MagicMock()
        kb._search.issues = {}
        with (
            patch("ceph_issue_kb.server.auto_update._has_remote", return_value=True),
            patch("ceph_issue_kb.server.auto_update._git_pull", return_value=(False, "Already up to date")),
        ):
            start_auto_update(kb, tmp_path, update_interval_hours=0)
            time.sleep(0.1)

    def test_starts_periodic_thread(self, tmp_path):
        (tmp_path / ".git").mkdir()
        kb = MagicMock()
        kb._search.issues = {}
        with (
            patch("ceph_issue_kb.server.auto_update._has_remote", return_value=True),
            patch("ceph_issue_kb.server.auto_update._git_pull", return_value=(False, "Already up to date")),
        ):
            start_auto_update(kb, tmp_path, update_interval_hours=0.0001)
            time.sleep(0.2)
            stop_auto_update()
            time.sleep(0.5)

        periodic_names = [
            t.name for t in threading.enumerate() if t.name == "kb-periodic-update"
        ]
        assert len(periodic_names) == 0

    def test_stop_is_idempotent(self):
        stop_auto_update()
        stop_auto_update()


# -- CLI --update-interval integration --------------------------------------


class TestCLIUpdateInterval:
    def test_mcp_server_accepts_update_interval(self):
        from ceph_issue_kb.server.mcp_server import main
        import argparse

        with patch("ceph_issue_kb.server.mcp_server.KnowledgeBase") as mock_kb_cls:
            mock_kb_cls.empty.return_value = MagicMock()
            mock_kb_cls.empty.return_value._search.issues = {}
            with (
                patch("ceph_issue_kb.server.mcp_server._find_kb_path", return_value=None),
                patch("ceph_issue_kb.server.auto_update.start_auto_update") as mock_start,
                patch("ceph_issue_kb.server.mcp_server.create_mcp_server") as mock_create,
            ):
                mock_mcp = MagicMock()
                mock_create.return_value = mock_mcp
                try:
                    main(["--no-auto-update", "--update-interval", "12"])
                except SystemExit:
                    pass

    def test_rest_api_accepts_update_interval(self):
        import argparse

        with (
            patch("ceph_issue_kb.server.rest_api.KnowledgeBase") as mock_kb_cls,
            patch("ceph_issue_kb.server.mcp_server._find_kb_path", return_value=None),
        ):
            mock_kb_cls.empty.return_value = MagicMock()
            mock_kb_cls.empty.return_value._search.issues = {}
            from ceph_issue_kb.server.rest_api import main as rest_main

            parser_args = ["--update-interval", "6", "--no-auto-update"]
            try:
                with patch("uvicorn.run"):
                    rest_main(parser_args)
            except SystemExit:
                pass
