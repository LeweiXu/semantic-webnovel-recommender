"""Per-user UI settings, persisted server-side alongside reading progress.

Stored as a small allowlisted blob per user so settings (theme, pinyin, type
size, ...) follow the account across devices instead of living only in one
browser's localStorage.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path

from scripts.repo_paths import DATA_DIR

SETTINGS_DIR = DATA_DIR / "user_settings"
_lock = threading.Lock()
_safe_username = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")

# Only these keys are persisted, so a client can't stuff arbitrary data into the
# file. Values themselves are stored as-is (the frontend owns their meaning).
ALLOWED_KEYS = frozenset(
    {
        "theme", "pinyin", "synopsisPinyin", "fontSize", "leading", "tracking",
        "measure", "contrast", "mode",
    }
)


def _path(username: str) -> Path:
    if not _safe_username.fullmatch(username):
        raise ValueError("invalid username")
    return SETTINGS_DIR / f"{username}.json"


def get(username: str) -> dict:
    with _lock:
        try:
            data = json.loads(_path(username).read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}


def put(username: str, settings: dict) -> dict:
    clean = {k: settings[k] for k in ALLOWED_KEYS if k in settings}
    with _lock:
        path = _path(username)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(clean, handle, ensure_ascii=False, indent=2)
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)
    return clean
