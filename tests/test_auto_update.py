"""Tests for the background auto-update module."""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from ceph_issue_kb.server.auto_update import (
    _UA,
    DEFAULT_UPDATE_INTERVAL_HOURS,
    _asset_url,
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

    def test_git_pull_then_release_tarball_reloads(self, tmp_path):
        """Non-.py git pull still downloads the Release tarball and reloads."""
        kb = self._mock_kb()
        order: list[str] = []

        def pull(*_a, **_k):
            order.append("pull")
            return True, "Updating abc..def\n1 file changed"

        def sync(*_a, **_k):
            order.append("sync")
            return True, "Knowledge index downloaded"

        with (
            patch("ceph_issue_kb.server.auto_update._has_remote", return_value=True),
            patch("ceph_issue_kb.server.auto_update._git_pull", side_effect=pull),
            patch(
                "ceph_issue_kb.server.auto_update._get_head_sha",
                side_effect=["aaa", "bbb"],
            ),
            patch(
                "ceph_issue_kb.server.auto_update._changed_files",
                return_value=["README.md"],
            ),
            patch("ceph_issue_kb.server.auto_update.os._exit") as mock_exit,
            patch(
                "ceph_issue_kb.server.auto_update._sync_knowledge_release",
                side_effect=sync,
            ),
        ):
            _do_update(kb, tmp_path, tmp_path)
        assert order == ["pull", "sync"]
        mock_exit.assert_not_called()
        kb.reload.assert_called_once()

    def test_no_remote_still_syncs_release_tarball(self, tmp_path):
        kb = self._mock_kb()
        with (
            patch("ceph_issue_kb.server.auto_update._has_remote", return_value=False),
            patch("ceph_issue_kb.server.auto_update._git_pull") as mock_pull,
            patch(
                "ceph_issue_kb.server.auto_update._sync_knowledge_release",
                return_value=(True, "Knowledge index downloaded"),
            ),
        ):
            _do_update(kb, tmp_path, tmp_path)
        mock_pull.assert_not_called()
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

    def test_skips_reload_when_lock_present_after_sync(self, tmp_path):
        kb = self._mock_kb()
        (tmp_path / "knowledge").mkdir()
        (tmp_path / "knowledge" / ".indexing_lock").write_text("1\n")
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

    def test_skips_download_when_local_kb_has_no_stamp(self, tmp_path):
        live = tmp_path / "knowledge" / "issues-2024-2025" / "ibm-jira"
        live.mkdir(parents=True)
        (live / "issues.json").write_text('["local"]')
        with patch("ceph_issue_kb.server.auto_update._head_asset") as head:
            changed, msg = _sync_knowledge_release(tmp_path)
        assert changed is False
        assert "no release stamp" in msg.lower()
        head.assert_not_called()
        assert (live / "issues.json").read_text() == '["local"]'

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
        (tmp_path / "knowledge" / ".release_etag").write_text('"old"\n')

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


_IDLE_SYNC = patch(
    "ceph_issue_kb.server.auto_update._sync_knowledge_release",
    return_value=(False, "Knowledge index is up to date"),
)


class TestStartAutoUpdate:
    def test_skips_when_no_kb_path_and_no_git(self):
        kb = MagicMock()
        with patch("ceph_issue_kb.server.auto_update._find_repo_root", return_value=None):
            start_auto_update(kb, None)

    def test_none_kb_path_still_starts_trigger(self, tmp_path):
        """First Release download failed — still watch so a later update can load."""
        (tmp_path / ".git").mkdir()
        kb = MagicMock()
        kb._state.search.issues = {}
        with (
            patch("ceph_issue_kb.server.auto_update._find_repo_root", return_value=tmp_path),
            patch("ceph_issue_kb.server.auto_update._has_remote", return_value=False),
            _IDLE_SYNC,
        ):
            start_auto_update(kb, None, update_interval_hours=0)
        time.sleep(0.05)
        names = [t.name for t in threading.enumerate()]
        assert "kb-reload-trigger" in names

    def test_skips_when_not_git_repo(self, tmp_path):
        kb = MagicMock()
        with _IDLE_SYNC:
            start_auto_update(kb, tmp_path, update_interval_hours=0)
        time.sleep(0.05)
        names = [t.name for t in threading.enumerate()]
        assert "kb-reload-trigger" in names

    def test_no_remote_still_starts_trigger_and_tarball_refresh(self, tmp_path):
        (tmp_path / ".git").mkdir()
        kb = MagicMock()
        kb._state.search.issues = {}
        with (
            patch("ceph_issue_kb.server.auto_update._has_remote", return_value=False),
            patch("ceph_issue_kb.server.auto_update._git_pull") as mock_pull,
            patch(
                "ceph_issue_kb.server.auto_update._sync_knowledge_release",
                return_value=(True, "Knowledge index downloaded"),
            ) as mock_sync,
        ):
            start_auto_update(kb, tmp_path, update_interval_hours=0)
            deadline = time.time() + 2.0
            while time.time() < deadline and not kb.reload.called:
                time.sleep(0.05)
        mock_pull.assert_not_called()
        mock_sync.assert_called()
        kb.reload.assert_called()
        names = [t.name for t in threading.enumerate()]
        assert "kb-reload-trigger" in names

    def test_duplicate_start_does_not_leak_threads(self, tmp_path):
        (tmp_path / ".git").mkdir()
        kb = MagicMock()
        kb._state.search.issues = {}
        with (
            patch("ceph_issue_kb.server.auto_update._has_remote", return_value=False),
            _IDLE_SYNC,
        ):
            start_auto_update(kb, tmp_path, update_interval_hours=0)
            start_auto_update(kb, tmp_path, update_interval_hours=0)
        time.sleep(0.05)
        triggers = [t for t in threading.enumerate() if t.name == "kb-reload-trigger"]
        assert len(triggers) == 1
        stop_auto_update()
        triggers = [t for t in threading.enumerate() if t.name == "kb-reload-trigger"]
        assert len(triggers) == 0

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
            time.sleep(0.1)

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

    def test_trigger_reload_waits_for_indexing_lock(self, tmp_path):
        kb = MagicMock()
        kb._state.search.issues = {}
        (tmp_path / "knowledge").mkdir()
        (tmp_path / "knowledge" / ".indexing_lock").write_text("1\n")
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
            time.sleep(0.2)
            kb.reload.assert_not_called()
            (tmp_path / "knowledge" / ".indexing_lock").unlink()
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

    def test_publish_only_does_not_write_last_index_update(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "update_index.sh").read_text()
        start = text.index('if [[ "${1:-}" == "--publish-only" ]]')
        end = text.index('# Determine the "since" date')
        block = text[start:end]
        assert "LAST_RUN_FILE" not in block
        assert "touch .reload_trigger" in block
        assert "publish_knowledge" in block
        assert "index_issues.py" not in block
        # Full index+publish path still advances the tracker after publish.
        after = text[end:]
        assert after.count("LAST_RUN_FILE") >= 1
        assert after.index("publish_knowledge") < after.index(
            'date -v-1d +%Y-%m-%d > "$LAST_RUN_FILE"',
        )

    def test_last_index_update_writes_yesterday(self):
        root = Path(__file__).resolve().parents[1]
        text = (root / "update_index.sh").read_text()
        docs = (root / "UPDATING.md").read_text()
        assert 'date -v-1d +%Y-%m-%d > "$LAST_RUN_FILE"' in text
        assert "1-day overlap" in docs
        yesterday = subprocess.check_output(
            ["date", "-v-1d", "+%Y-%m-%d"], text=True,
        ).strip()
        assert yesterday == (date.today() - timedelta(days=1)).isoformat()

    def test_invalid_since_is_rejected(self):
        from index_issues import _parse_args

        with pytest.raises(SystemExit):
            _parse_args(["--since", "not-a-date"])

    def test_invalid_since_does_not_run_indexer(self, monkeypatch, tmp_path):
        monkeypatch.chdir(tmp_path)
        from index_issues import main

        with (
            patch("index_issues.load_config") as mock_load,
            patch("index_issues.build_index") as mock_build,
        ):
            with pytest.raises(SystemExit) as exc:
                main(["--since", "not-a-date"])
        assert exc.value.code == 2
        mock_load.assert_not_called()
        mock_build.assert_not_called()

    def test_invalid_since_cli_exits_2_before_fetch(self, tmp_path):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, str(root / "index_issues.py"), "--since", "not-a-date"],
            cwd=tmp_path,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2
        assert "not-a-date" in result.stderr
        assert "Invalid date" in result.stderr
        assert not (tmp_path / "knowledge").exists()
        assert "Fetching" not in result.stderr
        assert "Fetching" not in result.stdout


