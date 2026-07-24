"""Shared, per-book chapter-heading regexes.

Patterns are global rather than per-user: once a reader fixes a book's chapter
layout, every reader gets the same corrected table of contents. Matching is
limited to short individual lines, and unsafe regex constructs are rejected.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import threading
from pathlib import Path

from scripts.repo_paths import DATA_DIR

PATTERNS_PATH = DATA_DIR / "chapter_patterns.json"
MAX_PATTERN_LENGTH = 240
MAX_SAMPLE_LENGTH = 100
_lock = threading.Lock()


def _load() -> dict[str, str]:
    try:
        data = json.loads(PATTERNS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return {
        str(key): str(value)
        for key, value in data.items()
        if isinstance(key, str) and isinstance(value, str)
    } if isinstance(data, dict) else {}


def _save(data: dict[str, str]) -> None:
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
        return _load().get(book_key)


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
        data[book_key] = pattern
        _save(data)
    return pattern


def remove(book_key: str) -> bool:
    with _lock:
        data = _load()
        existed = data.pop(book_key, None) is not None
        if existed:
            _save(data)
        return existed


def infer(sample: str) -> str:
    """Infer a useful line regex from one selected chapter heading."""
    sample = " ".join(sample.strip().splitlines()).strip()
    if not sample:
        raise ValueError("Select one chapter heading first")
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


def matches(pattern: str, text: str) -> list[str]:
    heading_re = re.compile(validate(pattern), re.IGNORECASE)
    return [
        line.strip()
        for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
        if 0 < len(line.strip()) <= MAX_SAMPLE_LENGTH
        and heading_re.search(line.strip()) is not None
    ]
