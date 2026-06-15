from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from curl_cffi import requests as cffi_requests

from recsys.store import NovelRecord, load_all
from scraper import CHAPTER_RE, fetch, parse_chapter_page, parse_landing, split_into_chapters
from scripts.repo_paths import ROOT_DIR


@dataclass
class Chapter:
    title: str
    body: str

    def text(self) -> str:
        return f"{self.title}\n\n{self.body}".strip()


def local_chapters(path: Path) -> list[Chapter]:
    text = path.read_text(encoding="utf-8", errors="replace")
    source_index = text.find("\n来源：")
    body = text[source_index + 1:] if source_index >= 0 else text
    divider = "═" * 10
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

    split = split_into_chapters(pages)
    chapters = [
        Chapter(header or f"Section {index}", content)
        for index, (header, content) in enumerate(split, 1)
        if header or content.strip()
    ]
    if chapters:
        return chapters

    # Chapterless files have no divider markers; exclude the generated preamble.
    source_line = next(
        (index for index, line in enumerate(parts) if line.startswith("来源：")),
        -1,
    )
    raw = "\n".join(parts[source_line + 1:]).strip()
    return [Chapter("Full text", raw)] if raw else []


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
    return path if path.is_absolute() else ROOT_DIR / path


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
