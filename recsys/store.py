"""Per-category metadata stores: <category>/metadata.jsonl, one record per line.

Each category folder at the repo root (gl/, yanqing/, …) owns a metadata.jsonl —
the recommender's view of that category. Records arrive from two sources,
deduplicated by `url` within their category file:
  * source="full" — extracted from a downloaded <cat>/YYYY-MM/*.txt novel
  * source="meta" — a landing-page-only crawl (synopsis + tags, no chapter text)

A "full" record takes precedence over a "meta" one for the same url (it is the
landing metadata plus verified chapter text). The crawl/resume graph lives
separately in <category>/_catalog.jsonl (see recsys.catalog).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from repo_paths import CATEGORIES, metadata_path  # noqa: E402

# Opening-text excerpt budget (chars) folded into the embed-doc for both full
# (sliced from .txt) and meta (first --pages reading pages) records.
EXCERPT_MAX_CHARS = 1500

# Precedence when two records share a url. Higher wins.
_SOURCE_RANK = {"meta": 0, "full": 1}


@dataclass
class NovelRecord:
    url: str                      # canonical id + dedup key
    category: str = ""            # gl / yanqing / bl / …
    title: str = ""
    author: str = ""
    status: str = ""              # 完结 / 连载 / ""
    upload_date: str = ""         # raw e.g. "2024年11月11日 20:09:06"
    year_month: str = ""          # "2024-11" (derived; "" if unknown)
    chapter_count: int | None = None   # real 第N章 count; None unless full text seen
    page_count: int | None = None      # number of reading pages (length proxy)
    synopsis: str = ""            # descriptive prose (structured tag lines removed)
    tags: list[str] = field(default_factory=list)   # 内容标签
    one_liner: str = ""           # 一句话简介
    intent: str = ""              # 立意
    excerpt: str = ""             # opening narrative text (optional)
    source: str = "full"          # "full" | "meta"
    file: str | None = None       # repo-relative .txt path; None if not downloaded
    content_hash: str = ""        # sha1 of embed_text(); set in __post_init__

    def __post_init__(self) -> None:
        if not self.content_hash:
            self.content_hash = hashlib.sha1(
                self.embed_text().encode("utf-8")
            ).hexdigest()

    def embed_text(self) -> str:
        """The document fed to the embedding model (and hashed for staleness)."""
        parts = [self.title]
        if self.tags:
            parts.append(" ".join(self.tags))
        if self.one_liner:
            parts.append(self.one_liner)
        if self.synopsis:
            parts.append(self.synopsis)
        if self.excerpt:
            parts.append(self.excerpt[:EXCERPT_MAX_CHARS])
        return "\n".join(p for p in parts if p)

    @property
    def downloaded(self) -> bool:
        return self.file is not None

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_dict(cls, d: dict) -> "NovelRecord":
        known = {f for f in cls.__dataclass_fields__}  # type: ignore[attr-defined]
        return cls(**{k: v for k, v in d.items() if k in known})


def supersedes(new: NovelRecord, old: NovelRecord) -> bool:
    """True if `new` should replace `old` for the same url: a higher-precedence
    source always wins; within the same source, a changed embed-doc is an update."""
    new_rank = _SOURCE_RANK.get(new.source, 0)
    old_rank = _SOURCE_RANK.get(old.source, 0)
    if new_rank != old_rank:
        return new_rank > old_rank
    return new.content_hash != old.content_hash


def load_category(category: str) -> dict[str, NovelRecord]:
    """Load one category's metadata.jsonl into {url: NovelRecord}."""
    path = metadata_path(category)
    records: dict[str, NovelRecord] = {}
    if not path.exists():
        return records
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rec = NovelRecord.from_dict(json.loads(line))
            records[rec.url] = rec
    return records


def load_all(categories: list[str] | None = None) -> dict[str, NovelRecord]:
    """Merge every category's metadata.jsonl into one {url: NovelRecord}."""
    merged: dict[str, NovelRecord] = {}
    for cat in (categories or CATEGORIES):
        merged.update(load_category(cat))
    return merged


def write_category(category: str, records: dict[str, NovelRecord] | list[NovelRecord]) -> None:
    """Atomically rewrite a category's metadata.jsonl, sorted by upload_date."""
    items = list(records.values()) if isinstance(records, dict) else list(records)
    items.sort(key=lambda r: (r.upload_date, r.url))
    path = metadata_path(category)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for rec in items:
            f.write(rec.to_json() + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def upsert_category(category: str, new_records: list[NovelRecord]) -> tuple[int, int]:
    """Merge records into one category's store. Returns (added, updated)."""
    store = load_category(category)
    added = updated = 0
    for rec in new_records:
        existing = store.get(rec.url)
        if existing is None:
            store[rec.url] = rec
            added += 1
        elif supersedes(rec, existing):
            store[rec.url] = rec
            updated += 1
    write_category(category, store)
    return added, updated


def upsert_all(new_records: list[NovelRecord]) -> tuple[int, int]:
    """Route records to their category files and upsert each. Records without a
    category are grouped under '' (written to ./metadata.jsonl, normally empty)."""
    by_cat: dict[str, list[NovelRecord]] = defaultdict(list)
    for rec in new_records:
        by_cat[rec.category or "gl"].append(rec)
    added = updated = 0
    for cat, recs in by_cat.items():
        a, u = upsert_category(cat, recs)
        added += a
        updated += u
    return added, updated
