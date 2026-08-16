"""Background auto-updater — pulls latest changes from git on startup
and periodically thereafter.

Runs ``git pull --ff-only origin <branch>`` in a daemon thread so the
server starts instantly with whatever data is on disk, then:

- If only knowledge base files changed -> hot-reload the search engine.
- If source code (.py) changed -> ``os._exit(0)`` so Cursor restarts
  the MCP server process with the updated code.

A second daemon thread wakes up every *update_interval_hours* (default 12)
to repeat the check, so long-running processes stay current without
manual restarts.

Every failure path logs a warning and returns — the server is never
blocked or crashed by this.
"""

from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ceph_issue_kb.server.kb import KnowledgeBase

logger = logging.getLogger(__name__)

_periodic_stop: threading.Event | None = None


def _find_repo_root(start: Path) -> Path | None:
    """Walk up from *start* to find the nearest ``.git`` directory."""
    current = start.resolve()
    for parent in [current, *current.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _has_remote(repo_dir: Path) -> bool:
    """Return True if the git repo has at least one remote."""
    try:
        result = subprocess.run(
            ["git", "remote"],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=10,
        )
        return bool(result.stdout.strip())
    except Exception:
        return False


def _detect_default_branch(repo_dir: Path) -> str:
    """Detect the default branch from the remote HEAD, falling back to 'main'."""
    try:
        result = subprocess.run(
            ["git", "symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            capture_output=True, text=True, cwd=str(repo_dir), timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip().replace("origin/", "")
    except Exception:
        pass
    return "main"


def _get_head_sha(repo_dir: Path) -> str | None:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=repo_dir, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _changed_files(repo_dir: Path, old_sha: str, new_sha: str) -> list[str]:
    try:
        result = subprocess.run(
            ["git", "diff", "--name-only", old_sha, new_sha],
            cwd=repo_dir, capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return [f for f in result.stdout.strip().splitlines() if f]
    except Exception:
        pass
    return []


def _git_pull(repo_dir: Path) -> tuple[bool, str]:
    """Run ``git pull --ff-only`` and return *(changed, message)*."""
    branch = _detect_default_branch(repo_dir)
    try:
        result = subprocess.run(
            ["git", "pull", "--ff-only", "origin", branch],
            cwd=repo_dir,
            capture_output=True,
            text=True,
            timeout=120,
        )
        output = result.stdout.strip()
        if result.returncode != 0:
            stderr = result.stderr.strip()
            return False, f"git pull failed: {stderr or output}"
        if "Already up to date" in output:
            return False, "Already up to date"
        return True, output
    except subprocess.TimeoutExpired:
        return False, "git pull timed out"
    except Exception as exc:
        return False, f"git pull error: {exc}"


def _has_code_changes(files: list[str]) -> bool:
    return any(f.endswith(".py") for f in files)


def _do_update(kb: KnowledgeBase, kb_path: Path, repo_root: Path) -> None:
    """Perform the update — called in the background thread."""
    try:
        count_before = len(kb._state.search.issues)
        old_sha = _get_head_sha(repo_root)

        changed, message = _git_pull(repo_root)
        if not changed:
            if "failed" in message.lower() or "error" in message.lower() or "timed out" in message.lower():
                logger.warning("Auto-update: %s", message)
            else:
                logger.info("Knowledge base is up to date (%d issues)", count_before)
            return

        new_sha = _get_head_sha(repo_root)
        files = _changed_files(repo_root, old_sha, new_sha) if old_sha and new_sha else []

        if _has_code_changes(files):
            logger.info("Code changes detected, restarting server")
            os._exit(0)

        kb.reload(kb_path)
        count_after = len(kb._state.search.issues)
        logger.info(
            "Knowledge base updated: %d -> %d issues",
            count_before,
            count_after,
        )
    except Exception as exc:
        logger.warning("Auto-update failed, continuing with existing data: %s", exc)


def _reload_trigger_loop(
    kb: KnowledgeBase,
    kb_path: Path,
    repo_root: Path,
    stop_event: threading.Event,
) -> None:
    """Poll for .reload_trigger file every 5s; reload KB when found."""
    trigger_file = repo_root / ".reload_trigger"
    while not stop_event.wait(timeout=5):
        if trigger_file.exists():
            try:
                trigger_file.unlink(missing_ok=True)
                count_before = len(kb._state.search.issues)
                kb.reload(kb_path)
                count_after = len(kb._state.search.issues)
                logger.info(
                    "Hot-reload triggered: %d -> %d issues",
                    count_before, count_after,
                )
            except Exception as exc:
                logger.warning("Trigger reload failed: %s", exc)


def _periodic_loop(
    kb: KnowledgeBase,
    kb_path: Path,
    repo_root: Path,
    interval_seconds: float,
    stop_event: threading.Event,
) -> None:
    """Sleep for *interval_seconds*, then update; repeat until stopped."""
    while not stop_event.wait(timeout=interval_seconds):
        logger.info("Periodic KB update check triggered")
        _do_update(kb, kb_path, repo_root)


def start_auto_update(
    kb: KnowledgeBase,
    kb_path: Path | None,
    *,
    update_interval_hours: float = 12,
) -> None:
    """Pull latest KB from git now and schedule periodic re-checks.

    Safe to call unconditionally — silently skips if the KB directory
    is not inside a git repository or has no remote configured.

    Parameters
    ----------
    update_interval_hours:
        Hours between periodic update checks.  Set to ``0`` to disable
        periodic checks (startup pull still runs).
    """
    global _periodic_stop  # noqa: PLW0603

    if _periodic_stop is not None and not _periodic_stop.is_set():
        logger.warning("Auto-update already running; skipping duplicate start")
        return

    if kb_path is None:
        logger.debug("Auto-update skipped: no KB path")
        return

    repo_root = _find_repo_root(kb_path)
    if repo_root is None:
        logger.debug("Auto-update skipped: not a git repository")
        return

    if not _has_remote(repo_root):
        logger.debug("Auto-update skipped: no git remote configured")
        return

    thread = threading.Thread(
        target=_do_update,
        args=(kb, kb_path, repo_root),
        daemon=True,
        name="kb-auto-update",
    )
    thread.start()

    # File-based reload trigger (5s polling) — allows update_index.sh
    # to signal immediate reload without waiting for the periodic cycle.
    stop_event = threading.Event()
    _periodic_stop = stop_event
    trigger_thread = threading.Thread(
        target=_reload_trigger_loop,
        args=(kb, kb_path, repo_root, stop_event),
        daemon=True,
        name="kb-reload-trigger",
    )
    trigger_thread.start()

    if update_interval_hours > 0:
        interval_seconds = update_interval_hours * 3600
        periodic = threading.Thread(
            target=_periodic_loop,
            args=(kb, kb_path, repo_root, interval_seconds, stop_event),
            daemon=True,
            name="kb-periodic-update",
        )
        periodic.start()
        logger.info(
            "Scheduled next KB update check in %dh",
            int(update_interval_hours),
        )


def stop_auto_update() -> None:
    """Signal the periodic update thread to stop.

    Primarily useful for tests.  The thread is a daemon, so it will
    also die when the process exits.
    """
    global _periodic_stop  # noqa: PLW0603
    if _periodic_stop is not None:
        _periodic_stop.set()
        _periodic_stop = None
