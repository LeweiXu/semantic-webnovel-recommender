"""CC-CEDICT lookup (offline).

Parses the vendored ``reader-app/data/cedict_ts.u8`` once into
``{simplified: [entry, ...]}`` and answers per-word lookups (cached). On a miss
for a multi-character word, the caller can fall back to per-character lookups so
every Han token yields something.

CC-CEDICT is CC BY-SA 4.0 — see ``reader-app/data/ATTRIBUTION.md``.
"""
from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
CEDICT_PATH = DATA_DIR / "cedict_ts.u8"

_LINE_RE = re.compile(r"^(\S+)\s+(\S+)\s+\[([^\]]*)\]\s+/(.*)/\s*$")

# Numbered pinyin (e.g. "shi4") -> tone-marked ("shì").
_TONE_MARKS = {
    "a": "āáǎàa", "e": "ēéěèe", "i": "īíǐìi",
    "o": "ōóǒòo", "u": "ūúǔùu", "ü": "ǖǘǚǜü",
}
_VOWELS = "aeiouü"

_index: dict[str, list[dict]] | None = None


def _toned_syllable(syl: str) -> str:
    match = re.match(r"^([a-zü]+)([1-5])$", syl, re.IGNORECASE)
    if not match:
        return syl
    base, tone = match.group(1), int(match.group(2))
    base = base.replace("u:", "ü").replace("v", "ü")
    if tone == 5:
        return base
    # Tone placement: a/e take it; "ou" -> o; otherwise the last vowel.
    lower = base.lower()
    if "a" in lower:
        target = "a"
    elif "e" in lower:
        target = "e"
    elif "ou" in lower:
        target = "o"
    else:
        target = next((c for c in reversed(lower) if c in _VOWELS), "")
    if not target:
        return base
    marked = _TONE_MARKS[target][tone - 1]
    idx = lower.index(target)
    return base[:idx] + marked + base[idx + 1:]


def _toned(pinyin_block: str) -> str:
    return " ".join(_toned_syllable(s) for s in pinyin_block.split())


def _load() -> dict[str, list[dict]]:
    global _index
    if _index is not None:
        return _index
    index: dict[str, list[dict]] = {}
    if CEDICT_PATH.exists():
        with CEDICT_PATH.open(encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("#") or not line.strip():
                    continue
                match = _LINE_RE.match(line.rstrip("\n"))
                if not match:
                    continue
                trad, simp, pin, defs = match.groups()
                entry = {
                    "trad": trad,
                    "simp": simp,
                    "pinyin": _toned(pin),
                    "defs": [d for d in defs.split("/") if d],
                }
                index.setdefault(simp, []).append(entry)
                if trad != simp:
                    index.setdefault(trad, []).append(entry)
    _index = index
    return index


@lru_cache(maxsize=20000)
def lookup(word: str) -> tuple:
    """Return a tuple of entry dicts for an exact word (cached, hashable)."""
    return tuple(_load().get(word, ()))


def define(word: str) -> dict:
    """Definition payload for a word, with a per-character fallback.

    Shape: {word, entries: [{pinyin, defs[]}], perChar: [{char, pinyin, defs[]}]}.
    """
    entries = [
        {"pinyin": e["pinyin"], "defs": e["defs"]}
        for e in lookup(word)
    ]
    per_char = []
    if len(word) > 1 or not entries:
        for char in word:
            char_entries = lookup(char)
            if char_entries:
                first = char_entries[0]
                per_char.append({
                    "char": char,
                    "pinyin": first["pinyin"],
                    "defs": first["defs"],
                })
    return {"word": word, "entries": entries, "perChar": per_char}


def ready() -> bool:
    return CEDICT_PATH.exists()
