#!/usr/bin/env python3
"""Strip hidden QQ-group watermarks from a pirated novel .txt.

Some txt rips (the 南锦 group's, for one) advertise their QQ groups by hiding
the group numbers inside the prose, a few thousand times per book. Each
insertion spells one 9-10 digit number with stand-ins for the digits, so plain
search misses it:

    然衣弍零珊尔玲VII斯玐而   ->  然 + 1 2 0 3 2 0 7 4 8 + 而   ->  然而

The stand-ins are circled/bracketed numerals (⑵ ⒏ ③ ⑺), Roman numerals and
pinyin (II, IX, jiu), zero look-alikes (〇 ⊙ O), sound-alike characters
(漆/琦/企 for 7, 伞/衫 for 3, 扒/吧 for 8, 霖/澪/龄 for 0), and 群 ("group")
variants or the group's name on either side (裙聊, qun, 峮, 南锦).

Removal only takes out a run that spells a *whole* watermark number, which is
what keeps real text safe: prose never spells out a specific 9-10 digit
number in digit sound-alikes, and the fixed length gives exact boundaries
(下一⒉久龄捂⒊坝琦亦⒊刻 -> 下一刻, keeping the real 一). The numbers come from
the ad block (【…群…】——517880761) plus any that the text itself repeats.

The same numbers are also hidden *vertically*: one digit token tacked onto the
end of each of ~10 consecutive lines (…上个药。”易 / …”⑦ / …道，s / …头，i /
…). Those are found by reading the last character of every line as one string
and stripping the matched lines' final character.

Lines that held a watermark also had their punctuation switched to ASCII, so
those get ，？！：； back. The ad block lines themselves are dropped too.

    python scripts/strip_watermarks.py book.txt            # dry run: report only
    python scripts/strip_watermarks.py book.txt --write    # clean in place
    python scripts/strip_watermarks.py book.txt --numbers 1017459498

--write keeps the original next to the file as a hidden .<name>.bak (never
overwritten, so a second run can't replace the true original). Pass
--no-backup when the file is already backed up elsewhere, e.g. inside the
uploads/ git backup, where a .bak would just be a second copy.
"""
from __future__ import annotations

import argparse
import collections
import re
import sys
from pathlib import Path

DIGIT_CHARS = {
    "0": "零〇⊙Oo0⓪霖林玲龄澪陵令灵凌菱铃伶泠囹磷淋另邻",
    "1": "一壹亿衣易亦依医伊益意艺异忆弈奕逸毅1①⑴⒈乙仪吆",
    "2": "二贰貳弍児尔耳洱迩而饵儿②⑵⒉陾侕",
    "3": "三叁伞傘散衫珊彡杉叄山姗删③⑶⒊厁氵",
    "4": "四肆泗似斯死私俬寺嗣思司丝事是世咝逝师師④⑷⒋",
    "5": "五伍午呜武捂污务物舞儛雾悟屋吾无巫吴焐⑤⑸⒌",
    "6": "六陆溜瘤榴刘流留柳琉锍硫鹨⑥⑹⒍",
    "7": "七柒漆奇琦气企齐祁旗起期其棋骑器弃泣鳍妻⑦⑺⒎",
    "8": "八捌扒吧巴玐岜拔疤坝罢把芭爸紦⑧⑻⒏",
    "9": "九玖久酒韭咎揪鸠疚就究旧臼救舅蹴氿⑨⑼⒐",
}
# Multi-character tokens. Roman numerals are listed long-first so VIII wins
# over VII + I.
DIGIT_WORDS = {
    "0": ["ling"], "1": ["yi", "I"], "2": ["er", "II"], "3": ["san", "III"],
    "4": ["si", "IV"], "5": ["wu", "V"], "6": ["liu", "VI"], "7": ["qi", "VII"],
    "8": ["ba", "VIII"], "9": ["jiu", "IX"],
}
# Bracketed numerals, in any mix of half/full-width brackets: (八) （八） （八) (一 )
for _digit, _han in enumerate("零一二三四五六七八九"):
    DIGIT_WORDS[str(_digit)] += [
        f"{open_}{_han}{gap}{close}"
        for open_ in "(（" for close in ")）" for gap in ("", " ")
    ]

CHAR_DIGIT = {ch: d for d, chars in DIGIT_CHARS.items() for ch in chars}
DIGIT_ALPHABET = set(CHAR_DIGIT) | set("IVXilngjuswaqbe()（）")

