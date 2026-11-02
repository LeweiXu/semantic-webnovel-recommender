"""Per-user personal library: the explicit shelf of novels a user has added.

Mirrors user_progress.py (atomic per-user JSON in DATA_DIR). The shelf shown on
the Library page is the union of these explicit entries and whatever the user has
reading progress on (so a novel you're reading always shows, even if you never
hit "add"), minus anything explicitly removed. Opening a novel auto-adds it; the
user can also add/remove by hand. Removing only drops the shelf entry and records
the id as removed so a progress-backed novel doesn't just reappear — it never
touches the file on disk (files are shared).

Archiving is the softer option: the novel stays in the library but moves off the
main Library page into the Archive. It's an id set like "removed", so it works
for progress-backed novels too. Opening an archived novel leaves it archived
(opening auto-adds, and that mustn't undo the archive); only an explicit
unarchive, or adding it to the library by hand, brings it back.

File shape (v2):
    {"v": 2, "items": {id: {url,title,kind,language,added}},
     "removed": [id, ...], "archived": [id, ...]}
An older flat {id: entry} file is read as the items map with no removals.
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
    """Return {"items", "removed", "archived"}, upgrading the old flat shape."""
    empty = {"items": {}, "removed": [], "archived": []}
    try:
        data = json.loads(_path(username).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return empty
    if not isinstance(data, dict):
        return empty
    if data.get("v") == 2:
        def ids(key: str) -> list:
            return data.get(key) if isinstance(data.get(key), list) else []
        items = data.get("items") if isinstance(data.get("items"), dict) else {}
        return {"items": items, "removed": ids("removed"), "archived": ids("archived")}
    # Legacy flat {id: entry} file.
    return {**empty, "items": data}


def _save(username: str, data: dict) -> None:
    path = _path(username)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "v": 2,
        "items": data["items"],
        "removed": data["removed"],
        "archived": data["archived"],
    }
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
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
    unarchive: bool = False,
) -> None:
    """Add a novel to the shelf. Idempotent: re-adding keeps the first ``added``.
    Clears any prior removal so opening a removed novel brings it back. An
    archived novel stays archived unless ``unarchive`` (an explicit add)."""
    with _lock:
        data = _load(username)
        if id in data["removed"]:
            data["removed"].remove(id)
        if unarchive and id in data["archived"]:
            data["archived"].remove(id)
        existing = data["items"].get(id)
        if existing is None:
            data["items"][id] = {
                "url": url,
                "title": title,
                "kind": kind,
                "language": language,
                "added": datetime.now().isoformat(timespec="seconds"),
            }
        _save(username, data)


def remove(username: str, id: str) -> bool:
    """Drop the shelf entry and mark the id removed (so a progress-backed novel
    doesn't reappear). Returns True if anything changed."""
    with _lock:
        data = _load(username)
        changed = data["items"].pop(id, None) is not None
        if id in data["archived"]:
            data["archived"].remove(id)
            changed = True
        if id not in data["removed"]:
            data["removed"].append(id)
            changed = True
        if changed:
            _save(username, data)
        return changed


def set_archived(username: str, id: str, archived: bool) -> bool:
    """Move a novel into (or back out of) the Archive. Returns True if changed."""
    with _lock:
        data = _load(username)
        if archived == (id in data["archived"]):
            return False
        if archived:
            data["archived"].append(id)
        else:
            data["archived"].remove(id)
        _save(username, data)
        return True


def load(username: str) -> dict:
    """Raw {"items", "removed", "archived"} for building the shelf."""
    with _lock:
        return _load(username)
