"""Manual .txt upload into library/uploads/, with metadata autodetect.

Uploaded files land in their own store (library/uploads/metadata.jsonl) so they
read like any downloaded novel: chapters are detected from the text, and the
title/author/tags/synopsis come from the confirmation form. Autodetect just
pre-fills that form; the user always confirms before anything is written.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from recsys.store import NovelRecord, upsert_category
from scripts.repo_paths import LIBRARY_DIR
from webnovel.library import chapters_from_text, decode_text, detect_language

import novels

UPLOADS_CATEGORY = "uploads"


def _safe_filename(name: str) -> str:
    name = Path(name).name  # drop any directory part
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip().strip(".")
    return name or "untitled"


def _parse_filename(stem: str) -> tuple[str, str, str]:
    """Best-effort (title, author, status) from a messy novel filename."""
    status = "完结" if "完结" in stem else ("连载" if "连载" in stem else "")
    # Drop bracketed tag groups like 【GL】, (完结), （gl）, [xxx].
    cleaned = re.sub(r"[【\[（(][^】\]）)]*[】\]）)]", "", stem).strip()
    title, author = cleaned, ""
    m = re.search(r"《([^》]+)》\s*[-—]?\s*(.*)", cleaned)
    if m:
        title, author = m.group(1).strip(), m.group(2).strip(" -—_·")
    elif " - " in cleaned:
        left, right = cleaned.split(" - ", 1)
        title, author = left.strip(), right.strip()
    elif "_" in cleaned:
        left, right = cleaned.rsplit("_", 1)
        title, author = left.strip(), right.strip()
    if len(author) > 30:  # probably not really an author
        author = ""
    return (title or stem).strip(), author, status


def _guess_synopsis(text: str, chapters) -> str:
    """A rough synopsis: the front matter before chapter one, else the opening."""
    if chapters and chapters[0].title == "Front matter":
        return chapters[0].body.strip()[:400]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " ".join(lines[:3])[:400]


def detect(filename: str, raw: bytes) -> dict:
    """Suggested metadata for the confirmation form. Writes nothing."""
    text = decode_text(raw)
    stem = Path(filename).stem
    title, author, status = _parse_filename(stem)
    chapters = chapters_from_text(text)
    return {
        "filename": _safe_filename(stem) + ".txt",
        "title": title,
        "author": author,
        "status": status,
        "language": detect_language(text[:2000]),
        "chapter_count": len(chapters),
        "synopsis": _guess_synopsis(text, chapters),
        "tags": [],
    }


def save(
    filename: str,
    raw: bytes,
    *,
    title: str,
    author: str = "",
    tags: list[str] | None = None,
    synopsis: str = "",
    status: str = "",
) -> dict:
    """Write the file (normalised to UTF-8) and upsert its metadata record."""
    text = decode_text(raw)
    # Name the file after the confirmed title (clean slug), falling back to the
    # original filename stem.
    stem = _safe_filename(title.strip() or Path(filename).stem)
    dest_dir = LIBRARY_DIR / UPLOADS_CATEGORY
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Don't clobber an existing upload with the same name.
    fname = f"{stem}.txt"
    counter = 2
    while (dest_dir / fname).exists():
        fname = f"{stem}_{counter}.txt"
        counter += 1
    dest = dest_dir / fname
    dest.write_text(text, encoding="utf-8")

    rel = f"{UPLOADS_CATEGORY}/{fname}"
    chapters = chapters_from_text(text)
    now = datetime.now()
    record = NovelRecord(
        url=f"upload:{rel}",
        category=UPLOADS_CATEGORY,
        title=title.strip() or Path(fname).stem,
        author=author.strip(),
        status=status.strip(),
        upload_date=now.strftime("%Y年%m月%d日 %H:%M:%S"),
        year_month=now.strftime("%Y-%m"),
        chapter_count=len(chapters),
        synopsis=synopsis.strip(),
        tags=[t.strip() for t in (tags or []) if t.strip()],
        source="full",
        file=rel,
    )
    upsert_category(UPLOADS_CATEGORY, [record])
    novels.invalidate(record.url)

    return {
        "id": f"{UPLOADS_CATEGORY}/{Path(fname).stem}",  # slug the reader opens by
        "url": record.url,
        "title": record.title,
        "chapter_count": record.chapter_count,
    }