# 群 ("group") variants, chat suffixes and the group's name, which insertions
# wear on either side of the number.
_TAG = r"(?:南锦|qun|[群裙峮裠羣囷箘麇踆逡宭][聊撩]?)"
PREFIX_TAG = re.compile(rf"{_TAG}+")
SUFFIX_TAG = re.compile(rf"{_TAG}*")

# A digit the map doesn't know yet may stand in once per number, but only as a
# CJK character, never punctuation or whitespace.
MAX_UNKNOWN = 1
_UNKNOWN_OK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
# Some insertions also drop one filler symbol between digits: "517 880761",
# "5*17880761", "517-880761", "120$320748", "517…880761", "5178【】80761".
MAX_FILLER = 2


def _is_filler(ch: str) -> bool:
    """Any one symbol: not a letter, digit or CJK character."""
    return not ch.isalnum() and not _UNKNOWN_OK.match(ch) and ch != "\n"

# Decorations that only ever come from the watermark; a cleaned line still
# holding one is reported as a leftover.
MARKERS = re.compile(r"[\u2460-\u24ff⊙〇]")

# The ad block: 本书由【南锦】整理…, 【南锦免费外群1号】——517880761, 本群免费提取…
AD_LINE = re.compile(r"【[^】]*群[^】]*】\s*——\s*\d{6,11}|本书由【[^】]+】整理|【[^】]+】提醒您|本群免费提取")
AD_NUMBER = re.compile(r"【[^】]*群[^】]*】\s*——\s*(\d{6,11})")

_ASCII_PUNCT = str.maketrans({",": "，", "?": "？", "!": "！", ":": "：", ";": "；"})


def _tokens_at(text: str, i: int):
    """Every (digit or None, end) token that can start at text[i]."""
    for digit, words in DIGIT_WORDS.items():
        for word in words:
            if text.startswith(word, i):
                yield digit, i + len(word)
    if i < len(text):
        ch = text[i]
        if ch in CHAR_DIGIT:
            yield CHAR_DIGIT[ch], i + 1
        elif _UNKNOWN_OK.match(ch):
            yield None, i + 1


def _spells(text: str, i: int, number: str) -> int | None:
    """End index if text[i:] spells `number` (longest match), else None."""
    best: int | None = None

    def walk(pos: int, k: int, unknown: int, filler: int) -> None:
        nonlocal best
        if k == len(number):
            best = pos if best is None else max(best, pos)
            return
        if 0 < k and filler < MAX_FILLER and pos < len(text) and _is_filler(text[pos]):
            walk(pos + 1, k, unknown, filler + 1)
        for digit, end in _tokens_at(text, pos):
            if digit == number[k]:
                walk(end, k + 1, unknown, filler)
            elif digit is None and unknown < MAX_UNKNOWN:
                walk(end, k + 1, unknown + 1, filler)

    walk(i, 0, 0, 0)
    return best


def _digit_run(text: str, i: int) -> tuple[str, int]:
    """The longest run of known digit tokens from i, as (digits, end)."""
    digits, pos = "", i
    while True:
        known = [(d, end) for d, end in _tokens_at(text, pos) if d is not None]
        if not known:
            return digits, pos
        digit, pos = max(known, key=lambda option: option[1])
        digits += digit


def _candidate(line: str) -> bool:
    """Cheap pre-filter: a watermark needs nine-odd digit tokens on the line."""
    return sum(ch in DIGIT_ALPHABET for ch in line) >= 7


def discover_numbers(text: str, known: list[str], min_count: int = 8) -> list[str]:
    """Numbers the text keeps spelling out, beyond the ones the ad block lists.

    Runs of digit tokens can pick up a real digit-ish character on either side,
    so a known number also shows up as one-digit-longer windows. Anything that
    is a known number plus one edge digit is ignored; what's left and repeats
    a lot is a number the ad block didn't mention.
    """
    seen: collections.Counter[str] = collections.Counter()
    for line in text.split("\n"):
        if not _candidate(line):
            continue
        i = 0
        while i < len(line):
            digits, end = _digit_run(line, i)
            if len(digits) >= 9:
                for size in (9, 10):
                    for k in range(len(digits) - size + 1):
                        seen[digits[k:k + size]] += 1
                i = end
            else:
                i += 1

    def explained(number: str, by: list[str]) -> bool:
        return any(
            number != other and (number[1:] == other or number[:-1] == other or other in number or number in other)
            for other in by
        )

    # 10-digit candidates first: a 10-digit number's two 9-digit halves count
    # up just as often, and taking a half would leave its last digit behind.
    found: list[str] = []
    ranked = sorted(
        (item for item in seen.items() if item[1] >= min_count),
        key=lambda item: (-len(item[0]), -item[1]),
    )
    for number, _count in ranked:
        if number in known or explained(number, known + found):
            continue
        found.append(number)
    return found


