"""Back library/uploads/ up to its own git repo (LeweiXu/novels-backup).

On the server, library/uploads/ is a git checkout whose root is the uploads
folder itself. Anything that changes it (an upload, rename, delete, or a bulk
import) calls schedule(), which waits for a quiet spell so a batch of uploads
lands as one commit, then commits everything and pushes.

If uploads/ isn't a git repo (local dev, tests) this is a no-op. A failed push
just gets logged: the commit stays local and goes out with the next sync.
"""
from __future__ import annotations

import logging
import os
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from scripts.repo_paths import category_dir

log = logging.getLogger("novel.backup")

# Seconds of quiet after the last change before committing. The upload modal
# confirms books one at a time, so this is what turns a batch into one commit.
DELAY_S = float(os.environ.get("NOVEL_BACKUP_DELAY", "60"))

_timer: threading.Timer | None = None
_timer_lock = threading.Lock()
_run_lock = threading.Lock()


def uploads_dir() -> Path:
    return category_dir("uploads")


def _git(repo: Path, *args: str, timeout: float = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo, capture_output=True, text=True, timeout=timeout,
    )


def sync(repo: Path | None = None) -> bool:
    """Commit whatever changed in uploads/ and push. True if it all went through."""
    repo = repo or uploads_dir()
    if not (repo / ".git").exists():
        return False
    with _run_lock:
        try:
            _git(repo, "add", "-A")
            changed = [
                line[3:] for line in _git(repo, "status", "--porcelain").stdout.splitlines()
            ]
            if changed:
                stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
                noun = "file" if len(changed) == 1 else "files"
                commit = _git(repo, "commit", "-q", "-m", f"backup {stamp} ({len(changed)} {noun})")
                if commit.returncode != 0:
                    log.warning("backup commit failed: %s", commit.stderr.strip())
                    return False
            # Push even with nothing new: an earlier push may have failed.
            push = _git(repo, "push", "-q", "origin", "HEAD", timeout=900)
            if push.returncode != 0:
                log.warning("backup push failed: %s", push.stderr.strip())
                return False
            return True
        except (OSError, subprocess.SubprocessError) as exc:
            log.warning("backup failed: %s", exc)
            return False


def schedule(delay: float | None = None) -> None:
    """Sync after `delay` seconds, restarting the countdown if one is pending."""
    global _timer
    with _timer_lock:
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(DELAY_S if delay is None else delay, sync)
        _timer.daemon = True
        _timer.start()
