"""Tests for the background auto-updater."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ceph_issue_kb.server.auto_update import (
    _do_update,
    _find_repo_root,
    _git_pull,
    _has_remote,
    start_auto_update,
)


# -- _find_repo_root ---------------------------------------------------------


class TestFindRepoRoot:
    def test_finds_git_dir(self, tmp_path):
        (tmp_path / ".git").mkdir()
        sub = tmp_path / "a" / "b"
        sub.mkdir(parents=True)
        assert _find_repo_root(sub) == tmp_path

    def test_returns_none_without_git(self, tmp_path):
        sub = tmp_path / "no_git"
        sub.mkdir()
        assert _find_repo_root(sub) is None

    def test_immediate_git_dir(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert _find_repo_root(tmp_path) == tmp_path


# -- _has_remote --------------------------------------------------------------


class TestHasRemote:
    def test_no_remote(self, tmp_path):
        (tmp_path / ".git").mkdir()
        with patch("ceph_issue_kb.server.auto_update.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["git", "remote"], returncode=0, stdout="", stderr="",
            )
            assert _has_remote(tmp_path) is False

    def test_has_origin(self, tmp_path):
        with patch("ceph_issue_kb.server.auto_update.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=["git", "remote"], returncode=0, stdout="origin\n", stderr="",
            )
            assert _has_remote(tmp_path) is True

    def test_subprocess_error(self, tmp_path):
        with patch("ceph_issue_kb.server.auto_update.subprocess.run", side_effect=OSError("no git")):
            assert _has_remote(tmp_path) is False


# -- _git_pull ----------------------------------------------------------------


class TestGitPull:
    def test_already_up_to_date(self, tmp_path):
        with patch("ceph_issue_kb.server.auto_update.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout="Already up to date.\n", stderr="",
            )
            changed, msg = _git_pull(tmp_path)
            assert changed is False
            assert "Already up to date" in msg

    def test_changes_pulled(self, tmp_path):
        with patch("ceph_issue_kb.server.auto_update.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=0,
                stdout="Updating abc1234..def5678\n 2 files changed\n", stderr="",
            )
            changed, msg = _git_pull(tmp_path)
            assert changed is True

    def test_pull_failure(self, tmp_path):
        with patch("ceph_issue_kb.server.auto_update.subprocess.run") as mock_run:
            mock_run.return_value = subprocess.CompletedProcess(
                args=[], returncode=1,
                stdout="", stderr="fatal: not a git repository",
            )
            changed, msg = _git_pull(tmp_path)
            assert changed is False
            assert "failed" in msg.lower()

    def test_timeout(self, tmp_path):
        with patch(
            "ceph_issue_kb.server.auto_update.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="git", timeout=120),
        ):
            changed, msg = _git_pull(tmp_path)
            assert changed is False
            assert "timed out" in msg.lower()

    def test_generic_exception(self, tmp_path):
        with patch(
            "ceph_issue_kb.server.auto_update.subprocess.run",
            side_effect=OSError("no such file"),
        ):
            changed, msg = _git_pull(tmp_path)
            assert changed is False
            assert "error" in msg.lower()


# -- _do_update ---------------------------------------------------------------


class TestDoUpdate:
    def _mock_kb(self, issue_count: int = 100) -> MagicMock:
        kb = MagicMock()
        kb._search.issues = {str(i): None for i in range(issue_count)}
        return kb

    def test_no_change(self, tmp_path):
        kb = self._mock_kb()
        with patch("ceph_issue_kb.server.auto_update._git_pull", return_value=(False, "Already up to date")):
            _do_update(kb, tmp_path, tmp_path)
        kb.reload.assert_not_called()

    def test_pull_failure_warns(self, tmp_path):
        kb = self._mock_kb()
        with patch("ceph_issue_kb.server.auto_update._git_pull", return_value=(False, "git pull failed: network")):
            _do_update(kb, tmp_path, tmp_path)
        kb.reload.assert_not_called()

    def test_changes_trigger_reload(self, tmp_path):
        kb = self._mock_kb(100)
        with patch("ceph_issue_kb.server.auto_update._git_pull", return_value=(True, "2 files changed")):
            _do_update(kb, tmp_path, tmp_path)
        kb.reload.assert_called_once_with(tmp_path)

    def test_reload_exception_is_caught(self, tmp_path):
        kb = self._mock_kb()
        kb.reload.side_effect = RuntimeError("bad data")
        with patch("ceph_issue_kb.server.auto_update._git_pull", return_value=(True, "updated")):
            _do_update(kb, tmp_path, tmp_path)


# -- start_auto_update -------------------------------------------------------


class TestStartAutoUpdate:
    def test_skips_when_no_kb_path(self):
        kb = MagicMock()
        with patch("ceph_issue_kb.server.auto_update.threading.Thread") as mock_thread:
            start_auto_update(kb, None)
            mock_thread.assert_not_called()

    def test_skips_when_not_git_repo(self, tmp_path):
        kb = MagicMock()
        with patch("ceph_issue_kb.server.auto_update.threading.Thread") as mock_thread:
            start_auto_update(kb, tmp_path)
            mock_thread.assert_not_called()

    def test_skips_when_no_remote(self, tmp_path):
        (tmp_path / ".git").mkdir()
        kb = MagicMock()
        with patch("ceph_issue_kb.server.auto_update._has_remote", return_value=False):
            with patch("ceph_issue_kb.server.auto_update.threading.Thread") as mock_thread:
                start_auto_update(kb, tmp_path)
                mock_thread.assert_not_called()

    def test_starts_thread_when_valid(self, tmp_path):
        (tmp_path / ".git").mkdir()
        kb = MagicMock()
        with patch("ceph_issue_kb.server.auto_update._has_remote", return_value=True):
            with patch("ceph_issue_kb.server.auto_update.threading.Thread") as mock_thread:
                start_auto_update(kb, tmp_path, update_interval_hours=0)
                mock_thread.assert_called_once()
                mock_thread.return_value.start.assert_called_once()

    def test_starts_periodic_thread(self, tmp_path):
        (tmp_path / ".git").mkdir()
        kb = MagicMock()
        with patch("ceph_issue_kb.server.auto_update._has_remote", return_value=True):
            with patch("ceph_issue_kb.server.auto_update.threading.Thread") as mock_thread:
                start_auto_update(kb, tmp_path, update_interval_hours=24)
                assert mock_thread.call_count == 2
                names = [c.kwargs["name"] for c in mock_thread.call_args_list]
                assert "kb-auto-update" in names
                assert "kb-periodic-update" in names


# -- CLI flag integration (smoke tests) --------------------------------------


class TestMCPServerAutoUpdateFlag:
    def test_auto_update_default_enabled(self):
        from ceph_issue_kb.server.mcp_server import main
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--kb-path", default=None)
        parser.add_argument("--transport", default="stdio", choices=["stdio", "sse"])
        parser.add_argument("--port", type=int, default=8080)
        parser.add_argument("--auto-update", action=argparse.BooleanOptionalAction, default=True)

        args = parser.parse_args([])
        assert args.auto_update is True

    def test_auto_update_disabled(self):
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--auto-update", action=argparse.BooleanOptionalAction, default=True)

        args = parser.parse_args(["--no-auto-update"])
        assert args.auto_update is False


class TestRESTAPIAutoUpdateFlag:
    def test_auto_update_disabled(self):
        import argparse

        parser = argparse.ArgumentParser()
        parser.add_argument("--auto-update", action=argparse.BooleanOptionalAction, default=True)

        args = parser.parse_args(["--no-auto-update"])
        assert args.auto_update is False
