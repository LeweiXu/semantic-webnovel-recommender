"""Per-user personal library: the explicit shelf of novels a user has added.

Mirrors user_progress.py (atomic per-user JSON in DATA_DIR). The shelf is what
the Library page shows; reading progress just annotates these entries. Opening a
novel auto-adds it; the user can also add/remove explicitly. Removing only drops
the shelf entry — it never touches the file on disk (files are shared).
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from scripts.repo_paths import DATA_DIR

LIBRARY_DIR = DATA_DIR / "user_library"
_lock = threading.Lock()
_safe_username = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")


def _path(username: str) -> Path:
    if not _safe_username.fullmatch(username):
        raise ValueError("invalid username")
    return LIBRARY_DIR / f"{username}.json"


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


def add(
    username: str,
    id: str,
    *,
    url: str,
    title: str,
    kind: str = "novel",
    language: str = "zh",
) -> None:
    """Add a novel to the shelf. Idempotent: re-adding keeps the first ``added``."""
    with _lock:
        data = _load(username)
        if id in data:
            return
        data[id] = {
            "url": url,
            "title": title,
            "kind": kind,
            "language": language,
            "added": datetime.now().isoformat(timespec="seconds"),
        }
        _save(username, data)


def remove(username: str, id: str) -> bool:
    with _lock:
        data = _load(username)
        if id not in data:
            return False
        del data[id]
        _save(username, data)
        return True


def all_items(username: str) -> list[dict]:
    """Shelf entries (each with its ``id``), newest-added first."""
    with _lock:
        data = _load(username)
    # Start from reverse insertion order so a stable sort keeps the most-recently
    # added first even when two entries share a one-second ``added`` timestamp.
    items = [{"id": id, **entry} for id, entry in reversed(data.items())]
    items.sort(key=lambda e: e.get("added", ""), reverse=True)
    return items
