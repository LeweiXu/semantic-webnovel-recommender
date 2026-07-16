"""Per-user UI settings, persisted server-side alongside reading progress.

Stored as a small allowlisted blob per user so settings (theme, pinyin, type
size, ...) follow the account across devices instead of living only in one
browser's localStorage.

Settings are split into two independent profiles, "desktop" and "mobile", so a
phone/tablet can keep its own type size, column width, etc. On disk that's::

    {"desktop": {...allowlisted...}, "mobile": {...allowlisted...}}

Older files predate the split and are just a flat blob; those are read back as
the desktop profile (with an empty mobile profile) so nothing is lost.
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

PROFILES = ("desktop", "mobile")

# Only these keys are persisted (per profile), so a client can't stuff arbitrary
# data into the file. Values themselves are stored as-is (the frontend owns their
# meaning).
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


def _clean(settings: object) -> dict:
    if not isinstance(settings, dict):
        return {}
    return {k: settings[k] for k in ALLOWED_KEYS if k in settings}


def _read_raw(username: str) -> dict:
    try:
        data = json.loads(_path(username).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _normalize(raw: dict) -> dict:
    """Return the two-profile shape, migrating legacy flat blobs to desktop."""
    if any(p in raw for p in PROFILES):
        return {p: _clean(raw.get(p)) for p in PROFILES}
    return {"desktop": _clean(raw), "mobile": {}}


def _write(username: str, data: dict) -> None:
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


def get(username: str) -> dict:
    """Return {"desktop": {...}, "mobile": {...}} for the user."""
    with _lock:
        return _normalize(_read_raw(username))


def put(username: str, payload: dict) -> dict:
    """Merge in whichever profiles the payload carries and return the full set.

    Accepts the two-profile shape ``{"desktop": {...}, "mobile": {...}}`` (either
    profile optional, so a client can push just the one it changed). A flat blob
    with no profile keys is treated as a legacy desktop write.
    """
    with _lock:
        current = _normalize(_read_raw(username))
        if isinstance(payload, dict) and any(p in payload for p in PROFILES):
            for profile in PROFILES:
                if profile in payload:
                    current[profile] = _clean(payload[profile])
        else:
            current["desktop"] = _clean(payload)
        _write(username, current)
    return current
