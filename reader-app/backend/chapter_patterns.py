"""JSON-backed per-book and global chapter-heading regexes."""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import uuid
from pathlib import Path

from scripts.repo_paths import DATA_DIR

PATTERNS_PATH = DATA_DIR / "chapter_patterns.json"
DEFAULT_PATTERNS_PATH = Path(__file__).resolve().parents[2] / "webnovel" / "chapter_heading_patterns.json"
MAX_PATTERN_LENGTH = 240
MAX_SAMPLE_LENGTH = 100
_lock = threading.Lock()


def _empty() -> dict:
    return {"version": 2, "books": {}, "globals": {}, "deleted_defaults": []}


def _load() -> dict:
    try:
        data = json.loads(PATTERNS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    if data.get("version") == 2:
        return {
            "version": 2,
            "books": data.get("books") if isinstance(data.get("books"), dict) else {},
            "globals": data.get("globals") if isinstance(data.get("globals"), dict) else {},
            "deleted_defaults": (
                data.get("deleted_defaults")
                if isinstance(data.get("deleted_defaults"), list)
                else []
            ),
        }
    # Upgrade the original flat {book_key: regex} store.
    return {
        **_empty(),
        "books": {
            str(key): str(value)
            for key, value in data.items()
            if isinstance(key, str) and isinstance(value, str)
        },
    }


def _save(data: dict) -> None:
    PATTERNS_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=PATTERNS_PATH.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2, sort_keys=True)
        os.replace(tmp, PATTERNS_PATH)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def get(book_key: str) -> str | None:
    with _lock:
        value = _load()["books"].get(book_key)
        return str(value) if isinstance(value, str) else None


def validate(pattern: str) -> str:
    pattern = pattern.strip()
    if not pattern:
        raise ValueError("Enter a chapter-heading regex")
    if len(pattern) > MAX_PATTERN_LENGTH:
        raise ValueError(f"Regex must be at most {MAX_PATTERN_LENGTH} characters")
    if not pattern.startswith("^"):
        raise ValueError("Regex must be anchored to the start of a line with ^")
    # Disallow execution-heavy/opaque constructs. Chapter patterns only need
    # ordinary groups, alternation, character classes, and bounded repetition.
    if re.search(r"\(\?(?!:)", pattern):
        raise ValueError("Lookarounds and special regex groups are not supported")
    if re.search(r"\\[1-9]", pattern):
        raise ValueError("Regex backreferences are not supported")
    if re.search(r"\([^)]*\)\s*(?:[+*]|\{)", pattern):
        raise ValueError("Repeated regex groups are not supported")
    try:
        re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise ValueError(f"Invalid regex: {exc}") from exc
    return pattern


def set_pattern(book_key: str, pattern: str) -> str:
    pattern = validate(pattern)
    with _lock:
        data = _load()
        data["books"][book_key] = pattern
        _save(data)
    return pattern


def remove(book_key: str) -> bool:
    with _lock:
        data = _load()
        existed = data["books"].pop(book_key, None) is not None
        if existed:
            _save(data)
        return existed


def _infer_one(sample: str) -> str:
    if not sample:
        raise ValueError("Enter at least one chapter heading example")
    if len(sample) > MAX_SAMPLE_LENGTH:
        raise ValueError(f"Chapter headings must be at most {MAX_SAMPLE_LENGTH} characters")

    chinese_number = "零一二三四五六七八九十百千万亿两〇"
    if re.match(rf"^第[{chinese_number}0-9]+[章回卷节部篇折]", sample):
        return rf"^\s*第[{chinese_number}0-9]+[章回卷节部篇折]\s*.*$"
    if re.match(r"^(?:chapter|prologue|epilogue|interlude|part|volume|book|act)\b", sample, re.I):
        return r"^\s*(?:chapter|prologue|epilogue|interlude|part|volume|book|act)\b.{0,98}$"
    # The common undetected form: "1重生", "2 归来", "003、终章".
    if re.match(r"^\d+", sample):
        return r"^\s*\d+\s*(?:[、.)）]\s*)?\S.{0,98}$"
    if re.match(rf"^[{chinese_number}]+", sample):
        return rf"^\s*[{chinese_number}]+\s*(?:[、.)）]\s*)?\S.{{0,98}}$"

    # General numbered prefix, e.g. "正文 12 重生". Keep the literal prefix and
    # generalise only the number and following title.
    numbered = re.match(r"^(.*?\D)\s*\d+\s*(.*)$", sample)
    if numbered:
        prefix = re.escape(numbered.group(1).strip())
        return rf"^\s*{prefix}\s*\d+\s*\S.{{0,98}}$"
    raise ValueError(
        "Could not infer a series from that heading; edit the regex manually"
    )


