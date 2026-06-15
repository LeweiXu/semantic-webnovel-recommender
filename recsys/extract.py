"""`sync` step: extract per-novel metadata from <category>/*.txt into each
category's metadata.jsonl.

Reads each saved novel's preamble (标题/作者/状态/上传时间/章节数/来源), the
synopsis block (between `来源：` and the first `═` divider), and an opening
excerpt (the start of the first chapter, for richer embeddings), then pulls
structured tags out of the synopsis (recsys.tags) and upserts a source="full"
NovelRecord into <category>/metadata.jsonl.

Incremental by default: a novel whose 来源 URL is already in its category store
as a "full" record is skipped after a cheap preamble read (use --rebuild to force).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from scraper import CHAPTER_RE, category_of_url, source_url_from_file  # noqa: E402
from repo_paths import CATEGORIES, ROOT_DIR, category_dir  # noqa: E402

from recsys import tags as tagmod  # noqa: E402
from recsys.store import (  # noqa: E402
    EXCERPT_MAX_CHARS, NovelRecord, load_category, upsert_category,
)

_DIVIDER_CHAR = "═"
_MAX_SYNOPSIS_RAW = 6000
_MAX_SYNOPSIS_STORED = 2500
_YEAR_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")
_UPLOAD_YM_RE = re.compile(r"(\d{4})年(\d{2})月")


def _field(line: str, prefix: str) -> str | None:
    return line[len(prefix):].strip() if line.startswith(prefix) else None


def _is_divider(line: str) -> bool:
    s = line.strip()
    return len(s) >= 10 and set(s) == {_DIVIDER_CHAR}


def _year_month(upload_date: str, path: Path) -> str:
    if _YEAR_MONTH_RE.match(path.parent.name):
        return path.parent.name
    m = _UPLOAD_YM_RE.search(upload_date)
    return f"{m.group(1)}-{m.group(2)}" if m else ""


def parse_txt(path: Path, url: str) -> NovelRecord | None:
    """Parse one saved novel .txt into a NovelRecord (source='full')."""
    title = author = status = upload_date = ""
    chapter_count: int | None = None
    syn_lines: list[str] = []
    excerpt_lines: list[str] = []
    # 0 = preamble, 1 = synopsis (post-来源), 2 = body excerpt (post-first-divider)
    phase = 0
    syn_chars = excerpt_chars = 0

    try:
        with path.open(encoding="utf-8") as f:
            for line in f:
                line = line.rstrip("\n")
                if phase == 0:
                    if (v := _field(line, "标题：")) is not None:
                        title = v
                    elif (v := _field(line, "作者：")) is not None:
                        author = v
                    elif (v := _field(line, "状态：")) is not None:
                        status = v
                    elif (v := _field(line, "上传时间：")) is not None:
                        upload_date = v
                    elif (v := _field(line, "章节数：")) is not None:
                        chapter_count = int(v) if v.isdigit() else None
                    elif line.startswith("来源："):
                        phase = 1
                    continue
                if phase == 1:
                    if _is_divider(line):
                        phase = 2
                        continue
                    syn_lines.append(line)
                    syn_chars += len(line)
                    if syn_chars >= _MAX_SYNOPSIS_RAW:
                        phase = 2  # synopsis runaway (no markers) — start excerpt
                    continue
                # phase 2: opening excerpt from the body
                if _is_divider(line) or CHAPTER_RE.match(line.strip()):
                    continue
                stripped = line.strip()
                if stripped:
                    excerpt_lines.append(stripped)
                    excerpt_chars += len(stripped)
                    if excerpt_chars >= EXCERPT_MAX_CHARS:
                        break
    except OSError:
        return None

    if not title:
        return None

    parsed = tagmod.extract("\n".join(syn_lines), title, author)
    synopsis = parsed["synopsis"][:_MAX_SYNOPSIS_STORED]
    excerpt = "\n".join(excerpt_lines)[:EXCERPT_MAX_CHARS]

    try:
        rel = str(path.resolve().relative_to(ROOT_DIR))
    except ValueError:
        rel = str(path)

    return NovelRecord(
        url=url,
        category=category_of_url(url) or path.parent.parent.name,
        title=title,
        author=author,
        status=status,
        upload_date=upload_date,
        year_month=_year_month(upload_date, path),
        chapter_count=chapter_count,
        synopsis=synopsis,
        tags=parsed["tags"],
        one_liner=parsed["one_liner"],
        intent=parsed["intent"],
        excerpt=excerpt,
        source="full",
        file=rel,
    )


def sync_category(category: str, limit: int = 0,
                  rebuild: bool = False) -> tuple[int, int, int]:
    """Scan one category's downloaded .txt files and upsert full records."""
    base = category_dir(category)
    if not base.exists():
        return 0, 0, 0
    existing = load_category(category)
    known_full = {u for u, r in existing.items() if r.source == "full"}

    new_records: list[NovelRecord] = []
    parsed_count = skipped = 0
    for path in sorted(base.rglob("*.txt")):
        url = source_url_from_file(path)
        if not url:
            continue
        if not rebuild and url in known_full:
            skipped += 1
            continue
        rec = parse_txt(path, url)
        if rec is None:
            continue
        new_records.append(rec)
        parsed_count += 1
        if limit and parsed_count >= limit:
            break

    added, updated = upsert_category(category, new_records)
    return added, updated, skipped


def sync(limit: int = 0, rebuild: bool = False,
         categories: list[str] | None = None) -> tuple[int, int, int]:
    """Sync every category's downloaded .txt files. Returns (added, updated, skipped)."""
    added = updated = skipped = 0
    for cat in (categories or CATEGORIES):
        a, u, s = sync_category(cat, limit=limit, rebuild=rebuild)
        added += a
        updated += u
        skipped += s
    return added, updated, skipped
