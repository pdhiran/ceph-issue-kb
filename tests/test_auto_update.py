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
    _indexing_in_progress,
    _install_extracted,
    _periodic_loop,
    _safe_extract,
    _sync_knowledge_release,
    _trigger_loop,
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
        kb._state.search.issues = {str(i): MagicMock() for i in range(issue_count)}
        return kb

    def test_no_change(self, tmp_path):
        kb = self._mock_kb()
        with (
            patch("ceph_issue_kb.server.auto_update._has_remote", return_value=True),
            patch(
                "ceph_issue_kb.server.auto_update._git_pull",
                return_value=(False, "Already up to date"),
            ),
            patch(
                "ceph_issue_kb.server.auto_update._sync_knowledge_release",
                return_value=(False, "Knowledge index is up to date"),
            ),
        ):
            _do_update(kb, tmp_path, tmp_path)
        kb.reload.assert_not_called()

    def test_knowledge_sync_triggers_reload(self, tmp_path):
        kb = self._mock_kb()
        kb_dir = tmp_path / "knowledge" / "issues-2024-2025"
        kb_dir.mkdir(parents=True)
        (kb_dir / "ibm-jira").mkdir()
        (kb_dir / "ibm-jira" / "issues.json").write_text("[]")
        with (
            patch("ceph_issue_kb.server.auto_update._has_remote", return_value=True),
            patch(
                "ceph_issue_kb.server.auto_update._git_pull",
                return_value=(False, "Already up to date"),
            ),
            patch(
                "ceph_issue_kb.server.auto_update._sync_knowledge_release",
                return_value=(True, "Knowledge index downloaded"),
            ),
        ):
            _do_update(kb, tmp_path, tmp_path)
        kb.reload.assert_called_once()

    def test_code_change_restarts(self, tmp_path):
        kb = self._mock_kb()
        with (
            patch("ceph_issue_kb.server.auto_update._has_remote", return_value=True),
            patch("ceph_issue_kb.server.auto_update._git_pull", return_value=(True, "Updated")),
            patch("ceph_issue_kb.server.auto_update._get_head_sha", side_effect=["aaa", "bbb"]),
            patch(
                "ceph_issue_kb.server.auto_update._changed_files",
                return_value=["src/ceph_issue_kb/server/auto_update.py"],
            ),
            patch("ceph_issue_kb.server.auto_update.os._exit") as mock_exit,
            patch(
                "ceph_issue_kb.server.auto_update._sync_knowledge_release",
                return_value=(False, "Knowledge index is up to date"),
            ),
        ):
            _do_update(kb, tmp_path, tmp_path)
        mock_exit.assert_called_once_with(0)
        kb.reload.assert_not_called()

    def test_exception_does_not_propagate(self, tmp_path):
        kb = self._mock_kb()
        with patch(
            "ceph_issue_kb.server.auto_update._has_remote",
            side_effect=RuntimeError("boom"),
        ):
            _do_update(kb, tmp_path, tmp_path)


# -- _sync_knowledge_release ------------------------------------------------


def _write_kb_tar(tar_path: Path) -> None:
    """Tiny tarball matching the published knowledge/ layout."""
    import tarfile

    src = tar_path.parent / "src"
    inner = src / "issues-2024-2025" / "ibm-jira"
    inner.mkdir(parents=True)
    (inner / "issues.json").write_text("[]")
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(src / "issues-2024-2025", arcname="issues-2024-2025")


