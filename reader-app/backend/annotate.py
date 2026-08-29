"""Offline Chinese annotation: segment text and attach per-character pinyin.

jieba proposes a segmentation and CC-CEDICT — the same dictionary the hover
lookup answers from — gets the final say on it. That reconciliation matters for
two reasons:

* jieba's model is context-sensitive, so it keeps 子时 together in one sentence
  and splits it into 子 + 时 in the next. Anything CC-CEDICT knows as a word is
  made one word here, which is both consistent and always answerable on hover.
* A reading depends on the grouping: split 重新 and per-character pinyin says
  "zhòng xīn" instead of "chóng xīn". Where the dictionary has an unambiguous
  reading for the whole word we use it, because it is right about far more of
  these than character-by-character guessing is.

Text the dictionary has never seen (character names, mostly) still gets broken
down to the words inside it, but its pinyin is taken from pypinyin's reading of
the *whole* original span, so a name keeps whatever phrase-level reading
pypinyin had for it.

Definitions are NOT produced here — they are fetched lazily on hover via the
dictionary endpoint, keeping chapter payloads small.
"""
from __future__ import annotations

import re

import jieba
from pypinyin import Style, pinyin

import dictionary

# CJK Unified Ideographs (incl. common extensions) — what we annotate.
_HAN_RE = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_HAN_ONLY_RE = re.compile(r"^[㐀-䶿一-鿿豈-﫿]+$")

# Bounds on the reconciliation. Merges are capped so a run of short tokens can't
# be swallowed by some rare long headword; the split span is the longest word we
# go looking for inside a token the dictionary doesn't know.
MAX_MERGE_CHARS = 4
MAX_MERGE_TOKENS = 3
MAX_SPLIT_CHARS = 4

# Grammatical particles, which attach to whatever precedes them. CC-CEDICT holds
# rare literary words that collide with those everyday pairings — 中的 is really
# zhòng dì "to hit the target", 到了 is dào liǎo — so a merge is not allowed to
# end on one. Without this, "书中的人" reads zhòng dì instead of zhōng de. A
# particle leading a merge is fine: 的确 and 了解 are ordinary words.
_TRAILING_PARTICLES = frozenset("的了着过地得们吗呢吧啊呀哦嘛")

# Marks of CC-CEDICT's ASCII pinyin that should never survive into a ruby.
_RAW_PINYIN_RE = re.compile(r"[0-9:]")


def _has_han(text: str) -> bool:
    return bool(_HAN_RE.search(text))


def _syllables(word: str) -> list[str]:
    # One reading per character. heteronym=False picks the most common reading
    # (a known, accepted limitation for a reading aid).
    return [
        item[0]
        for item in pinyin(word, style=Style.TONE, heteronym=False, errors="default")
    ]


def _pinyin_for(word: str) -> str:
    return " ".join(_syllables(word))


def _dictionary_reading(word: str) -> str | None:
    """CC-CEDICT's reading for a word, when it is unambiguous and aligns.

    Returns None when the dictionary doesn't know the word, when its entries
    disagree about the reading (single characters usually do, and pypinyin
    weighs context better there), or when the reading doesn't come to one
    syllable per character — the client renders one ruby per character.
    """
    entries = dictionary.lookup(word)
    if not entries:
        return None
    readings = {entry["pinyin"] for entry in entries}
    if len(readings) != 1:
        return None
    reading = next(iter(readings))
    if len(reading.split()) != len(word):
        return None
    # A tone digit or colon left in means the syllable never converted out of
    # CC-CEDICT's ASCII notation; pypinyin is a better answer than raw source.
    if _RAW_PINYIN_RE.search(reading):
        return None
    # CC-CEDICT capitalises proper nouns; the rest of the ruby is lower case.
    return reading.lower()


def _raw_segments(text: str) -> list[str]:
    """jieba's cut, with newlines broken out so paragraphs survive to the client."""
    segments: list[str] = []
    for segment in jieba.cut(text, HMM=True):
        if segment == "":
            continue
        if "\n" in segment:
            segments.extend(piece for piece in re.split(r"(\n)", segment) if piece)
        else:
            segments.append(segment)
    return segments


def _merged(segments: list[str]) -> list[str]:
    """Join neighbouring tokens when together they spell a CC-CEDICT word."""
    out: list[str] = []
    index = 0
    while index < len(segments):
        joined: tuple[str, int] | None = None
        # Longest merge first, so 子 + 时 beats leaving 子 alone.
        for end in range(min(index + MAX_MERGE_TOKENS, len(segments)), index + 1, -1):
            candidate = "".join(segments[index:end])
            if len(candidate) > MAX_MERGE_CHARS:
                continue
            if not _HAN_ONLY_RE.match(candidate):
                continue
            if segments[end - 1] in _TRAILING_PARTICLES:
                continue
            if dictionary.lookup(candidate):
                joined = (candidate, end)
                break
        if joined:
            out.append(joined[0])
            index = joined[1]
        else:
            out.append(segments[index])
            index += 1
    return out


def _dictionary_spans(word: str) -> list[str]:
    """Break a token CC-CEDICT doesn't know into the longest words it does.

    A leftover character it can't place comes back on its own, which is what
    happens to the characters of a name.
    """
    if len(word) < 2 or dictionary.lookup(word):
        return [word]
    spans: list[str] = []
    index = 0
    while index < len(word):
        match = 0
        for end in range(min(index + MAX_SPLIT_CHARS, len(word)), index + 1, -1):
            if dictionary.lookup(word[index:end]):
                match = end
                break
        if match:
            spans.append(word[index:match])
            index = match
        else:
            spans.append(word[index])
            index += 1
    return spans


def tokenize(text: str) -> list[dict]:
    """Return a flat token list: [{"t": surface, "py": "pin yin" | None}, ...]."""
    tokens: list[dict] = []
    for segment in _merged(_raw_segments(text)):
        if not _has_han(segment):
            tokens.append({"t": segment, "py": None})
            continue
        if not _HAN_ONLY_RE.match(segment):
            # Mixed Han and latin/digits: no syllable-per-character alignment to
            # slice, so leave the span whole.
            tokens.append({"t": segment, "py": _pinyin_for(segment)})
            continue
        # Read the whole span once, then hand each piece its own syllables, so
        # splitting a name never costs pypinyin's reading of it.
        syllables = _syllables(segment)
        at = 0
        for piece in _dictionary_spans(segment):
            reading = _dictionary_reading(piece)
            if reading is None:
                reading = " ".join(syllables[at:at + len(piece)])
            tokens.append({"t": piece, "py": reading})
            at += len(piece)
    return tokens
