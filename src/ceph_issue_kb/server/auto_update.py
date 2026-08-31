"""Background auto-updater — pulls code from git and the issue index from
GitHub Releases.

Runs in a daemon thread so the server starts instantly with whatever data
is on disk, then:

- If the knowledge-base tarball is newer -> download, extract, hot-reload.
- If source code (.py) changed via ``git pull --ff-only`` -> ``os._exit(0)``
  so Cursor respawns the MCP subprocess (the IDE stays open).
- ``./update_index.sh`` touches ``.reload_trigger`` -> in-process reload.
  The watcher waits while ``knowledge/.indexing_lock`` is held.

A second daemon thread wakes up every *update_interval_hours* (default
``DEFAULT_UPDATE_INTERVAL_HOURS``, 12) to repeat the check, so long-running
processes stay current without manual restarts. MCP and REST argparse
use the same default.

Every failure path logs a warning and returns — the server is never
blocked or crashed by this.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tarfile
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import requests

if TYPE_CHECKING:
    from ceph_issue_kb.server.kb import KnowledgeBase

logger = logging.getLogger(__name__)

_periodic_stop: threading.Event | None = None
_auto_update_threads: list[threading.Thread] = []

TRIGGER_NAME = ".reload_trigger"
TRIGGER_POLL_SECONDS = 5.0

DEFAULT_RELEASE_REPO = os.environ.get("CEPH_ISSUE_KB_RELEASE_REPO", "pdhiran/ceph-issue-kb")
RELEASE_TAG = "knowledge"
ASSET_NAME = "knowledge.tar.gz"
STAMP_NAME = ".release_etag"
INDEX_LOCK_NAME = ".indexing_lock"
INDEX_LOCK_STALE_SECONDS = 6 * 3600
KNOWLEDGE_DIRNAME = "knowledge"

# Shared with MCP and REST argparse so the 12h default cannot drift.
DEFAULT_UPDATE_INTERVAL_HOURS = 12

AUTO_UPDATE_CLI_HELP = (
    "If knowledge/ is missing, download the GitHub Release (no JIRA tokens). "
    "Then git pull + Release refresh on a timer, and watch .reload_trigger "
    "(default: on). --no-auto-update skips all of these: first-run download, "
    "periodic update, and the trigger watcher."
)

UPDATE_INTERVAL_CLI_HELP = (
    f"Hours between periodic update checks "
    f"(default: {DEFAULT_UPDATE_INTERVAL_HOURS:g}, 0=disable periodic; "
    "trigger watcher still runs)"
)

_UA = {"User-Agent": "ceph-issue-kb"}
_HEAD_TIMEOUT = 30
_DOWNLOAD_TIMEOUT = 600


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


def _knowledge_root(repo_root: Path) -> Path:
    return repo_root / KNOWLEDGE_DIRNAME


def _stamp_path(repo_root: Path) -> Path:
    return _knowledge_root(repo_root) / STAMP_NAME


def _indexing_in_progress(repo_root: Path) -> bool:
    """True when the maintainer indexer holds knowledge/.indexing_lock."""
    lock = _knowledge_root(repo_root) / INDEX_LOCK_NAME
    if not lock.exists():
        return False
    try:
        age = time.time() - lock.stat().st_mtime
    except OSError:
        return False
    if age > INDEX_LOCK_STALE_SECONDS:
        logger.warning(
            "Ignoring stale indexing lock (%.0fh old)", age / 3600,
        )
        return False
    return True


def _asset_url(repo: str | None = None) -> str:
    slug = repo or DEFAULT_RELEASE_REPO
    return f"https://github.com/{slug}/releases/download/{RELEASE_TAG}/{ASSET_NAME}"


def _read_stamp(repo_root: Path) -> str:
    path = _stamp_path(repo_root)
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _write_stamp(repo_root: Path, stamp: str) -> None:
    path = _stamp_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(stamp + "\n", encoding="utf-8")


def _validator_from_headers(headers: dict) -> str:
    """Prefer ETag, then Last-Modified, then Content-Length."""
    etag = headers.get("ETag") or headers.get("etag") or ""
    if etag:
        return etag.strip()
    modified = headers.get("Last-Modified") or headers.get("last-modified") or ""
    if modified:
        return modified.strip()
    length = headers.get("Content-Length") or headers.get("content-length") or ""
    return length.strip()


def _head_asset(url: str) -> tuple[int, str]:
    """HEAD the release asset. Returns *(status_code, validator)*."""
    response = requests.head(
        url, allow_redirects=True, timeout=_HEAD_TIMEOUT, headers=_UA,
    )
    return response.status_code, _validator_from_headers(response.headers)


def _resolve_kb_path(root: Path) -> Path | None:
    """Return the KB directory under *root*, or None if it is not populated."""
    if not root.is_dir():
        return None
    if (root / "issues.json").exists():
        return root
    source_dirs = [
        sub for sub in sorted(root.iterdir())
        if sub.is_dir() and (sub / "issues.json").exists()
    ]
    if source_dirs:
        return root
    for sub in sorted(root.iterdir()):
        if not sub.is_dir() or sub.name.startswith("."):
            continue
        if (sub / "issues.json").exists():
            return sub
        inner = [
            s for s in sorted(sub.iterdir())
            if s.is_dir() and (s / "issues.json").exists()
        ]
        if inner:
            return sub
    return None


def _safe_extract(tar: tarfile.TarFile, dest: Path) -> None:
    """Extract *tar* into *dest*, rejecting path-escape members."""
    dest = dest.resolve()
    for member in tar.getmembers():
        target = (dest / member.name).resolve()
        if not str(target).startswith(str(dest) + os.sep) and target != dest:
            raise ValueError(f"unsafe path in archive: {member.name}")
    kwargs: dict = {}
    if hasattr(tarfile, "data_filter"):
        kwargs["filter"] = "data"
    tar.extractall(dest, **kwargs)


def _install_extracted(staging: Path, knowledge_root: Path) -> None:
    """Move extracted archive members into *knowledge_root*, replacing existing."""
    if _indexing_in_progress(knowledge_root.parent):
        raise RuntimeError(
            "indexing lock present; refusing to replace knowledge/"
        )
    knowledge_root.mkdir(parents=True, exist_ok=True)
    for child in staging.iterdir():
        if child.name in {STAMP_NAME, ".staging"}:
            continue
        dest = knowledge_root / child.name
        if dest.exists():
            if dest.is_dir():
                shutil.rmtree(dest)
            else:
                dest.unlink()
        shutil.move(str(child), str(dest))


def _sync_knowledge_release(repo_root: Path) -> tuple[bool, str]:
    """Download and extract the knowledge tarball if the remote asset is newer.

    Returns *(changed, message)*. Never raises — callers log *message*.
    """
    url = _asset_url()
    knowledge_root = _knowledge_root(repo_root)
    if _indexing_in_progress(repo_root):
        return False, "Indexing in progress; skipping release download"

    have_kb = _resolve_kb_path(knowledge_root) is not None
    local = _read_stamp(repo_root)
    # Maintainer tree: local index exists but was never a Release install.
    # Do not replace it with whatever GitHub currently has.
    if have_kb and not local:
        return False, "Local index has no release stamp; skipping download"

    try:
        status, validator = _head_asset(url)
    except Exception as exc:
        return False, f"knowledge release HEAD failed: {exc}"

    if status == 404:
        return False, (
            f"knowledge release not found at {url} "
            "(maintainer: run ./update_index.sh)"
        )
    if status >= 400:
        return False, f"knowledge release HEAD returned HTTP {status}"

    if have_kb and validator and validator == local:
        return False, "Knowledge index is up to date"

    knowledge_root.mkdir(parents=True, exist_ok=True)
    tar_path = knowledge_root / ".knowledge.tar.gz.partial"
    staging = knowledge_root / ".staging"
    try:
        logger.info("Downloading knowledge index from GitHub Releases")
        with requests.get(
            url, stream=True, timeout=_DOWNLOAD_TIMEOUT, headers=_UA,
        ) as response:
            if response.status_code >= 400:
                return False, f"knowledge download returned HTTP {response.status_code}"
            if not validator:
                validator = _validator_from_headers(response.headers)
            with open(tar_path, "wb") as fh:
                for chunk in response.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        fh.write(chunk)

        if staging.exists():
            shutil.rmtree(staging)
        staging.mkdir()
        with tarfile.open(tar_path, "r:gz") as tar:
            _safe_extract(tar, staging)
        if _indexing_in_progress(repo_root):
            return False, "Indexing started during download; aborting install"
        _install_extracted(staging, knowledge_root)
        if validator:
            _write_stamp(repo_root, validator)
        return True, "Knowledge index downloaded"
    except Exception as exc:
        return False, f"knowledge download failed: {exc}"
    finally:
        tar_path.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)


def ensure_knowledge(start: Path) -> Path | None:
    """Download the index from GitHub Releases if missing.

    Public HTTPS to the ``knowledge`` Release asset only. Does not read
    JIRA / RHKB tokens. Returns the KB path, or None.
    """
    repo_root = _find_repo_root(start) or _find_repo_root(Path(__file__))
    if repo_root is None:
        return None
    changed, message = _sync_knowledge_release(repo_root)
    if changed:
        logger.info("%s", message)
    elif "failed" in message.lower() or "not found" in message.lower() or "HTTP" in message:
        logger.warning("Auto-update: %s", message)
    return _resolve_kb_path(_knowledge_root(repo_root))


def _do_update(kb: KnowledgeBase, kb_path: Path, repo_root: Path) -> None:
    """Perform the update — called in the background thread."""
    try:
        count_before = len(kb._state.search.issues)
        old_sha = _get_head_sha(repo_root)

        code_changed = False
        if _has_remote(repo_root):
            code_changed, pull_msg = _git_pull(repo_root)
            if (
                "failed" in pull_msg.lower()
                or "error" in pull_msg.lower()
                or "timed out" in pull_msg.lower()
            ):
                logger.warning("Auto-update: %s", pull_msg)

        if code_changed:
            new_sha = _get_head_sha(repo_root)
            files = _changed_files(repo_root, old_sha, new_sha) if old_sha and new_sha else []
            if _has_code_changes(files):
                logger.info("Code changes detected, restarting server")
                os._exit(0)
                return

        kb_changed, kb_msg = _sync_knowledge_release(repo_root)
        if (
            "failed" in kb_msg.lower()
            or "not found" in kb_msg.lower()
            or "HTTP" in kb_msg
        ):
            logger.warning("Auto-update: %s", kb_msg)

        if not kb_changed:
            if not code_changed:
                logger.info("Knowledge base is up to date (%d issues)", count_before)
            return

        if not _hot_reload(kb, kb_path, repo_root):
            logger.info("Indexing lock present after download; skipping reload")
            return
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


def _trigger_mtime(repo_root: Path) -> float:
    try:
        return (repo_root / TRIGGER_NAME).stat().st_mtime
    except OSError:
        return 0.0


def _hot_reload(kb: KnowledgeBase, kb_path: Path, repo_root: Path) -> bool:
    """Reload from disk. Returns False if the indexer lock is held."""
    if _indexing_in_progress(repo_root):
        return False
    resolved = _resolve_kb_path(_knowledge_root(repo_root)) or kb_path
    kb.reload(resolved)
    return True


def _trigger_loop(
    kb: KnowledgeBase,
    kb_path: Path,
    repo_root: Path,
    stop_event: threading.Event,
) -> None:
    last = _trigger_mtime(repo_root)
    while not stop_event.wait(timeout=TRIGGER_POLL_SECONDS):
        now = _trigger_mtime(repo_root)
        if now <= last + 0.01:
            continue
        # Do not consume the trigger while locked — SearchEngine.load reads
        # issues.json in place; indexer write_text() truncates first.
        if _indexing_in_progress(repo_root):
            logger.info("Reload trigger detected, waiting for indexing lock")
            continue
        logger.info("Reload trigger detected, hot-reloading issue index")
        try:
            if not _hot_reload(kb, kb_path, repo_root):
                continue
            last = now
        except Exception as exc:
            last = now
            logger.warning("Trigger reload failed: %s", exc)


def _join_auto_update_threads(timeout: float = 1.0) -> None:
    global _auto_update_threads  # noqa: PLW0603
    for thread in _auto_update_threads:
        thread.join(timeout=timeout)
    _auto_update_threads = []


def start_auto_update(
    kb: KnowledgeBase,
    kb_path: Path | None,
    *,
    update_interval_hours: float = DEFAULT_UPDATE_INTERVAL_HOURS,
) -> None:
    """Pull latest code from git and the issue index from GitHub Releases.

    Always watches ``.reload_trigger`` so ``./update_index.sh`` hot-reloads
    without restarting Cursor. Git pull still requires a remote; the
    Release tarball refresh does not (knowledge is not in git).
    Default interval is ``DEFAULT_UPDATE_INTERVAL_HOURS`` (12), matching
    MCP and REST ``--update-interval``.

    If *kb_path* is None (first Release download failed), still start the
    watcher from the git repo so a later periodic check can load the index.
    """
    global _periodic_stop  # noqa: PLW0603

    if _periodic_stop is not None and not _periodic_stop.is_set():
        logger.warning("Auto-update already running; skipping duplicate start")
        return

    _join_auto_update_threads()

    if kb_path is None:
        repo_root = _find_repo_root(Path.cwd()) or _find_repo_root(Path(__file__))
        if repo_root is None:
            logger.debug("Auto-update skipped: no KB path and not a git repository")
            return
        kb_path = _knowledge_root(repo_root)
    else:
        repo_root = _find_repo_root(kb_path)
        if repo_root is None:
            path = kb_path.resolve()
            if path.name == "issues-2024-2025":
                repo_root = path.parent.parent
            elif path.name == "knowledge":
                repo_root = path.parent
            else:
                repo_root = path

    stop_event = threading.Event()
    _periodic_stop = stop_event

    # _do_update skips git pull when there is no remote, then still
    # refreshes the Release tarball. Knowledge is not stored in git.
    if not _has_remote(repo_root):
        logger.debug(
            "No git remote — skip pull, still refreshing Release tarball "
            "and watching .reload_trigger",
        )

    thread = threading.Thread(
        target=_do_update,
        args=(kb, kb_path, repo_root),
        daemon=True,
        name="kb-auto-update",
    )
    thread.start()
    _auto_update_threads.append(thread)

    if update_interval_hours > 0:
        interval_seconds = update_interval_hours * 3600
        periodic = threading.Thread(
            target=_periodic_loop,
            args=(kb, kb_path, repo_root, interval_seconds, stop_event),
            daemon=True,
            name="kb-periodic-update",
        )
        periodic.start()
        _auto_update_threads.append(periodic)
        logger.info(
            "Scheduled next KB update check in %dh",
            int(update_interval_hours),
        )

    trigger = threading.Thread(
        target=_trigger_loop,
        args=(kb, kb_path, repo_root, stop_event),
        daemon=True,
        name="kb-reload-trigger",
    )
    trigger.start()
    _auto_update_threads.append(trigger)


def stop_auto_update() -> None:
    """Signal watcher threads to stop and join them.

    Primarily useful for tests.  The threads are daemons, so they will
    also die when the process exits.
    """
    global _periodic_stop  # noqa: PLW0603
    if _periodic_stop is not None:
        _periodic_stop.set()
        _periodic_stop = None
    _join_auto_update_threads()
