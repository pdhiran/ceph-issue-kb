"""Background auto-updater — pulls latest changes from git on startup
and periodically thereafter.

Runs a resilient sync against ``origin/<branch>`` in a daemon thread so the
server starts instantly with whatever data is on disk, then:

- If only knowledge base files changed -> hot-reload the search engine.
- If source code (.py) changed -> ``os._exit(0)`` so Cursor restarts
  the MCP server process with the updated code.

A second daemon thread wakes up every *update_interval_hours* (default 1)
to repeat the check, so long-running processes stay current without
manual restarts.

Consumer clones often accumulate dirty index/worktree state (for example
staged deletions with files still on disk) that makes ``git pull --ff-only``
fail. Before pulling we discard local tracked modifications so the KB can
track upstream. Gitignored files (``.env``, ``.venv``) are left alone.

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


def _run_git(
    repo_dir: Path,
    args: list[str],
    *,
    timeout: int = 120,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _has_remote(repo_dir: Path) -> bool:
    """Return True if the git repo has at least one remote."""
    try:
        result = _run_git(repo_dir, ["remote"], timeout=10)
        return bool(result.stdout.strip())
    except Exception:
        return False


def _detect_default_branch(repo_dir: Path) -> str:
    """Detect the default branch from the remote HEAD, falling back to 'main'."""
    try:
        result = _run_git(
            repo_dir,
            ["symbolic-ref", "--short", "refs/remotes/origin/HEAD"],
            timeout=10,
        )
        if result.returncode == 0:
            return result.stdout.strip().replace("origin/", "")
    except Exception:
        pass
    return "main"


def _get_head_sha(repo_dir: Path) -> str | None:
    try:
        result = _run_git(repo_dir, ["rev-parse", "HEAD"], timeout=10)
        if result.returncode == 0:
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _changed_files(repo_dir: Path, old_sha: str, new_sha: str) -> list[str]:
    try:
        result = _run_git(
            repo_dir,
            ["diff", "--name-only", old_sha, new_sha],
            timeout=30,
        )
        if result.returncode == 0:
            return [f for f in result.stdout.strip().splitlines() if f]
    except Exception:
        pass
    return []


def _is_dirty(repo_dir: Path) -> bool:
    try:
        result = _run_git(repo_dir, ["status", "--porcelain"], timeout=30)
        return bool(result.stdout.strip())
    except Exception:
        return False


def _discard_local_tracked_changes(repo_dir: Path) -> str | None:
    """Reset index + worktree to HEAD when dirty.

    Returns a human-readable note when a reset ran, or an error message
    starting with ``failed`` / ``error``. Returns ``None`` when clean.
    """
    if not _is_dirty(repo_dir):
        return None
    try:
        result = _run_git(repo_dir, ["reset", "--hard", "HEAD"], timeout=60)
        if result.returncode != 0:
            stderr = result.stderr.strip() or result.stdout.strip()
            return f"failed to reset dirty worktree: {stderr}"
        return "Discarded local tracked changes blocking git pull"
    except subprocess.TimeoutExpired:
        return "error: git reset --hard timed out"
    except Exception as exc:
        return f"error resetting dirty worktree: {exc}"


def _git_lfs_pull(repo_dir: Path) -> None:
    """Best-effort ``git lfs pull`` so index blobs are materialised."""
    try:
        result = _run_git(repo_dir, ["lfs", "pull"], timeout=600)
        if result.returncode != 0:
            logger.warning(
                "Auto-update: git lfs pull failed: %s",
                result.stderr.strip() or result.stdout.strip(),
            )
    except FileNotFoundError:
        logger.debug("Auto-update: git-lfs not installed; skipping lfs pull")
    except subprocess.TimeoutExpired:
        logger.warning("Auto-update: git lfs pull timed out")
    except Exception as exc:
        logger.warning("Auto-update: git lfs pull error: %s", exc)


def _git_pull(repo_dir: Path) -> tuple[bool, str]:
    """Fetch + fast-forward to origin, recovering from dirty trees.

    Returns *(changed, message)*.
    """
    branch = _detect_default_branch(repo_dir)
    remote_ref = f"origin/{branch}"
    old_sha = _get_head_sha(repo_dir)

    try:
        fetch = _run_git(repo_dir, ["fetch", "origin", branch], timeout=180)
        if fetch.returncode != 0:
            stderr = fetch.stderr.strip() or fetch.stdout.strip()
            return False, f"git fetch failed: {stderr}"

        reset_note = _discard_local_tracked_changes(repo_dir)
        if reset_note:
            if reset_note.startswith(("failed", "error")):
                return False, reset_note
            logger.warning("Auto-update: %s", reset_note)

        pull = _run_git(
            repo_dir,
            ["merge", "--ff-only", remote_ref],
            timeout=180,
        )
        if pull.returncode != 0:
            stderr = (pull.stderr or pull.stdout or "").strip()
            # Consumer KB clones should track upstream; hard-reset as last resort.
            logger.warning(
                "Auto-update: ff-only merge failed (%s); hard-resetting to %s",
                stderr or "unknown error",
                remote_ref,
            )
            hard = _run_git(repo_dir, ["reset", "--hard", remote_ref], timeout=120)
            if hard.returncode != 0:
                err = hard.stderr.strip() or hard.stdout.strip()
                return False, f"git pull failed: {err or stderr}"

        _git_lfs_pull(repo_dir)

        new_sha = _get_head_sha(repo_dir)
        if old_sha and new_sha and old_sha == new_sha:
            return False, "Already up to date"
        if not old_sha and not new_sha:
            return False, "Already up to date"
        return True, pull.stdout.strip() or f"Updated to {remote_ref}"
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
    update_interval_hours: float = 1,
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

    if update_interval_hours > 0:
        interval_seconds = update_interval_hours * 3600
        stop_event = threading.Event()
        _periodic_stop = stop_event
        periodic = threading.Thread(
            target=_periodic_loop,
            args=(kb, kb_path, repo_root, interval_seconds, stop_event),
            daemon=True,
            name="kb-periodic-update",
        )
        periodic.start()
        logger.info(
            "Scheduled next KB update check in %sh",
            update_interval_hours,
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