# -- CLI --update-interval integration --------------------------------------


class TestCLIUpdateInterval:
    def test_mcp_default_interval_is_12_and_starts_without_kb(self):
        from ceph_issue_kb.server.auto_update import DEFAULT_UPDATE_INTERVAL_HOURS
        from ceph_issue_kb.server.mcp_server import main

        assert DEFAULT_UPDATE_INTERVAL_HOURS == 12
        with (
            patch("ceph_issue_kb.server.mcp_server.KnowledgeBase") as mock_kb_cls,
            patch("ceph_issue_kb.server.mcp_server._find_kb_path", return_value=None),
            patch("ceph_issue_kb.server.auto_update.ensure_knowledge", return_value=None),
            patch("ceph_issue_kb.server.auto_update.start_auto_update") as mock_start,
            patch("ceph_issue_kb.server.mcp_server.create_mcp_server") as mock_create,
        ):
            mock_kb_cls.empty.return_value = MagicMock()
            mock_create.return_value = MagicMock()
            main([])
            mock_start.assert_called_once()
            _kb, path = mock_start.call_args[0]
            assert path is not None
            assert mock_start.call_args.kwargs["update_interval_hours"] == 12

    def test_mcp_no_auto_update_skips_ensure_knowledge_and_start(self):
        from ceph_issue_kb.server.mcp_server import main

        with (
            patch("ceph_issue_kb.server.mcp_server.KnowledgeBase") as mock_kb_cls,
            patch("ceph_issue_kb.server.mcp_server._find_kb_path", return_value=None),
            patch("ceph_issue_kb.server.auto_update.ensure_knowledge") as mock_ensure,
            patch("ceph_issue_kb.server.auto_update.start_auto_update") as mock_start,
            patch("ceph_issue_kb.server.mcp_server.create_mcp_server") as mock_create,
        ):
            mock_kb_cls.empty.return_value = MagicMock()
            mock_create.return_value = MagicMock()
            main(["--no-auto-update", "--update-interval", "12"])
            mock_ensure.assert_not_called()
            mock_start.assert_not_called()

    def test_mcp_help_no_auto_update_skips_ensure_and_trigger(self):
        root = Path(__file__).resolve().parents[1]
        result = subprocess.run(
            [sys.executable, "-m", "ceph_issue_kb.server.mcp_server", "--help"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
        )
        compact = " ".join(result.stdout.split()).lower()
        assert "--no-auto-update" in result.stdout
        assert "first-run download" in compact
        assert "trigger watcher" in compact

    def test_rest_default_interval_is_12_and_starts_without_kb(self):
        from ceph_issue_kb.server.rest_api import main as rest_main

        with (
            patch("ceph_issue_kb.server.rest_api.KnowledgeBase") as mock_kb_cls,
            patch("ceph_issue_kb.server.mcp_server._find_kb_path", return_value=None),
            patch("ceph_issue_kb.server.auto_update.ensure_knowledge", return_value=None),
            patch("ceph_issue_kb.server.auto_update.start_auto_update") as mock_start,
            patch("uvicorn.run"),
        ):
            mock_kb_cls.empty.return_value = MagicMock()
            rest_main([])
            mock_start.assert_called_once()
            assert mock_start.call_args.kwargs["update_interval_hours"] == 12

    def test_rest_no_auto_update_skips_ensure_knowledge_and_start(self):
        from ceph_issue_kb.server.rest_api import main as rest_main

        with (
            patch("ceph_issue_kb.server.rest_api.KnowledgeBase") as mock_kb_cls,
            patch("ceph_issue_kb.server.mcp_server._find_kb_path", return_value=None),
            patch("ceph_issue_kb.server.auto_update.ensure_knowledge") as mock_ensure,
            patch("ceph_issue_kb.server.auto_update.start_auto_update") as mock_start,
            patch("uvicorn.run"),
        ):
            mock_kb_cls.empty.return_value = MagicMock()
            rest_main(["--update-interval", "6", "--no-auto-update"])
            mock_ensure.assert_not_called()
            mock_start.assert_not_called()


class TestConsumerReleaseDownload:
    def test_user_agent_has_no_authorization(self):
        assert list(_UA.keys()) == ["User-Agent"]
        assert "Authorization" not in _UA

    def test_asset_url_is_public_github_release(self):
        url = _asset_url()
        assert url.startswith("https://github.com/")
        assert "/releases/download/knowledge/knowledge.tar.gz" in url

    def test_interval_constant_matches_cli_default(self):
        assert DEFAULT_UPDATE_INTERVAL_HOURS == 12


class TestRestDocstringPort:
    def test_rest_module_docstring_uses_8200_not_9000(self):
        import ceph_issue_kb.server.rest_api as rest

        doc = rest.__doc__ or ""
        assert "--port 8200" in doc
        assert "127.0.0.1:8200" in doc
        assert "9000" not in doc


class TestFastMCPInstructions:
    def test_does_not_treat_tracker_bugzilla_as_enabled(self):
        from ceph_issue_kb.server.mcp_server import create_mcp_server

        text = create_mcp_server(MagicMock()).instructions
        assert "IBM JIRA" in text
        assert "Red Hat KB" in text
        assert "connectors are present but disabled" in text
        assert "JIRA, Ceph Tracker, Red Hat Bugzilla, and Red Hat KB" not in text
        enabled_clause = text.split("Ceph Tracker")[0]
        assert "Bugzilla" not in enabled_clause
