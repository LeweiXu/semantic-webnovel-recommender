"""Persistent per-novel reading progress.

A single JSON file (``data/reading_progress.json``) maps a novel URL to the
0-based index of the chapter to read next, so ``read.py`` can resume where you
left off and advance the bookmark as it serves chapters to the clipboard.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from scripts.repo_paths import DATA_DIR

PROGRESS_PATH = DATA_DIR / "reading_progress.json"


def _load() -> dict:
    try:
        return json.loads(PROGRESS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Atomic write so an interrupted run never corrupts the bookmark file.
    fd, tmp = tempfile.mkstemp(dir=PROGRESS_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, PROGRESS_PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def get_position(url: str) -> int:
    """Return the 0-based index of the next chapter to read (0 if unseen)."""
    entry = _load().get(url)
    return int(entry["position"]) if entry else 0


def set_position(url: str, position: int, *, title: str | None = None, total: int | None = None) -> None:
    data = _load()
    entry = data.get(url, {})
    entry["position"] = max(0, int(position))
    if title is not None:
        entry["title"] = title
    if total is not None:
        entry["total"] = int(total)
    entry["updated"] = datetime.now().isoformat(timespec="seconds")
    data[url] = entry
    _save(data)


def clear(url: str) -> bool:
    data = _load()
    if url in data:
        del data[url]
        _save(data)
        return True
    return False


def all_progress() -> dict:
    """Return the full ``{url: entry}`` map, most recently read first."""
    data = _load()
    return dict(
        sorted(
            data.items(),
            key=lambda item: item[1].get("updated", ""),
            reverse=True,
        )
    )
