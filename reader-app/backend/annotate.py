"""Offline Chinese annotation: segment text and attach per-character pinyin.

jieba segments the text into words; each Han word gets space-joined per-character
pinyin (so the frontend can align one ruby reading per character). Non-Han tokens
(punctuation, latin, digits, whitespace, newlines) pass through with ``py=None``.
Newlines are preserved as their own tokens so the frontend can rebuild
paragraphs.

Definitions are NOT produced here — they are fetched lazily on hover via the
dictionary endpoint, keeping chapter payloads small.
"""
from __future__ import annotations

import re

import jieba
from pypinyin import Style, pinyin

# CJK Unified Ideographs (incl. common extensions) — what we annotate.
_HAN_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_HAN_ONLY_RE = re.compile(r"^[㐀-䶿一-鿿豈-﫿]+$")


def _has_han(text: str) -> bool:
    return bool(_HAN_RE.search(text))


def _pinyin_for(word: str) -> str:
    # One reading per character, joined by spaces. heteronym=False picks the most
    # common reading (a known, accepted limitation for a reading aid).
    syllables = pinyin(word, style=Style.TONE, heteronym=False, errors="default")
    return " ".join(item[0] for item in syllables)


def tokenize(text: str) -> list[dict]:
    """Return a flat token list: [{"t": surface, "py": "pin yin" | None}, ...]."""
    tokens: list[dict] = []
    for segment in jieba.cut(text, HMM=True):
        if segment == "":
            continue
        # Split out newlines so paragraph structure survives to the client.
        if "\n" in segment and not _HAN_ONLY_RE.match(segment):
            for piece in re.split(r"(\n)", segment):
                if piece == "":
                    continue
                tokens.append({"t": piece, "py": None})
            continue
        if _has_han(segment):
            tokens.append({"t": segment, "py": _pinyin_for(segment)})
        else:
            tokens.append({"t": segment, "py": None})
    return tokens
