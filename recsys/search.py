"""Retrieval: semantic cosine + tag-overlap boost + metadata filters.

At ~10k novels the embedding matrix is tiny, so similarity is an exact NumPy
matrix-vector product over the whole corpus (sub-millisecond) — no ANN index
needed.
"""
from __future__ import annotations

import difflib
from dataclasses import dataclass, field

import numpy as np

from recsys.index import Index, load_index
from recsys.store import NovelRecord, load_all

DEFAULT_TAG_BOOST = 0.15


@dataclass
class Filters:
    status: str | None = None          # e.g. "完结"
    categories: set[str] = field(default_factory=set)    # gl / yanqing / …
    min_chapters: int | None = None
    max_chapters: int | None = None
    year_from: int | None = None       # inclusive (calendar year)
    year_to: int | None = None         # inclusive
    exclude_authors: set[str] = field(default_factory=set)
    require_tags: set[str] = field(default_factory=set)  # all must be present

    def active(self) -> bool:
        return any([
            self.status, self.categories, self.min_chapters, self.max_chapters,
            self.year_from, self.year_to, self.exclude_authors, self.require_tags,
        ])


@dataclass
class Result:
    record: NovelRecord
    score: float        # final ranking score (cosine + tag boost)
    cosine: float       # raw semantic similarity
    reason: str = ""    # filled by the optional LLM layer


def _year(rec: NovelRecord) -> int | None:
    if len(rec.year_month) >= 4 and rec.year_month[:4].isdigit():
        return int(rec.year_month[:4])
    return None


def _length(rec: NovelRecord) -> int | None:
    """Best available length proxy: real chapter count, else reading-page count."""
    return rec.chapter_count if rec.chapter_count is not None else rec.page_count


def _passes(rec: NovelRecord, f: Filters) -> bool:
    if f.status and rec.status != f.status:
        return False
    if f.categories and rec.category not in f.categories:
        return False
    if f.min_chapters is not None:
        n = _length(rec)
        if n is None or n < f.min_chapters:
            return False
    if f.max_chapters is not None:
        n = _length(rec)
        if n is None or n > f.max_chapters:
            return False
    if f.year_from is not None or f.year_to is not None:
        y = _year(rec)
        if y is None:
            return False
        if f.year_from is not None and y < f.year_from:
            return False
        if f.year_to is not None and y > f.year_to:
            return False
    if f.exclude_authors and rec.author in f.exclude_authors:
        return False
    if f.require_tags and not f.require_tags.issubset(set(rec.tags)):
        return False
    return True


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    inter = len(a & b)
    return inter / len(a | b) if inter else 0.0


class SearchEngine:
    def __init__(self, index: Index, records: list[NovelRecord]) -> None:
        self.index = index
        self.records = records                       # row-aligned with index.urls
        self.emb = index.embeddings                  # [N, D] normalized
        self._row_of = {r.url: i for i, r in enumerate(records)}
        self._titles = [r.title for r in records]

    @classmethod
    def load(cls) -> "SearchEngine":
        index = load_index()
        if index is None:
            raise RuntimeError("No index found — run `build` first.")
        store = load_all()
        missing = [u for u in index.urls if u not in store]
        if missing:
            raise RuntimeError(
                f"Index references {len(missing)} url(s) absent from the store; "
                "re-run `build` after `sync`."
            )
        records = [store[u] for u in index.urls]
        return cls(index, records)

    def vector_for_url(self, url: str) -> np.ndarray:
        return self.emb[self._row_of[url]]

    def resolve_title(self, query: str) -> NovelRecord | None:
        """Best-effort match of a (partial) title to a stored novel."""
        q = query.strip().lower()
        exact = [r for r in self.records if r.title.lower() == q]
        if exact:
            return exact[0]
        subs = [r for r in self.records if q in r.title.lower()]
        if subs:
            return min(subs, key=lambda r: len(r.title))
        close = difflib.get_close_matches(query, self._titles, n=1, cutoff=0.5)
        if close:
            return self.records[self._titles.index(close[0])]
        return None

    def search(self, query_vec: np.ndarray, *, ref_tags: set[str] | None = None,
               filters: Filters | None = None, exclude_urls: set[str] | None = None,
               n: int = 10, tag_boost: float = DEFAULT_TAG_BOOST) -> list[Result]:
        filters = filters or Filters()
        exclude_urls = exclude_urls or set()
        sims = self.emb @ query_vec                  # cosine (vectors normalized)

        results: list[Result] = []
        for row, cos in enumerate(sims):
            rec = self.records[row]
            if rec.url in exclude_urls or not _passes(rec, filters):
                continue
            score = float(cos)
            if ref_tags and rec.tags:
                score += tag_boost * _jaccard(ref_tags, set(rec.tags))
            results.append(Result(record=rec, score=score, cosine=float(cos)))

        results.sort(key=lambda r: r.score, reverse=True)
        return results[:n]
