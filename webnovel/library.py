from __future__ import annotations

import json
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from curl_cffi import requests as cffi_requests

from recsys.store import NovelRecord, load_all
from scraper import fetch, parse_chapter_page, parse_landing, split_into_chapters
from scripts.repo_paths import LIBRARY_DIR


@dataclass
class Chapter:
    title: str
    body: str

    def text(self) -> str:
        return f"{self.title}\n\n{self.body}".strip()


FALLBACK_BLOCK_CHARS = 2_000
MAX_HEADING_LENGTH = 100
# Block size used when cutting up a chapter the *numbering* proved is really
# several fused chapters. Length alone never triggers a split: a real chapter is
# sometimes very long, and splitting it would be wrong.
MAX_CHAPTER_CHARS = 3_000
# A jump bigger than this is treated as noise (a year in a title, a volume
# restart) rather than that many missing chapters.
MAX_NUMBER_GAP = 500
# How much longer than the book's typical chapter a body must be before a
# numbering gap is believed. These sources skip a chapter number often enough
# that the gap alone would chop up plenty of perfectly normal chapters.
FUSED_LENGTH_FACTOR = 1.5
DEFAULT_HEADING_PATTERNS_PATH = Path(__file__).with_name("chapter_heading_patterns.json")


def default_heading_patterns() -> list[str]:
    """Load the built-in detector rules from editable JSON, never Python code."""
    try:
        data = json.loads(DEFAULT_HEADING_PATTERNS_PATH.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    if not isinstance(data, list):
        return []
    return [
        str(item["pattern"])
        for item in data
        if isinstance(item, dict) and isinstance(item.get("pattern"), str)
    ]


def _text_blocks(text: str, limit: int = FALLBACK_BLOCK_CHARS) -> list[str]:
    """Split text into bounded blocks, preferring paragraph/line boundaries."""
    remaining = text.strip()
    blocks: list[str] = []
    while remaining:
        if len(remaining) <= limit:
            blocks.append(remaining)
            break
        # Prefer a paragraph break, then any line break, without producing a
        # tiny block just because the source has an early newline.
        floor = max(1, limit // 2)
        cut = remaining.rfind("\n\n", floor, limit + 1)
        separator = 2
        if cut < floor:
            cut = remaining.rfind("\n", floor, limit + 1)
            separator = 1
        if cut < floor:
            cut = limit
            separator = 0
        block = remaining[:cut].strip()
        if block:
            blocks.append(block)
        remaining = remaining[cut + separator :].lstrip()
    return blocks


_CN_DIGITS = {
    "〇": 0, "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
    "五": 5, "六": 6, "七": 7, "八": 8, "九": 9,
}
_CN_UNITS = {"十": 10, "百": 100, "千": 1000}
_CN_NUMERAL = "".join(_CN_DIGITS) + "".join(_CN_UNITS)
# 第N章 / 第N节 / 第N回 …, with either Arabic or Chinese numerals.
_ORDINAL_RE = re.compile(rf"^第\s*([0-9]+|[{_CN_NUMERAL}]+)\s*[章节節回卷篇集]")
_ENGLISH_ORDINAL_RE = re.compile(r"^(?:chapter|ch\.?|part)\s*([0-9]+)\b", re.IGNORECASE)
_BARE_ORDINAL_RE = re.compile(r"^([0-9]+)")


def _cn_number(text: str) -> int | None:
    """Parse a Chinese numeral: 一, 十五, 二十三, 一百零三."""
    total = 0
    current = 0
    seen = False
    for char in text:
        if char in _CN_DIGITS:
            current = _CN_DIGITS[char]
            seen = True
        elif char in _CN_UNITS:
            # A leading unit is implicitly one: 十五 is 15, not 5.
            total += (current or 1) * _CN_UNITS[char]
            current = 0
            seen = True
        else:
            return None
    return total + current if seen else None


def chapter_number(title: str) -> int | None:
    """Return the ordinal a chapter heading claims, or None if it has none."""
    text = title.strip()
    match = _ORDINAL_RE.match(text)
    if match:
        raw = match.group(1)
        return int(raw) if raw.isdigit() else _cn_number(raw)
    for pattern in (_ENGLISH_ORDINAL_RE, _BARE_ORDINAL_RE):
        match = pattern.match(text)
        if match:
            return int(match.group(1))
    return None


def _fused_indices(chapters: list[Chapter]) -> set[int]:
    """Find chapters whose body actually holds several chapters' worth of text.

    The heading detector sometimes misses a run of headings, and every chapter in
    that run then ends up inside the body of whatever came before it. The give-
    away is the numbering: if 第一章 is followed by 第五章, the three chapters in
    between are sitting inside 第一章. Likewise, if the first heading found is
    第16章, chapters 1-15 are in the front matter above it.

    Numbering is the signal rather than length, because a real chapter is
    sometimes genuinely very long and must not be chopped up.

    A gap on its own isn't proof, though: these sources skip a chapter number
    fairly often without any text actually being missing. So a flagged chapter
    also has to be far longer than this book's typical chapter before we believe
    it holds several. Length can only ever veto a split here, never cause one.
    """
    numbers = [chapter_number(chapter.title) for chapter in chapters]
    typical = _typical_body(chapters)
    fused: set[int] = set()
    previous: int | None = None
    for index, number in enumerate(numbers):
        if number is None:
            continue
        if previous is None:
            # Nothing numbered before this one. If it doesn't start the book,
            # the missing chapters are in the unnumbered text above it.
            if 1 < number <= MAX_NUMBER_GAP:
                fused.update(
                    early for early in range(index)
                    if _holds_multiple(chapters[early], typical)
                )
        else:
            gap = number - numbers[previous]
            if 1 < gap <= MAX_NUMBER_GAP and _holds_multiple(chapters[previous], typical):
                fused.add(previous)
        previous = index
    return fused


def _typical_body(chapters: list[Chapter]) -> int:
    """Median body length, the yardstick for "normal chapter" in this book."""
    lengths = sorted(len(chapter.body) for chapter in chapters)
    return lengths[len(lengths) // 2] if lengths else 0


def _holds_multiple(chapter: Chapter, typical: int) -> bool:
    """Is this body long enough to plausibly hold more than one chapter?

    Not scaled by how many chapters are missing: the skipped ones are often
    short, so demanding the full combined length would miss real fusions. All
    this has to rule out is a normal-sized chapter whose number skipped a beat.
    """
    if len(chapter.body) <= MAX_CHAPTER_CHARS:
        return False
    return len(chapter.body) >= max(typical, MAX_CHAPTER_CHARS) * FUSED_LENGTH_FACTOR


def _split_fused(chapters: list[Chapter], limit: int = MAX_CHAPTER_CHARS) -> list[Chapter]:
    """Cut fused chapters into bounded parts, leaving every real chapter alone.

    A fused block is unreadable as one page: the reader has to render the whole
    run at once, which blows past its rich-text budget, so pinyin and the
    clickable dictionary silently drop out. Bounded parts keep both working.
    """
    fused = _fused_indices(chapters)
    if not fused:
        return chapters
    split: list[Chapter] = []
    for index, chapter in enumerate(chapters):
        if index not in fused or len(chapter.body) <= limit:
            split.append(chapter)
            continue
        blocks = _text_blocks(chapter.body, limit)
        split.extend(
            Chapter(chapter.title if order == 1 else f"{chapter.title} ({order})", block)
            for order, block in enumerate(blocks, 1)
        )
    return split


def fallback_chapters(
    text: str,
    limit: int = FALLBACK_BLOCK_CHARS,
) -> list[Chapter]:
    """Make virtual chapters when a file has no reliable chapter headings."""
    blocks = _text_blocks(text, limit)
    width = max(2, len(str(len(blocks))))
    return [
        Chapter(f"Part {index:0{width}d}", block)
        for index, block in enumerate(blocks, 1)
    ]


def chapters_from_patterns(
    text: str,
    patterns: list[str],
    *,
    include_preamble: bool = True,
) -> list[Chapter]:
    """Split normalized text on complete lines matched by a book-specific regex."""
    heading_res = [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    heads = [
        index
        for index, line in enumerate(lines)
        if 0 < len(line.strip()) <= MAX_HEADING_LENGTH
        and any(heading_re.search(line.strip()) is not None for heading_re in heading_res)
    ]
    if len(heads) < 2:
        return []
    chapters: list[Chapter] = []
    preamble = "\n".join(lines[: heads[0]]).strip()
    if preamble and include_preamble:
        chapters.append(Chapter("Front matter", preamble))
    for order, start in enumerate(heads):
        end = heads[order + 1] if order + 1 < len(heads) else len(lines)
        title = lines[start].strip()
        content = "\n".join(lines[start + 1 : end]).strip()
        chapters.append(Chapter(title, content))
    # Detected chapters keep their own boundaries — splitting on length alone
    # would create fake duplicate TOC entries and arbitrary mid-chapter breaks
    # in any book with a long chapter. Only chapters the numbering proves are
    # several fused chapters get cut up; see _split_fused().
    return _split_fused(chapters)


def chapters_from_pattern(
    text: str,
    pattern: str,
    *,
    include_preamble: bool = True,
) -> list[Chapter]:
    return chapters_from_patterns(
        text,
        [pattern],
        include_preamble=include_preamble,
    )


def local_chapters(
    path: Path,
    heading_pattern: str | None = None,
    heading_patterns: list[str] | None = None,
) -> list[Chapter]:
    text = path.read_text(encoding="utf-8", errors="replace")
    source_index = text.find("\n来源：")
    body = text[source_index + 1:] if source_index >= 0 else text
    divider = "═" * 10
    # Generated downloads place visual divider rows around chapters. They are
    # storage framing, not reader content.
    custom_body = "\n".join(
        line for line in body.splitlines() if divider not in line
    )
    patterns = [heading_pattern] if heading_pattern else (
        heading_patterns if heading_patterns is not None else default_heading_patterns()
    )
    detected_chapters = chapters_from_patterns(
        custom_body,
        patterns,
        include_preamble=False,
    )
    if detected_chapters:
        return detected_chapters
    parts = body.split("\n")
    pages: list[str] = []
    collecting = False
    current: list[str] = []
    for line in parts:
        if divider in line:
            collecting = True
            if current:
                pages.append("\n".join(current))
                current = []
            continue
        if collecting:
            current.append(line)
    if current:
        pages.append("\n".join(current))

    if pages:
        return fallback_chapters("\n\n".join(page for page in pages if page.strip()))

    # Chapterless files have no divider markers; exclude the generated preamble.
    source_line = next(
        (index for index, line in enumerate(parts) if line.startswith("来源：")),
        -1,
    )
    raw = "\n".join(parts[source_line + 1:]).strip()
    return fallback_chapters(raw)


def local_synopsis(path: Path) -> Chapter | None:
    """Return the text between the generated preamble and first chapter page."""
    text = path.read_text(encoding="utf-8", errors="replace")
    divider = "═" * 10
    source_index = text.find("\n来源：")
    if source_index < 0:
        source_index = text.find("来源：")
    if source_index < 0:
        return None
    start = text.find("\n", source_index + 1)
    if start < 0:
        return None
    end = text.find(divider, start)
    if end < 0:
        # Without a generated divider there is no reliable synopsis boundary.
        # Treating the rest of the file as synopsis bypasses chapter windowing
        # and can send an entire novel to the frontend.
        return None
    raw = text[start:end if end >= 0 else None].strip()
    if not raw:
        return None
    lines = [line.rstrip() for line in raw.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    body = "\n".join(lines).strip()
    return Chapter("Synopsis", body) if body else None


# ── Raw file reading (the Library file explorer) ─────────────────────────────
# These handle arbitrary .txt files that aren't in the metadata store: the
# Windows dump the user browses (mixed encodings, English + Chinese, no 52shuku
# preamble). local_chapters/local_synopsis above stay for downloaded 52shuku
# files, which are always UTF-8 with the generated preamble + "═" dividers.

def decode_text(raw: bytes) -> str:
    """Decode bytes, trying the encodings these files actually use.

    Downloaded 52shuku files are UTF-8, but the browsed/uploaded Windows library
    has GBK/GB2312 Chinese files too. Try UTF-8 (with/without BOM) first, then
    GB18030 (a superset of GBK/GB2312), then give up and replace bad bytes.
    """
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def read_text_smart(path: Path) -> str:
    return decode_text(path.read_bytes())


def chapters_from_text(
    text: str,
    heading_pattern: str | None = None,
    heading_patterns: list[str] | None = None,
) -> list[Chapter]:
    """Split raw novel text into chapters by heading lines (Chinese 第N章 or
    English Chapter N), falling back to bounded virtual ``Part`` chapters."""
    patterns = [heading_pattern] if heading_pattern else (
        heading_patterns if heading_patterns is not None else default_heading_patterns()
    )
    chapters = chapters_from_patterns(text, patterns)
    return chapters if chapters else fallback_chapters(text)


def raw_chapters(
    path: Path,
    heading_pattern: str | None = None,
    heading_patterns: list[str] | None = None,
) -> list[Chapter]:
    """Split a raw .txt file into chapters. See chapters_from_text."""
    return chapters_from_text(read_text_smart(path), heading_pattern, heading_patterns)


def detect_language(sample: str) -> str:
    """Return "zh" or "en" from a text sample by Han vs Latin letter counts."""
    han = sum(1 for ch in sample if "一" <= ch <= "鿿")
    latin = sum(1 for ch in sample if ch.isascii() and ch.isalpha())
    return "en" if latin > han else "zh"


def live_first_chapter(url: str, page_limit: int = 10) -> tuple[str, bool]:
    session = cffi_requests.Session()
    try:
        landing = fetch(session, url)
        meta = parse_landing(landing.text, url)
        pages: list[str] = []
        truncated = False
        for page_url in meta.chapter_urls[:page_limit]:
            pages.append(parse_chapter_page(fetch(session, page_url).text).text)
            chapters = split_into_chapters(pages)
            numbered = [(header, body) for header, body in chapters if header]
            if len(numbered) >= 2:
                header, body = numbered[0]
                return f"{header}\n\n{body}".strip(), False
        truncated = len(meta.chapter_urls) > page_limit
        chapters = split_into_chapters(pages)
        numbered = [(header, body) for header, body in chapters if header]
        if numbered:
            header, body = numbered[0]
            return f"{header}\n\n{body}".strip(), truncated
        return "\n\n".join(page for page in pages if page.strip()), truncated
    finally:
        session.close()


def clipboard_copy(text: str) -> str:
    commands = [
        (["clip.exe"], text.encode("utf-16le")),
        (["wl-copy"], text.encode("utf-8")),
        (["xclip", "-selection", "clipboard"], text.encode("utf-8")),
        (["xsel", "--clipboard", "--input"], text.encode("utf-8")),
        (["pbcopy"], text.encode("utf-8")),
    ]
    for command, payload in commands:
        if shutil.which(command[0]):
            subprocess.run(command, input=payload, check=True)
            return command[0]
    raise RuntimeError("No clipboard command found (clip.exe, wl-copy, xclip, xsel, pbcopy)")


def print_record(record: NovelRecord) -> None:
    print(f"《{record.title}》 - {record.author or '?'}")
    print(f"Category: {record.category}  Status: {record.status or '?'}")
    print(f"Uploaded: {record.upload_date or '?'}")
    if record.chapter_count is not None:
        print(f"Chapters: {record.chapter_count}")
    elif record.page_count is not None:
        print(f"Reading pages: {record.page_count}")
    if record.tags:
        print(f"Tags: {' / '.join(record.tags)}")
    print(f"Source: {record.url}")
    print(f"Local file: {record.file or '(metadata only)'}")
    if record.synopsis:
        print(f"\n{record.synopsis}")


def list_library(
    *,
    query: str = "",
    categories: set[str] | None = None,
    downloaded_only: bool = False,
    limit: int = 50,
) -> list[NovelRecord]:
    records = list(load_all().values())
    if downloaded_only:
        records = [record for record in records if record.downloaded]
    if categories:
        records = [record for record in records if record.category in categories]
    if query:
        needle = query.casefold()
        records = [
            record
            for record in records
            if needle in record.title.casefold()
            or needle in record.author.casefold()
            or any(needle in tag.casefold() for tag in record.tags)
        ]
    records.sort(key=lambda record: (record.upload_date, record.title), reverse=True)
    return records[:limit]


def local_path(record: NovelRecord) -> Path | None:
    if not record.file:
        return None
    path = Path(record.file)
    return path if path.is_absolute() else LIBRARY_DIR / path


def interactive_reader(record: NovelRecord, chapters: list[Chapter]) -> int:
    if not chapters:
        print("No readable chapters found.")
        return 1
    position = 0
    while True:
        chapter = chapters[position]
        print(f"\n[{position + 1}/{len(chapters)}] {chapter.text()}\n")
        try:
            command = input("reader [n/p/g N/c N/a/q]: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if command in {"q", "quit", "exit"}:
            return 0
        if command in {"", "n", "next"}:
            position = min(position + 1, len(chapters) - 1)
        elif command in {"p", "prev", "previous"}:
            position = max(position - 1, 0)
        elif command.startswith(("g ", "goto ")):
            try:
                number = int(command.split()[-1])
                position = max(0, min(number - 1, len(chapters) - 1))
            except ValueError:
                print("Use: g <chapter-number>")
        elif command.startswith(("c ", "copy ")):
            try:
                count = max(1, int(command.split()[-1]))
                payload = "\n\n".join(
                    item.text() for item in chapters[position:position + count]
                )
                backend = clipboard_copy(payload)
                print(f"Copied {min(count, len(chapters) - position)} chapter(s) via {backend}.")
            except (ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
                print(f"Copy failed: {exc}")
        elif command in {"a", "all"}:
            print("\n\n".join(item.text() for item in chapters))
        else:
            print("Commands: next, previous, goto N, copy N, all, quit")
    return 0