def find_spans(text: str, numbers: list[str]) -> list[tuple[int, int]]:
    """(start, end) of every watermark in text, tags included."""
    spans: list[tuple[int, int]] = []
    i = 0
    while i < len(text):
        starts = [i]
        tag = PREFIX_TAG.match(text, i)
        if tag:
            starts.append(tag.end())
        end = None
        for start in starts:
            for number in numbers:
                stop = _spells(text, start, number)
                if stop is not None and (end is None or stop > end):
                    end = stop
        if end is None:
            i += 1
            continue
        end = SUFFIX_TAG.match(text, end).end()
        spans.append((i, end))
        i = end
    return spans


def strip_line(line: str, numbers: list[str]) -> tuple[str, int]:
    """Remove every watermark from one line. Returns (line, insertions removed)."""
    if not _candidate(line):
        return line, 0
    spans = find_spans(line, numbers)
    if not spans:
        return line, 0
    out, last = [], 0
    for start, end in spans:
        out.append(line[last:start])
        last = end
    out.append(line[last:])
    return "".join(out).translate(_ASCII_PUNCT), len(spans)


def strip_line_ends(lines: list[str], numbers: list[str]) -> int:
    """Remove numbers spelled down the last characters of consecutive lines.

    Edits `lines` in place and returns how many numbers were removed.
    """
    rows = [n for n, line in enumerate(lines) if line.rstrip()]
    tails = "".join(lines[n].rstrip()[-1] for n in rows)
    spans = find_spans(tails, numbers)
    for start, end in spans:
        for n in rows[start:end]:
            lines[n] = lines[n].rstrip()[:-1]
    return len(spans)


def clean(text: str, extra_numbers: list[str] | None = None) -> dict:
    """Strip ad lines and watermarks. Returns the cleaned text plus a report."""
    known = list(dict.fromkeys(AD_NUMBER.findall(text) + (extra_numbers or [])))
    numbers = known + discover_numbers(text, known)
    # Longest first, so a 10-digit number isn't half-eaten by a 9-digit one.
    numbers.sort(key=len, reverse=True)

    lines = text.split("\n")
    kept: list[str] = []
    ad_lines = insertions = 0
    changed: list[tuple[str, str]] = []
    for line in lines:
        if AD_LINE.search(line):
            ad_lines += 1
            continue
        new, removed = strip_line(line, numbers)
        if removed:
            insertions += removed
            changed.append((line, new))
        kept.append(new)
    vertical = strip_line_ends(kept, numbers)
    leftovers = [line for line in kept if MARKERS.search(line)]
    return {
        "text": "\n".join(kept),
        "numbers": numbers,
        "ad_lines": ad_lines,
        "insertions": insertions,
        "vertical": vertical,
        "changed": changed,
        "leftovers": leftovers,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("file", type=Path)
    parser.add_argument("--write", action="store_true", help="clean the file in place (keeps a .bak)")
    parser.add_argument("--no-backup", action="store_true", help="with --write, skip the .bak copy")
    parser.add_argument("--numbers", nargs="*", default=[], help="extra watermark numbers to strip")
    parser.add_argument("--samples", type=int, default=8, help="before/after lines to show")
    args = parser.parse_args(argv)

    raw = args.file.read_bytes()
    text = raw.decode("utf-8-sig")
    report = clean(text, args.numbers)

    print(f"numbers:     {', '.join(report['numbers']) or '(none found)'}")
    print(f"ad lines:    {report['ad_lines']}")
    print(f"watermarks:  {report['insertions']} removed from {len(report['changed'])} lines")
    print(f"line-end:    {report['vertical']} numbers removed from line endings")
    print(f"leftovers:   {len(report['leftovers'])} lines still hold watermark symbols")
    step = max(1, len(report["changed"]) // max(1, args.samples))
    for before, after in report["changed"][::step][: args.samples]:
        print(f"\n  - {before.strip()}\n  + {after.strip()}")
    for line in report["leftovers"][:20]:
        print(f"\n  ? {line.strip()}")

    if not args.write:
        print("\n(dry run; pass --write to clean the file)")
        return 0
    if not (report["insertions"] or report["vertical"] or report["ad_lines"]):
        print("\nnothing to clean")
        return 0
    note = "no backup kept"
    if not args.no_backup:
        backup = args.file.with_name(f".{args.file.name}.bak")
        if not backup.exists():
            backup.write_bytes(raw)
        note = f"original kept at {backup.name}"
    args.file.write_text(report["text"], encoding="utf-8")
    print(f"\nwrote {args.file} ({note})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
