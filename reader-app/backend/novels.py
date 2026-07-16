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
from webnovel.library import Chapter, local_chapters, local_path, local_synopsis
from scripts.repo_paths import LIBRARY_DIR

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
    url: str
    record: NovelRecord
    chapters: list[Chapter]
    synopsis: str


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
    chapters = local_chapters(path) if path and path.exists() else []
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
    synopsis_chapter = local_synopsis(path)
    synopsis = synopsis_chapter.body if synopsis_chapter else (record.synopsis or "")
    return ResolvedNovel(url=url, record=record, chapters=chapters, synopsis=synopsis)


def resolve_slug(slug: str) -> ResolvedNovel | None:
    """Resolve a "<category>/<stem>" slug to a downloaded novel, or None."""
    url = url_for_slug(slug)
    return resolve(url) if url is not None else None


def invalidate(url: str) -> None:
    global _records_cache, _records_mtimes, _slug_cache
    with _cache_lock:
        _chapter_cache.pop(url, None)
    with _records_lock:
        _records_cache = None  # a download added/changed a metadata record
        _records_mtimes = None
        _slug_cache = None