def infer(sample: str) -> str:
    """Infer one anchored regex from one or more newline-separated examples."""
    examples = [line.strip() for line in sample.splitlines() if line.strip()]
    if not examples:
        raise ValueError("Enter at least one chapter heading example")
    if len(examples) > 20:
        raise ValueError("Use at most 20 heading examples")
    patterns = list(dict.fromkeys(_infer_one(example) for example in examples))
    if len(patterns) == 1:
        result = patterns[0]
    else:
        branches = [
            pattern[1:-1] if pattern.startswith("^") and pattern.endswith("$") else pattern
            for pattern in patterns
        ]
        result = "^(?:" + "|".join(f"(?:{branch})" for branch in branches) + ")$"
    result = validate(result)
    compiled = re.compile(result, re.IGNORECASE)
    if any(compiled.search(example) is None for example in examples):
        raise ValueError("Could not generate one regex matching every example")
    return result


def matches(pattern: str, text: str) -> list[str]:
    heading_re = re.compile(validate(pattern), re.IGNORECASE)
    return [
        line.strip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if 0 < len(line.strip()) <= MAX_SAMPLE_LENGTH
        and heading_re.search(line.strip()) is not None
    ]


def _default_globals() -> list[dict]:
    try:
        data = json.loads(DEFAULT_PATTERNS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [
        {
            "id": str(item["id"]),
            "label": str(item.get("label") or item["id"]),
            "pattern": str(item["pattern"]),
            "builtin": True,
        }
        for item in data
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and isinstance(item.get("pattern"), str)
    ]


def list_globals() -> list[dict]:
    with _lock:
        data = _load()
    deleted = set(str(item) for item in data["deleted_defaults"])
    overrides = data["globals"]
    result: list[dict] = []
    seen: set[str] = set()
    for item in _default_globals():
        pattern_id = item["id"]
        if pattern_id in deleted:
            continue
        override = overrides.get(pattern_id)
        if isinstance(override, dict):
            item = {
                "id": pattern_id,
                "label": str(override.get("label") or item["label"]),
                "pattern": str(override.get("pattern") or item["pattern"]),
                "builtin": True,
            }
        result.append(item)
        seen.add(pattern_id)
    for pattern_id, item in overrides.items():
        if pattern_id in seen or not isinstance(item, dict):
            continue
        result.append({
            "id": str(pattern_id),
            "label": str(item.get("label") or pattern_id),
            "pattern": str(item.get("pattern") or ""),
            "builtin": False,
        })
    return result


def effective_patterns() -> list[str]:
    return [item["pattern"] for item in list_globals()]


def save_global(
    *,
    pattern: str,
    label: str,
    pattern_id: str | None = None,
) -> dict:
    pattern = validate(pattern)
    label = label.strip()
    if not label:
        raise ValueError("Enter a pattern name")
    if len(label) > 80:
        raise ValueError("Pattern name must be at most 80 characters")
    pattern_id = pattern_id or f"global-{uuid.uuid4().hex[:12]}"
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", pattern_id):
        raise ValueError("Invalid pattern id")
    with _lock:
        data = _load()
        data["globals"][pattern_id] = {"label": label, "pattern": pattern}
        data["deleted_defaults"] = [
            item for item in data["deleted_defaults"] if item != pattern_id
        ]
        _save(data)
    return next(item for item in list_globals() if item["id"] == pattern_id)


def remove_global(pattern_id: str) -> bool:
    default_ids = {item["id"] for item in _default_globals()}
    with _lock:
        data = _load()
        existed = data["globals"].pop(pattern_id, None) is not None
        if pattern_id in default_ids and pattern_id not in data["deleted_defaults"]:
            data["deleted_defaults"].append(pattern_id)
            existed = True
        if existed:
            _save(data)
        return existed
