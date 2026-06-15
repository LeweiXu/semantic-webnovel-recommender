#!/usr/bin/env python3
"""Watch the scraper output directory and report newly added novels.

The initial summary uses file metadata only. New files are reported after their
size and modification time remain unchanged for one full polling interval, so
the watcher does not inspect a file while the scraper is still writing it.

Usage:
    python3 scripts/watch_output.py
    python3 scripts/watch_output.py --interval 1
    python3 scripts/watch_output.py --output /path/to/output
    python3 scripts/watch_output.py --no-log-file
"""
from __future__ import annotations

import argparse
import logging
import re
import signal
import sys
import threading
from dataclasses import dataclass
from pathlib import Path

from repo_paths import LOGS_DIR, OUTPUT_DIR

DEFAULT_LOG = LOGS_DIR / "output_watcher.log"
FIELD_RE = re.compile(r"^(标题|章节数|完整性|来源)：(.*)$", re.MULTILINE)
CHAPTER_RE = re.compile(r"^第[零一二三四五六七八九十百千万亿\d]+章", re.MULTILINE)


@dataclass(frozen=True)
class FileSignature:
    size: int
    mtime_ns: int


@dataclass
class PendingFile:
    signature: FileSignature


@dataclass(frozen=True)
class NovelInfo:
    path: Path
    title: str
    source_url: str
    chapters: int
    characters: int
    size_bytes: int
    integrity: str


def format_size(size_bytes: float) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            precision = 0 if unit == "B" else 2
            return f"{value:,.{precision}f} {unit}"
        value /= 1024
    return f"{value:,.2f} TB"


def scan_files(output_dir: Path) -> dict[Path, FileSignature]:
    files: dict[Path, FileSignature] = {}
    if not output_dir.exists():
        return files

    for path in output_dir.rglob("*.txt"):
        try:
            stat = path.stat()
        except OSError:
            continue
        if path.is_file():
            files[path] = FileSignature(stat.st_size, stat.st_mtime_ns)
    return files


def output_summary(output_dir: Path, files: dict[Path, FileSignature]) -> str:
    count = len(files)
    total_bytes = sum(signature.size for signature in files.values())
    average_bytes = total_bytes / count if count else 0

    lines = [
        "Output directory summary",
        f"  Directory:          {output_dir.resolve()}",
        f"  Novels downloaded:  {count:,}",
        f"  Total size:         {format_size(total_bytes)}",
        f"  Average novel size: {format_size(average_bytes)}",
    ]

    if files:
        smallest = min(files, key=lambda path: files[path].size)
        largest = max(files, key=lambda path: files[path].size)
        lines.extend(
            [
                f"  Smallest novel:     {format_size(files[smallest].size)}  "
                f"{smallest.relative_to(output_dir)}",
                f"  Largest novel:      {format_size(files[largest].size)}  "
                f"{largest.relative_to(output_dir)}",
            ]
        )
    return "\n".join(lines)


def parse_novel(path: Path) -> NovelInfo:
    text = path.read_text(encoding="utf-8", errors="replace")
    fields: dict[str, str] = {}
    source_line_end = 0

    for match in FIELD_RE.finditer(text):
        key, value = match.groups()
        fields.setdefault(key, value.strip())
        if key == "来源":
            source_line_end = match.end()

    title = fields.get("标题") or path.stem
    source_url = fields.get("来源") or "(source URL missing)"
    integrity = fields.get("完整性") or "unknown"

    chapter_value = fields.get("章节数", "")
    try:
        chapters = int(chapter_value)
    except ValueError:
        chapters = len(CHAPTER_RE.findall(text))

    # Match scraper.py's character metric: non-whitespace characters in the
    # novel body, excluding the generated metadata preamble.
    body = text[source_line_end:] if source_line_end else text
    characters = sum(1 for char in body if not char.isspace())

    return NovelInfo(
        path=path,
        title=title,
        source_url=source_url,
        chapters=chapters,
        characters=characters,
        size_bytes=path.stat().st_size,
        integrity=integrity,
    )


def format_novel(info: NovelInfo, output_dir: Path) -> str:
    try:
        relative_path = info.path.relative_to(output_dir)
    except ValueError:
        relative_path = info.path

    return "\n".join(
        [
            "New novel detected",
            f"  Title:      {info.title}",
            f"  Chapters:   {info.chapters:,}",
            f"  Characters: {info.characters:,} non-whitespace body characters",
            f"  Size:       {info.size_bytes / 1024:,.2f} KB",
            f"  Source:     {info.source_url}",
            f"  Integrity:  {info.integrity}",
            f"  File:       {relative_path}",
        ]
    )


def configure_logging(log_path: Path | None) -> logging.Logger:
    logger = logging.getLogger("output_watcher")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(formatter)
    logger.addHandler(stream_handler)

    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding="utf-8")
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.propagate = False
    return logger


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output",
        type=Path,
        default=OUTPUT_DIR,
        help=f"Directory to watch (default: {OUTPUT_DIR})",
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=2.0,
        help="Seconds between scans (default: 2)",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=DEFAULT_LOG,
        help=f"Append events to this log file (default: {DEFAULT_LOG})",
    )
    parser.add_argument(
        "--no-log-file",
        action="store_true",
        help="Write to the terminal only",
    )
    args = parser.parse_args()
    if args.interval <= 0:
        parser.error("--interval must be greater than zero")
    return args


def main() -> int:
    args = parse_args()
    output_dir = args.output.resolve()
    log_path = None if args.no_log_file else args.log.resolve()
    logger = configure_logging(log_path)
    stop_event = threading.Event()

    def request_stop(_signum: int, _frame: object) -> None:
        stop_event.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)

    known = scan_files(output_dir)
    pending: dict[Path, PendingFile] = {}

    logger.info(output_summary(output_dir, known))
    logger.info(
        "Watching for new .txt files every %g second(s). Press Ctrl-C to stop.",
        args.interval,
    )
    if log_path is not None:
        logger.info("Watcher log: %s", log_path)

    while not stop_event.wait(args.interval):
        current = scan_files(output_dir)

        # A deleted path should be treated as new if it later reappears.
        for missing in known.keys() - current.keys():
            known.pop(missing, None)

        for path, signature in current.items():
            if path in known:
                continue

            candidate = pending.get(path)
            if candidate is None or candidate.signature != signature:
                pending[path] = PendingFile(signature=signature)
                continue

            try:
                info = parse_novel(path)
            except OSError as exc:
                logger.warning("Could not inspect new file %s: %s", path, exc)
                pending.pop(path, None)
                continue

            logger.info(format_novel(info, output_dir))
            known[path] = signature
            pending.pop(path, None)

        for missing in pending.keys() - current.keys():
            pending.pop(missing, None)

    logger.info("Watcher stopped. %d novel file(s) currently present.", len(known))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
