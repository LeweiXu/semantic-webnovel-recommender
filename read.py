#!/usr/bin/env python3
"""Read a downloaded novel, tracking progress and copying chapters to the clipboard.

This is built for reading Chinese text in a separate annotation app (pinyin /
dictionary lookup): it remembers where you are in each novel and serves the next
N chapters straight to the system clipboard so you can paste them in. On WSL2
the clipboard is the Windows clipboard (via clip.exe).

The bookmark for each novel lives in data/reading_progress.json and advances as
you copy, so re-running the command keeps handing you the next chapters.

Examples:
  # Copy the synopsis + next chapter to the clipboard and advance the bookmark
  python read.py "诡秘之主" --copy

  # Copy the synopsis + next 3 chapters and advance, if starting at chapter 1
  python read.py "诡秘之主" --copy 3

  # Jump to chapter 12, then copy 5 chapters from there
  python read.py "诡秘之主" --chapter 12 --copy 5

  # Peek at the next 2 chapters WITHOUT moving the bookmark
  python read.py "诡秘之主" --copy 2 --no-advance

  # Where am I? / list chapters / reset to the start
  python read.py "诡秘之主"
  python read.py "诡秘之主" --list
  python read.py "诡秘之主" --reset

  # Print the next chapter to the terminal instead of the clipboard
  python read.py "诡秘之主" --copy --stdout

  # Show saved progress across all novels
  python read.py --progress

  # Open the reading-room web app (optionally jumping to a novel's bookmark)
  python read.py --gui
  python read.py "诡秘之主" --gui
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from webnovel import progress
from webnovel.library import (
    Chapter,
    clipboard_copy,
    local_chapters,
    local_path,
    local_synopsis,
)
from webnovel.targets import choose_resolution, print_candidates


def _show_all_progress() -> int:
    entries = progress.all_progress()
    if not entries:
        print("No reading progress recorded yet.")
        return 0
    print(f"Reading progress ({len(entries)} novel(s)):\n")
    for url, entry in entries.items():
        title = entry.get("title") or url
        position = int(entry.get("position", 0))
        total = entry.get("total")
        where = f"chapter {position + 1}" + (f"/{total}" if total else "")
        if total and position >= int(total):
            where = f"finished ({total} chapters)"
        updated = entry.get("updated", "?")
        print(f"  《{title}》 — {where}   (updated {updated})")
        print(f"      {url}")
    return 0


def _resolve_downloaded(target: str):
    """Return (record, chapters, synopsis) for a downloaded novel, or None."""
    resolution = choose_resolution(target, downloaded_only=True, interactive=sys.stdin.isatty())
    record = resolution.record
    if record is None and resolution.url:
        # A bare URL resolves to no store record; look it up by URL.
        from recsys.store import load_all

        record = load_all().get(resolution.url)
    if record is None:
        if resolution.candidates:
            print(f"Ambiguous title: {target}")
            print_candidates(resolution.candidates)
        elif resolution.url:
            print("That URL is not in the downloaded library. Download it first:")
            print(f"  python download.py novel {resolution.url}")
        else:
            print(f"No downloaded novel matched: {target}")
        return None
    path = local_path(record)
    if path is None or not path.exists():
        print(f"《{record.title}》 has metadata but no local file. Download it first:")
        print(f"  python download.py novel \"{record.title}\"")
        return None
    chapters = local_chapters(path)
    if not chapters:
        print(f"No readable chapters found in {path}.")
        return None
    return record, chapters, local_synopsis(path)


def _print_status(record, chapters: list[Chapter], position: int) -> None:
    total = len(chapters)
    print(f"《{record.title}》 — {record.author or '?'}  [{record.category}]")
    if position >= total:
        print(f"Bookmark: finished all {total} chapters. Use --reset to start over.")
        return
    print(f"Bookmark: chapter {position + 1} of {total}  →  {chapters[position].title}")
    print("Copy it to the clipboard with:  python read.py "
          f"\"{record.title}\" --copy [N]")


def _list_chapters(record, chapters: list[Chapter], position: int) -> None:
    print(f"《{record.title}》 — {len(chapters)} chapters (▸ = next to read):\n")
    for index, chapter in enumerate(chapters):
        marker = "▸" if index == position else " "
        print(f"  {marker} {index + 1:>4}. {chapter.title}")


def _payload_for_span(
    chapters: list[Chapter],
    position: int,
    end: int,
    *,
    synopsis: Chapter | None,
    include_synopsis: bool,
) -> str:
    sections: list[str] = []
    if include_synopsis and position == 0 and synopsis is not None:
        sections.append(synopsis.text())
    sections.extend(chapter.text() for chapter in chapters[position:end])
    return "\n\n".join(sections)


def cmd_read(args) -> int:
    resolved = _resolve_downloaded(args.target)
    if resolved is None:
        return 1
    record, chapters, synopsis = resolved
    total = len(chapters)

    # Move the bookmark if an explicit chapter was given.
    if args.chapter is not None:
        position = max(0, min(args.chapter - 1, total - 1))
        progress.set_position(record.url, position, title=record.title, total=total)
    else:
        position = min(progress.get_position(record.url), total)

    if args.reset:
        progress.clear(record.url)
        print(f"Reset 《{record.title}》 to chapter 1.")
        return 0

    if args.list:
        _list_chapters(record, chapters, min(position, total - 1))
        return 0

    if args.copy is None:
        _print_status(record, chapters, position)
        return 0

    # --copy N: serve N chapters from the current bookmark.
    if position >= total:
        print(f"《{record.title}》 is finished ({total} chapters). Use --reset to start over.")
        return 0
    count = max(1, args.copy)
    end = min(position + count, total)
    served = chapters[position:end]
    payload = _payload_for_span(
        chapters,
        position,
        end,
        synopsis=synopsis,
        include_synopsis=not args.no_synopsis,
    )

    if args.stdout:
        print(payload)
    else:
        try:
            backend = clipboard_copy(payload)
        except Exception as exc:
            print(f"Copy failed: {exc}", file=sys.stderr)
            return 1
        span = f"chapter {position + 1}" if len(served) == 1 else f"chapters {position + 1}–{end}"
        print(f"Copied {span} of {total} to the clipboard via {backend}.")
        print(f"  {served[0].title}" + (f"  …  {served[-1].title}" if len(served) > 1 else ""))

    if args.no_advance:
        if not args.stdout:
            print(f"Bookmark unchanged (still chapter {position + 1}).")
        return 0

    progress.set_position(record.url, end, title=record.title, total=total)
    if not args.stdout:
        if end >= total:
            print(f"Bookmark → finished all {total} chapters.")
        else:
            print(f"Bookmark → chapter {end + 1}: {chapters[end].title}")
    return 0


def cmd_gui(args) -> int:
    """Launch the local reading-room web app (reader-app/serve.py) in-process.

    With a TARGET, deep-link straight to that novel at its current bookmark; the
    web app shares data/reading_progress.json, so the two stay in sync.
    """
    reader_app = Path(__file__).resolve().parent / "reader-app"
    if not (reader_app / "serve.py").exists():
        print("reader-app is missing — cannot launch the GUI.", file=sys.stderr)
        return 1
    sys.path.insert(0, str(reader_app))
    sys.path.insert(0, str(reader_app / "backend"))
    try:
        import serve
        from ids import nid_encode
    except ImportError as exc:
        print(f"Could not load the reader app ({exc}).", file=sys.stderr)
        print("Run reader-app/setup.py first to install its dependencies.", file=sys.stderr)
        return 1

    open_path = ""
    if args.target:
        resolved = _resolve_downloaded(args.target)
        if resolved is None:
            return 1
        record, chapters, _ = resolved
        position = min(progress.get_position(record.url), len(chapters) - 1)
        open_path = f"?open={nid_encode(record.url)}&ch={position}"

    return serve.serve(
        args.host,
        args.port,
        open_browser_tab=not args.no_open,
        rebuild=args.rebuild,
        open_path=open_path,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="read.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "target", nargs="?",
        help="Novel title (in the downloaded library) or a downloaded novel's URL",
    )
    parser.add_argument(
        "--copy", nargs="?", type=int, const=1, default=None, metavar="N",
        help="Copy N chapters from the bookmark to the clipboard and advance "
        "(N defaults to 1 when given without a number)",
    )
    parser.add_argument(
        "--chapter", type=int, metavar="K",
        help="Move the bookmark to chapter K (1-based) before reading/copying",
    )
    parser.add_argument(
        "--no-advance", action="store_true",
        help="Copy without moving the bookmark (peek ahead)",
    )
    parser.add_argument(
        "--no-synopsis", action="store_true",
        help="Do not prepend the synopsis when copying from chapter 1",
    )
    parser.add_argument(
        "--stdout", action="store_true",
        help="Print the chapters to the terminal instead of the clipboard",
    )
    parser.add_argument(
        "--list", action="store_true",
        help="List the novel's chapters with the current bookmark marked",
    )
    parser.add_argument(
        "--reset", action="store_true",
        help="Clear this novel's bookmark (back to chapter 1)",
    )
    parser.add_argument(
        "--progress", action="store_true",
        help="Show saved progress across all novels (ignores TARGET)",
    )

    gui = parser.add_argument_group(
        "reading-room GUI",
        "Open the local web reader instead of copying to the clipboard. With a "
        "TARGET, it jumps straight to that novel at its current bookmark.",
    )
    gui.add_argument(
        "--gui", action="store_true",
        help="Launch the reading-room web app (reader-app) in the browser",
    )
    gui.add_argument("--port", type=int, default=8000, help="GUI server port (default 8000)")
    gui.add_argument("--host", default="127.0.0.1", help="GUI server host (default 127.0.0.1)")
    gui.add_argument("--no-open", action="store_true", help="Don't open a browser tab")
    gui.add_argument("--rebuild", action="store_true", help="Force a frontend rebuild")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.gui:
        return cmd_gui(args)
    if args.progress:
        return _show_all_progress()
    if not args.target:
        build_parser().error("a novel TARGET is required (or use --progress)")
    return cmd_read(args)


if __name__ == "__main__":
    raise SystemExit(main())
