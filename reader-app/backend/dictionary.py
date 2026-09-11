"""CC-CEDICT lookup (offline), and the traditional characters it can identify.

Parses the vendored ``reader-app/data/cedict_ts.u8`` once into
``{simplified: [entry, ...]}`` and answers per-word lookups (cached). On a miss
for a multi-character word, the caller can fall back to per-character lookups so
every Han token yields something.

The same parse builds a traditional-to-simplified character table, because some
downloads come with stray traditional characters mixed into otherwise
simplified text. See ``traditional_map`` for why that table is narrower than the
full set of pairs CC-CEDICT lists.

CC-CEDICT is CC BY-SA 4.0 — see ``reader-app/data/ATTRIBUTION.md``.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
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
_traditional: dict[str, str] | None = None
_traditional_re: "re.Pattern[str] | None" = None


def _toned_syllable(syl: str) -> str:
    # CC-CEDICT spells ü as "u:" (nu:3 = nǚ), so the colon has to be allowed
    # through the match or the syllable comes out raw, tone digit and all.
    match = re.match(r"^([a-zü:]+)([1-5])$", syl, re.IGNORECASE)
    if not match:
        return syl
    base, tone = match.group(1), int(match.group(2))
    base = base.replace("u:", "ü").replace("U:", "ü").replace("v", "ü")
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
    global _index, _traditional
    if _index is not None:
        return _index
    index: dict[str, list[dict]] = {}
    # Every character CC-CEDICT ever writes in a simplified headword. A
    # character in here is ordinary simplified text and must never be rewritten,
    # however often it also turns up on the traditional side of some pair.
    simplified_chars: set[str] = set()
    # Character-for-character votes from headword pairs of equal length.
    votes: dict[str, Counter] = defaultdict(Counter)
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
                simplified_chars.update(simp)
                if len(trad) == len(simp):
                    for old, new in zip(trad, simp):
                        if old != new:
                            votes[old][new] += 1
    _index = index
    _traditional = {
        old: counts.most_common(1)[0][0]
        for old, counts in votes.items()
        if old not in simplified_chars
    }
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


def traditional_map() -> dict[str, str]:
    """Characters that are only ever traditional, and what they simplify to.

    Deliberately narrower than every pair CC-CEDICT lists. Plenty of ordinary
    simplified characters also sit on the traditional side of some unrelated
    pair: 么, 宁, 份, 座, 覆, 著 and 沈 all do. Rewriting those would wreck far
    more text than it fixed (么 alone runs to thousands of hits in a novel), so
    a character is only mapped when CC-CEDICT never writes it in simplified
    text. What that leaves out is the genuinely context-dependent cases, 著 for
    着 among them, which cannot be settled one character at a time anyway.

    Where a traditional character has several simplified forms the most attested
    one wins; the alternatives are rare variants (餘 gives 余, not 馀).
    """
    _load()
    return _traditional or {}


def to_simplified(text: str) -> tuple[str, int]:
    """Rewrite stray traditional characters, with how many changed.

    One character in, one character out, so offsets into the text are untouched.
    """
    global _traditional_re
    table = traditional_map()
    if not table:
        return text, 0
    if _traditional_re is None:
        _traditional_re = re.compile(f"[{re.escape(''.join(table))}]")
    # subn converts and counts in one scan, and only pays Python per character
    # actually replaced rather than per character in the book.
    return _traditional_re.subn(lambda hit: table[hit.group()], text)


def ready() -> bool:
    return CEDICT_PATH.exists()