class TestSyncKnowledgeRelease:
    def test_404_is_not_an_update(self, tmp_path):
        with patch("ceph_issue_kb.server.auto_update._head_asset", return_value=(404, "")):
            changed, msg = _sync_knowledge_release(tmp_path)
        assert changed is False
        assert "not found" in msg

    def test_matching_etag_skips_download(self, tmp_path):
        kb_dir = tmp_path / "knowledge" / "issues-2024-2025" / "ibm-jira"
        kb_dir.mkdir(parents=True)
        (kb_dir / "issues.json").write_text("[]")
        (tmp_path / "knowledge" / ".release_etag").write_text('"abc"\n')
        with patch("ceph_issue_kb.server.auto_update._head_asset", return_value=(200, '"abc"')):
            changed, msg = _sync_knowledge_release(tmp_path)
        assert changed is False
        assert "up to date" in msg.lower()

    def test_skips_download_when_indexing_lock_present(self, tmp_path):
        (tmp_path / "knowledge").mkdir()
        (tmp_path / "knowledge" / ".indexing_lock").write_text("1\n")
        with patch("ceph_issue_kb.server.auto_update._head_asset") as head:
            changed, msg = _sync_knowledge_release(tmp_path)
        assert changed is False
        assert "Indexing in progress" in msg
        head.assert_not_called()

    def test_stale_indexing_lock_is_ignored(self, tmp_path):
        import os

        lock = tmp_path / "knowledge" / ".indexing_lock"
        lock.parent.mkdir()
        lock.write_text("1\n")
        old = time.time() - 7 * 3600
        os.utime(lock, (old, old))
        assert _indexing_in_progress(tmp_path) is False
        with patch("ceph_issue_kb.server.auto_update._head_asset", return_value=(404, "")):
            changed, msg = _sync_knowledge_release(tmp_path)
        assert "not found" in msg

    def test_install_extracted_refuses_when_locked(self, tmp_path):
        knowledge = tmp_path / "knowledge"
        live = knowledge / "issues-2024-2025" / "ibm-jira"
        live.mkdir(parents=True)
        (live / "issues.json").write_text('["keep-me"]')
        (knowledge / ".indexing_lock").write_text("1\n")
        staging = tmp_path / "staging"
        (staging / "issues-2024-2025").mkdir(parents=True)
        with pytest.raises(RuntimeError, match="indexing lock"):
            _install_extracted(staging, knowledge)
        assert (live / "issues.json").read_text() == '["keep-me"]'

    def test_aborts_install_if_lock_appears_during_download(self, tmp_path):
        tar_path = tmp_path / "payload.tar.gz"
        _write_kb_tar(tar_path)
        payload = tar_path.read_bytes()

        live = tmp_path / "knowledge" / "issues-2024-2025" / "ibm-jira"
        live.mkdir(parents=True)
        (live / "issues.json").write_text('["keep-me"]')

        real_extract = _safe_extract

        def extract_then_lock(tar, dest):
            (tmp_path / "knowledge" / ".indexing_lock").write_text("1\n")
            real_extract(tar, dest)

        class _Resp:
            status_code = 200
            headers = {"ETag": '"new"'}

            def iter_content(self, chunk_size=1):
                yield payload

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with (
            patch("ceph_issue_kb.server.auto_update._head_asset", return_value=(200, '"new"')),
            patch("ceph_issue_kb.server.auto_update.requests.get", return_value=_Resp()),
            patch("ceph_issue_kb.server.auto_update._safe_extract", side_effect=extract_then_lock),
        ):
            changed, msg = _sync_knowledge_release(tmp_path)
        assert changed is False
        assert "aborting install" in msg.lower()
        assert (live / "issues.json").read_text() == '["keep-me"]'

    def test_downloads_when_etag_differs(self, tmp_path):
        tar_path = tmp_path / "payload.tar.gz"
        _write_kb_tar(tar_path)
        payload = tar_path.read_bytes()

        class _Resp:
            status_code = 200
            headers = {"ETag": '"new"'}

            def iter_content(self, chunk_size=1):
                yield payload

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        with (
            patch("ceph_issue_kb.server.auto_update._head_asset", return_value=(200, '"new"')),
            patch("ceph_issue_kb.server.auto_update.requests.get", return_value=_Resp()),
        ):
            changed, msg = _sync_knowledge_release(tmp_path)
        assert changed is True
        assert (tmp_path / "knowledge" / "issues-2024-2025" / "ibm-jira" / "issues.json").exists()
        assert (tmp_path / "knowledge" / ".release_etag").read_text().strip() == '"new"'

    def test_rejects_path_escape(self, tmp_path):
        import io
        import tarfile

        dest = tmp_path / "dest"
        dest.mkdir()
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            info = tarfile.TarInfo(name="../evil")
            info.size = 0
            tar.addfile(info)
        buf.seek(0)
        with tarfile.open(fileobj=buf, mode="r") as tar:
            with pytest.raises(ValueError, match="unsafe path"):
                _safe_extract(tar, dest)


# -- _periodic_loop ---------------------------------------------------------


class TestPeriodicLoop:
    def test_fires_update_then_stops(self, tmp_path):
        kb = MagicMock()
        kb._state.search.issues = {}
        stop = threading.Event()
        call_count = 0

        def counting_pull(*_a, **_kw):
            nonlocal call_count
            call_count += 1
            stop.set()
            return False, "Already up to date"

        with (
            patch("ceph_issue_kb.server.auto_update._has_remote", return_value=True),
            patch("ceph_issue_kb.server.auto_update._git_pull", side_effect=counting_pull),
            patch(
                "ceph_issue_kb.server.auto_update._sync_knowledge_release",
                return_value=(False, "Knowledge index is up to date"),
            ),
        ):
            _periodic_loop(kb, tmp_path, tmp_path, 0.01, stop)

        assert call_count >= 1

    def test_immediate_stop(self, tmp_path):
        """Stop event already set — loop body never runs."""
        kb = MagicMock()
        kb._state.search.issues = {}
        stop = threading.Event()
        stop.set()

        with patch("ceph_issue_kb.server.auto_update._git_pull") as mock_pull:
            _periodic_loop(kb, tmp_path, tmp_path, 0.01, stop)

        mock_pull.assert_not_called()


