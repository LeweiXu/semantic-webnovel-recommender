"""Novel + chapter access, reusing the existing webnovel/recsys library.

The backend never reparses files itself — it leans on the same functions the
`read.py` CLI uses, so the web app and the CLI always agree on chapters and
progress.
"""
from __future__ import annotations

import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

from recsys.store import NovelRecord, load_all
from webnovel.library import (
    Chapter, detect_language, local_chapters, local_path, local_synopsis,
    raw_chapters, read_text_smart,
)
from scripts.repo_paths import LIBRARY_DIR

import browse
import chapter_patterns

# Small LRU of parsed chapter lists keyed by novel url. A median novel is
# ~300k chars; parsing it on every chapter request would be wasteful, so keep a
# few recently-opened novels in memory.
_CACHE_SIZE = 6
_chapter_cache: "OrderedDict[str, list[Chapter]]" = OrderedDict()
_cache_lock = threading.Lock()

# load_all() scans every category's metadata.jsonl (~10k records), which is too
# slow to repeat per request. Cache it; it only changes when a novel is
# downloaded, which calls invalidate().
_records_cache: dict[str, NovelRecord] | None = None
_records_mtimes: tuple[tuple[str, int], ...] | None = None
_records_lock = threading.Lock()

# slug -> url, built alongside _records so downloaded novels can be addressed by
# a readable "<category>/<file stem>" path in the frontend URL instead of a hash.
_slug_cache: dict[str, str] | None = None


@dataclass
class ResolvedNovel:
    # ``id`` is the frontend/URL id (metadata slug or a raw browse path); ``url``
    # is the progress/cache key (the real 52shuku url, or the raw path for a
    # file-explorer novel). ``record`` is None for raw files not in the store.
    id: str
    url: str
    title: str
    author: str
    category: str
    tags: list[str]
    synopsis: str
    chapters: list[Chapter]
    kind: str = "novel"  # "novel" = metadata-backed, "text" = raw .txt
    language: str = "zh"
    chapter_mode: str = "detected"  # "detected" | "fallback" | "custom"
    chapter_pattern: str | None = None
    record: NovelRecord | None = None


def _records() -> dict[str, NovelRecord]:
    global _records_cache, _records_mtimes, _slug_cache
    with _records_lock:
        mtimes = tuple(
            sorted(
                (str(path), path.stat().st_mtime_ns)
                for path in LIBRARY_DIR.glob("*/metadata.jsonl")
                if path.is_file()
            )
        )
        if _records_cache is None or mtimes != _records_mtimes:
            _records_cache = load_all()
            _records_mtimes = mtimes
            _slug_cache = None  # rebuilt lazily from the fresh records
        return _records_cache


def slug_for(record: NovelRecord | None) -> str | None:
    """A readable id for a downloaded novel: "<category>/<file stem>" (no .txt)."""
    if record is None or not record.file:
        return None
    return f"{record.category}/{Path(record.file).stem}"


def _slug_index() -> dict[str, str]:
    global _slug_cache
    records = _records()  # refreshes _slug_cache to None if the library changed
    with _records_lock:
        if _slug_cache is None:
            index: dict[str, str] = {}
            for url, record in records.items():
                slug = slug_for(record)
                if slug and slug not in index:  # first writer wins on a rare clash
                    index[slug] = url
            _slug_cache = index
        return _slug_cache


def url_for_slug(slug: str) -> str | None:
    return _slug_index().get(slug)


def record_for_url(url: str) -> NovelRecord | None:
    return _records().get(url)


def chapters_for(url: str, record: NovelRecord) -> list[Chapter]:
    with _cache_lock:
        cached = _chapter_cache.get(url)
        if cached is not None:
            _chapter_cache.move_to_end(url)
            return cached
    path = local_path(record)
    pattern = chapter_patterns.get(url)
    chapters = local_chapters(path, pattern) if path and path.exists() else []
    with _cache_lock:
        _chapter_cache[url] = chapters
        _chapter_cache.move_to_end(url)
        while len(_chapter_cache) > _CACHE_SIZE:
            _chapter_cache.popitem(last=False)
    return chapters


def resolve(url: str) -> ResolvedNovel | None:
    """Return the record, parsed chapters, and synopsis for a downloaded novel."""
    record = record_for_url(url)
    if record is None:
        return None
    path = local_path(record)
    if path is None or not path.exists():
        return None
    chapters = chapters_for(url, record)
    pattern = chapter_patterns.get(url)
    synopsis_chapter = local_synopsis(path)
    synopsis = synopsis_chapter.body if synopsis_chapter else (record.synopsis or "")
    return ResolvedNovel(
        id=slug_for(record) or url,
        url=url,
        title=record.title,
        author=record.author,
        category=record.category,
        tags=list(record.tags),
        synopsis=synopsis,
        chapters=chapters,
        kind="novel",
        language="zh",
        chapter_mode="custom" if pattern else _chapter_mode(chapters),
        chapter_pattern=pattern,
        record=record,
    )


def resolve_slug(slug: str) -> ResolvedNovel | None:
    """Resolve a "<category>/<stem>" slug to a downloaded novel, or None."""
    url = url_for_slug(slug)
    return resolve(url) if url is not None else None


def resolve_path(rawid: str) -> ResolvedNovel | None:
    """Resolve a raw browse path (e.g. "GL/foo.txt") to a readable novel, or None.

    This is the file-explorer read path: it opens .txt files that aren't in the
    metadata store, so there's no NovelRecord. Chapters and language come from
    the file itself. The rawid doubles as the progress/cache key.
    """
    try:
        path = browse.safe_join(rawid)
    except ValueError:
        return None
    if not path.is_file() or browse.classify(path) != "text":
        return None

    with _cache_lock:
        chapters = _chapter_cache.get(rawid)
        if chapters is not None:
            _chapter_cache.move_to_end(rawid)
    if chapters is None:
        chapters = raw_chapters(path, chapter_patterns.get(rawid))
        with _cache_lock:
            _chapter_cache[rawid] = chapters
            _chapter_cache.move_to_end(rawid)
            while len(_chapter_cache) > _CACHE_SIZE:
                _chapter_cache.popitem(last=False)

    sample = "\n".join(ch.text() for ch in chapters[:1])[:2000]
    pattern = chapter_patterns.get(rawid)
    return ResolvedNovel(
        id=rawid,
        url=rawid,
        title=path.stem,
        author="",
        category="",
        tags=[],
        synopsis="",
        chapters=chapters,
        kind="text",
        language=detect_language(sample),
        chapter_mode="custom" if pattern else _chapter_mode(chapters),
        chapter_pattern=pattern,
        record=None,
    )


def _chapter_mode(chapters: list[Chapter]) -> str:
    return (
        "fallback"
        if chapters and all(chapter.title.startswith("Part ") for chapter in chapters)
        else "detected"
    )


def source_text(resolved: ResolvedNovel) -> str:
    """Return the original book text used to preview a custom heading regex."""
    if resolved.record is not None:
        path = local_path(resolved.record)
    else:
        try:
            path = browse.safe_join(resolved.id)
        except ValueError:
            path = None
    if path is None or not path.is_file():
        return ""
    return read_text_smart(path)


def invalidate(url: str) -> None:
    global _records_cache, _records_mtimes, _slug_cache
    with _cache_lock:
        _chapter_cache.pop(url, None)
    with _records_lock:
        _records_cache = None  # a download added/changed a metadata record
        _records_mtimes = None
        _slug_cache = None
