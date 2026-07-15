"""Per-user reading bookmarks, independent from the read.py CLI bookmark."""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from scripts.repo_paths import DATA_DIR

PROGRESS_DIR = DATA_DIR / "user_progress"
_lock = threading.Lock()
_safe_username = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")


def _path(username: str) -> Path:
    if not _safe_username.fullmatch(username):
        raise ValueError("invalid username")
    return PROGRESS_DIR / f"{username}.json"


def _load(username: str) -> dict:
    try:
        data = json.loads(_path(username).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(username: str, data: dict) -> None:
    path = _path(username)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def get_entry(username: str, url: str) -> dict:
    with _lock:
        return dict(_load(username).get(url) or {})


def get_position(username: str, url: str) -> int:
    return int(get_entry(username, url).get("position", 0))


def set_position(
    username: str,
    url: str,
    position: int,
    line: int | None = None,
    *,
    title: str | None = None,
    total: int | None = None,
    force: bool = False,
) -> dict:
    """Persist a (chapter, rendered-line) bookmark.

    Normally monotonic: it never moves the bookmark backward (so re-reading an
    earlier chapter, or an accidental jump back, doesn't rewind progress). Pass
    ``force=True`` to set it to exactly (position, line) even if that's earlier,
    which is how the reader's "reset to here" control backtracks.
    """
    with _lock:
        data = _load(username)
        entry = data.get(url, {})
        target_position = max(0, int(position))
        current_line = entry.get("line")
        current = (int(entry.get("position", 0)), -1 if current_line is None else int(current_line))
        target_line = None if line is None else max(0, int(line))
        target = (target_position, -1 if target_line is None else target_line)
        if not entry or force or target > current:
            entry["position"] = target_position
            if target_line is None:
                entry.pop("line", None)
            else:
                entry["line"] = target_line
        if title is not None:
            entry["title"] = title
        if total is not None:
            entry["total"] = int(total)
        entry["updated"] = datetime.now().isoformat(timespec="seconds")
        data[url] = entry
        _save(username, data)
        return dict(entry)


def all_progress(username: str) -> dict:
    with _lock:
        data = _load(username)
    return dict(
        sorted(data.items(), key=lambda item: item[1].get("updated", ""), reverse=True)
    )