# -- start_auto_update / stop_auto_update -----------------------------------


@pytest.fixture(autouse=True)
def _cleanup_auto_update():
    yield
    stop_auto_update()


class TestStartAutoUpdate:
    def test_skips_when_no_kb_path(self):
        kb = MagicMock()
        start_auto_update(kb, None)

    def test_skips_when_not_git_repo(self, tmp_path):
        kb = MagicMock()
        start_auto_update(kb, tmp_path)

    def test_no_remote_still_starts_trigger(self, tmp_path):
        (tmp_path / ".git").mkdir()
        kb = MagicMock()
        with patch("ceph_issue_kb.server.auto_update._has_remote", return_value=False):
            start_auto_update(kb, tmp_path, update_interval_hours=0)
        time.sleep(0.05)
        names = [t.name for t in threading.enumerate()]
        assert "kb-reload-trigger" in names

    def test_starts_startup_thread(self, tmp_path):
        (tmp_path / ".git").mkdir()
        kb = MagicMock()
        kb._state.search.issues = {}
        with (
            patch("ceph_issue_kb.server.auto_update._has_remote", return_value=True),
            patch(
                "ceph_issue_kb.server.auto_update._git_pull",
                return_value=(False, "Already up to date"),
            ),
            patch(
                "ceph_issue_kb.server.auto_update._sync_knowledge_release",
                return_value=(False, "Knowledge index is up to date"),
            ),
        ):
            start_auto_update(kb, tmp_path, update_interval_hours=0)
            time.sleep(0.1)

    def test_starts_periodic_thread(self, tmp_path):
        (tmp_path / ".git").mkdir()
        kb = MagicMock()
        kb._state.search.issues = {}
        with (
            patch("ceph_issue_kb.server.auto_update._has_remote", return_value=True),
            patch(
                "ceph_issue_kb.server.auto_update._git_pull",
                return_value=(False, "Already up to date"),
            ),
            patch(
                "ceph_issue_kb.server.auto_update._sync_knowledge_release",
                return_value=(False, "Knowledge index is up to date"),
            ),
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


class TestTriggerLoop:
    def test_touch_trigger_reloads_without_git(self, tmp_path):
        kb = MagicMock()
        kb._state.search.issues = {}
        stop = threading.Event()
        with patch("ceph_issue_kb.server.auto_update.TRIGGER_POLL_SECONDS", 0.05):
            t = threading.Thread(
                target=_trigger_loop,
                args=(kb, tmp_path, tmp_path, stop),
                daemon=True,
            )
            t.start()
            time.sleep(0.08)
            (tmp_path / ".reload_trigger").write_text("1")
            deadline = time.time() + 2.0
            while time.time() < deadline and not kb.reload.called:
                time.sleep(0.05)
            stop.set()
            t.join(timeout=1)
        kb.reload.assert_called()


class TestUpdateIndexScript:
    def test_script_touches_reload_trigger(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "update_index.sh").read_text()
        assert "touch .reload_trigger" in text

    def test_reset_clears_tracker(self, tmp_path):
        root = Path(__file__).resolve().parents[1]
        script = tmp_path / "update_index.sh"
        script.write_text((root / "update_index.sh").read_text())
        script.chmod(0o755)
        (tmp_path / ".last_index_update").write_text("2026-01-01\n")
        subprocess.run([str(script), "--reset"], cwd=tmp_path, check=True)
        assert not (tmp_path / ".last_index_update").exists()

    def test_invalid_since_is_rejected(self):
        from index_issues import _parse_args

        with pytest.raises(SystemExit):
            _parse_args(["--since", "not-a-date"])


# -- CLI --update-interval integration --------------------------------------


class TestCLIUpdateInterval:
    def test_mcp_server_accepts_update_interval(self):
        from ceph_issue_kb.server.mcp_server import main
        import argparse

        with patch("ceph_issue_kb.server.mcp_server.KnowledgeBase") as mock_kb_cls:
            mock_kb_cls.empty.return_value = MagicMock()
            mock_kb_cls.empty.return_value._state.search.issues = {}
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
            mock_kb_cls.empty.return_value._state.search.issues = {}
            from ceph_issue_kb.server.rest_api import main as rest_main

            parser_args = ["--update-interval", "6", "--no-auto-update"]
            try:
                with patch("uvicorn.run"):
                    rest_main(parser_args)
            except SystemExit:
                pass
