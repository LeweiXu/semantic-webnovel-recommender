"""Per-user bookmarks: named positions a reader saved by hand.

Separate from user_progress.py, which holds the single automatic "where I left
off" bookmark per novel. These are the explicit ones: any number per novel, kept
until the user deletes them, never moved by reading.

Mirrors the other per-user stores (atomic per-user JSON under DATA_DIR), keyed
by the novel's progress url so a bookmark survives the slug/path a novel was
opened through.

File shape:
    {"v": 1, "items": {url: [{id, chapter, chapter_title, line, excerpt,
                              title, created}, ...]}}
Each list is newest first.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from scripts.repo_paths import DATA_DIR

BOOKMARK_DIR = DATA_DIR / "user_bookmarks"
# A reader can only usefully keep so many places in one book, and the whole list
# is sent on every open. Adding past the cap drops the oldest.
MAX_PER_NOVEL = 200
# Enough of the line to recognise the place, not enough to be a copy of the text.
MAX_EXCERPT = 120

_lock = threading.Lock()
_safe_username = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")


def _path(username: str) -> Path:
    if not _safe_username.fullmatch(username):
        raise ValueError("invalid username")
    return BOOKMARK_DIR / f"{username}.json"


def _load(username: str) -> dict[str, list[dict]]:
    try:
        data = json.loads(_path(username).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, dict):
        return {}
    return {url: list(rows) for url, rows in items.items() if isinstance(rows, list)}


def _save(username: str, items: dict[str, list[dict]]) -> None:
    path = _path(username)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"v": 1, "items": {url: rows for url, rows in items.items() if rows}}
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def list_for(username: str, url: str) -> list[dict]:
    """Bookmarks in one novel, newest first."""
    with _lock:
        return [dict(row) for row in _load(username).get(url, [])]


def add(
    username: str,
    url: str,
    *,
    chapter: int,
    line: int | None = None,
    chapter_title: str = "",
    excerpt: str = "",
    title: str = "",
) -> list[dict]:
    """Save a bookmark and return the novel's list. Re-bookmarking a position
    already saved is a no-op, so a double tap can't stack duplicates."""
    with _lock:
        items = _load(username)
        rows = items.get(url, [])
        target = (max(0, int(chapter)), None if line is None else max(0, int(line)))
        if not any((row.get("chapter"), row.get("line")) == target for row in rows):
            rows.insert(0, {
                "id": secrets.token_hex(8),
                "chapter": target[0],
                "chapter_title": chapter_title[:200],
                "line": target[1],
                "excerpt": " ".join(excerpt.split())[:MAX_EXCERPT],
                "title": title[:200],
                "created": datetime.now().isoformat(timespec="seconds"),
            })
            del rows[MAX_PER_NOVEL:]
            items[url] = rows
            _save(username, items)
        return [dict(row) for row in rows]


def remove(username: str, url: str, bookmark_id: str) -> list[dict]:
    """Delete one bookmark and return what's left for that novel."""
    with _lock:
        items = _load(username)
        rows = items.get(url, [])
        kept = [row for row in rows if row.get("id") != bookmark_id]
        if len(kept) != len(rows):
            items[url] = kept
            _save(username, items)
        return [dict(row) for row in kept]
