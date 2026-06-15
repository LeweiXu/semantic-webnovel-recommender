"""Multi-category metadata crawler.

Discovers novels across all 52shuku categories by graph traversal — prev/next
chains (exhaustive within a category's timeline) plus recommendation links (which
cross categories and bridge deletion gaps) — and records each landing page's
metadata (synopsis + tags, optionally an opening excerpt) without downloading the
full text.

Two artifacts per category, both resumable:
  * <category>/metadata.jsonl — recommender records (source="meta")
  * <category>/_catalog.jsonl — the crawl graph + confirmed 404s

Uses a chain-first-then-recommendation-BFS strategy across all categories.
"""
from __future__ import annotations

import logging
import random
import re
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from scraper import (  # noqa: E402
    IMPERSONATE, REQUEST_TIMEOUT, fetch, is_novel_landing_url,
    parse_chapter_page, parse_landing,
)
from repo_paths import CATEGORIES  # noqa: E402

from recsys import tags as tagmod  # noqa: E402
from recsys.catalog import CatalogRecord, load_catalog, write_catalog  # noqa: E402
from recsys.store import (  # noqa: E402
    EXCERPT_MAX_CHARS, NovelRecord, load_category, supersedes, write_category,
)

log = logging.getLogger("recsys.crawl")

RECOMMENDATION_BFS_DEPTH = 4
CHECKPOINT_EVERY = 200
_UPLOAD_YM_RE = re.compile(r"(\d{4})年(\d{2})月")
_CHAPTER_RE = re.compile(r"_\d+$")


# ── URL helpers (category-agnostic) ─────────────────────────────────────────

def parse_url_parts(url: str) -> tuple[str | None, str | None, str | None]:
    """(category, shard, id) from any /{cat}/… URL shape."""
    segs = [s for s in urlparse(url).path.split("/") if s]
    if not segs:
        return None, None, None
    category = segs[0]
    ident = segs[-1][:-5] if segs[-1].endswith(".html") else segs[-1]
    shard = segs[-2] if len(segs) >= 3 else None
    return category, shard, ident


def is_novel_landing(url: str | None, categories: set[str] | None = None) -> bool:
    return is_novel_landing_url(url, categories)


def parse_recommendations(html: str, base_url: str, categories: set[str]) -> list[str]:
    """Novel landing URLs (in allowed categories) from the recommendation block."""
    soup = BeautifulSoup(html, "lxml")
    out: list[str] = []
    seen: set[str] = set()
    for a in soup.select("[class*=relate] a[href]"):
        absu = urljoin(base_url, a.get("href", ""))
        if absu not in seen and is_novel_landing(absu, categories):
            seen.add(absu)
            out.append(absu)
    return out


def _year_month(upload_date: str) -> str:
    m = _UPLOAD_YM_RE.search(upload_date or "")
    return f"{m.group(1)}-{m.group(2)}" if m else ""


# ── Fetch result (computed off the main thread) ─────────────────────────────

@dataclass
class FetchResult:
    url: str
    status: str                       # "ok" | "not_found" | "error"
    record: NovelRecord | None = None
    prev_url: str | None = None
    next_url: str | None = None
    rec_urls: list[str] | None = None


def _fetch_landing(session, url: str, categories: set[str], pages: int) -> FetchResult:
    try:
        resp = fetch(session, url)
    except FileNotFoundError:
        return FetchResult(url, "not_found")
    except Exception:
        return FetchResult(url, "error")

    meta = parse_landing(resp.text, url)
    if not meta.title:
        return FetchResult(url, "error")

    parsed = tagmod.extract(parse_chapter_page(resp.text).text, meta.title, meta.author)
    excerpt = ""
    if pages > 0 and meta.chapter_urls:
        texts: list[str] = []
        for cu in meta.chapter_urls[:pages]:
            try:
                texts.append(parse_chapter_page(fetch(session, cu).text).text)
            except Exception:
                pass
        excerpt = "\n".join(t for t in texts if t)[:EXCERPT_MAX_CHARS]

    category = parse_url_parts(url)[0] or ""
    rec = NovelRecord(
        url=url,
        category=category,
        title=meta.title,
        author=meta.author,
        status=meta.status,
        upload_date=meta.upload_date,
        year_month=_year_month(meta.upload_date),
        chapter_count=None,
        page_count=len(meta.chapter_urls),
        synopsis=parsed["synopsis"][:2500],
        tags=parsed["tags"],
        one_liner=parsed["one_liner"],
        intent=parsed["intent"],
        excerpt=excerpt,
        source="meta",
        file=None,
    )
    recs = parse_recommendations(resp.text, url, categories)
    return FetchResult(url, "ok", rec, meta.prev_url, meta.next_url, recs)


# ── Crawler ─────────────────────────────────────────────────────────────────

class MetaCrawler:
    def __init__(self, categories: list[str], *, pages: int = 0, delay: float = 0.4,
                 workers: int = 1, refresh: bool = False,
                 rec_depth: int = RECOMMENDATION_BFS_DEPTH) -> None:
        self.targets = set(categories)
        self.pages = pages
        self.delay = delay
        self.workers = max(1, workers)
        self.refresh = refresh
        self.rec_depth = rec_depth

        self.store: dict[str, dict[str, NovelRecord]] = {
            c: load_category(c) for c in categories
        }
        self.ledger: dict[str, dict[str, CatalogRecord]] = {
            c: load_catalog(c) for c in categories
        }
        self.dirty: set[str] = set()

        self.chain_queue: deque[str] = deque()
        self.rec_queue: deque[tuple[str, int]] = deque()
        self.scheduled: set[str] = set()
        self.visited: set[str] = set()

        self.fetched = self.not_found = self.errors = self.skipped = 0

    # ── frontier management ──────────────────────────────────────────────
    def _enqueue_chain(self, url: str | None) -> None:
        if url and url not in self.scheduled and is_novel_landing(url, self.targets):
            self.scheduled.add(url)
            self.chain_queue.append(url)

    def _enqueue_rec(self, url: str | None, depth: int) -> None:
        if depth > self.rec_depth or not is_novel_landing(url, self.targets):
            return
        if url in self.scheduled:
            return
        self.scheduled.add(url)
        self.rec_queue.append((url, depth))

    def _expand(self, url: str, depth: int) -> None:
        """Push a known node's neighbours from the ledger (no fetch)."""
        cat = parse_url_parts(url)[0]
        led = self.ledger.get(cat, {}).get(url)
        if not led:
            return
        self._enqueue_chain(led.prev_url)
        self._enqueue_chain(led.next_url)
        for r in led.rec_urls:
            self._enqueue_rec(r, depth + 1)

    def seed(self, session) -> None:
        for cat in self.targets:
            # Known graph: re-expand every ledger node (skip-fetch) to continue.
            for url in self.ledger.get(cat, {}):
                self._enqueue_chain(url)
            # Fresh uploads: the category index page lists recent novels. Index
            # listings lack the novel content-markers scraper._classify looks
            # for, so fetch them raw instead of via fetch().
            try:
                resp = session.get(f"https://www.52shuku.net/{cat}/",
                                   timeout=REQUEST_TIMEOUT, impersonate=IMPERSONATE)
                if resp.status_code != 200:
                    raise RuntimeError(f"index HTTP {resp.status_code}")
                soup = BeautifulSoup(resp.text, "lxml")
                base = str(resp.url)
                for a in soup.select("a[href]"):
                    absu = urljoin(base, a.get("href", ""))
                    if is_novel_landing(absu, {cat}):
                        self._enqueue_chain(absu)
            except Exception as exc:
                log.warning("Could not seed %s index: %s", cat, exc)

    # ── integration ──────────────────────────────────────────────────────
    def _is_known(self, url: str) -> bool:
        cat = parse_url_parts(url)[0]
        if self.refresh:
            return False
        led = self.ledger.get(cat, {}).get(url)
        if led and led.fetch_status == "not_found":
            return True
        return bool(
            led
            and led.fetch_status == "ok"
            and led.has_meta
            and url in self.store.get(cat, {})
        )

    def _integrate(self, res: FetchResult, depth: int) -> None:
        cat = parse_url_parts(res.url)[0] or ""
        led = self.ledger.setdefault(cat, {})
        if res.status == "not_found":
            led[res.url] = CatalogRecord(res.url, cat, "not_found")
            self.not_found += 1
            self.dirty.add(cat)
            return
        if res.status == "error" or res.record is None:
            self.errors += 1
            return
        # store (respect full > meta precedence)
        existing = self.store.setdefault(cat, {}).get(res.url)
        if existing is None or supersedes(res.record, existing):
            self.store[cat][res.url] = res.record
        led[res.url] = CatalogRecord(
            res.url, cat, "ok", res.prev_url, res.next_url,
            list(res.rec_urls or ()), has_meta=True,
        )
        self.dirty.add(cat)
        self.fetched += 1
        self._enqueue_chain(res.prev_url)
        self._enqueue_chain(res.next_url)
        for r in (res.rec_urls or ()):
            self._enqueue_rec(r, depth + 1)

    def _next_batch(self, size: int) -> list[tuple[str, int]]:
        """Pop up to `size` urls that actually need fetching; expand known nodes
        inline. Chain frontier drains before the recommendation frontier."""
        batch: list[tuple[str, int]] = []
        while len(batch) < size and (self.chain_queue or self.rec_queue):
            if self.chain_queue:
                url, depth = self.chain_queue.popleft(), 0
            else:
                url, depth = self.rec_queue.popleft()
            if url in self.visited:
                continue
            self.visited.add(url)
            if self._is_known(url):
                self.skipped += 1
                self._expand(url, depth)
                continue
            batch.append((url, depth))
        return batch

    def checkpoint(self) -> None:
        for cat in list(self.dirty):
            write_category(cat, self.store[cat])
            write_catalog(cat, self.ledger[cat])
        self.dirty.clear()

    def run(self, session, *, limit: int = 0,
            stop_event: threading.Event | None = None, controls=None) -> None:
        stop_event = controls.stop_event if controls is not None else (
            stop_event or threading.Event()
        )
        network_lock = controls.network_lock if controls is not None else None
        if controls is not None:
            controls.register_worker()
        with network_lock or nullcontext():
            self.seed(session)
        log.info("Seeded frontier: %d chain, %d rec candidates (targets: %s)",
                 len(self.chain_queue), len(self.rec_queue), ",".join(sorted(self.targets)))
        since_ckpt = 0
        pool = ThreadPoolExecutor(max_workers=self.workers) if self.workers > 1 else None
        try:
            while not stop_event.is_set():
                if controls is not None and not controls.safe_point(self.checkpoint):
                    break
                if limit and self.fetched >= limit:
                    break
                room = limit - self.fetched if limit else self.workers
                batch = self._next_batch(min(self.workers, max(1, room)))
                if not batch:
                    break

                with network_lock or nullcontext():
                    if pool is None:
                        results = [(_fetch_landing(session, batch[0][0], self.targets,
                                                   self.pages), batch[0][1])]
                    else:
                        futs = {pool.submit(_fetch_landing, session, u, self.targets,
                                            self.pages): d for u, d in batch}
                        results = [(f.result(), futs[f]) for f in futs]

                for res, depth in results:
                    self._integrate(res, depth)
                    since_ckpt += 1
                    if res.status == "ok":
                        log.info("[%s] %d fetched (q:%d/%d) %s",
                                 parse_url_parts(res.url)[0], self.fetched,
                                 len(self.chain_queue), len(self.rec_queue), res.url)

                if since_ckpt >= CHECKPOINT_EVERY:
                    self.checkpoint()
                    since_ckpt = 0
                if not stop_event.is_set():
                    delay = self.delay + random.uniform(0, self.delay)
                    if controls is not None:
                        controls.interruptible_wait(delay)
                    else:
                        stop_event.wait(delay)
        finally:
            if pool is not None:
                pool.shutdown(wait=True)
            self.checkpoint()
            if controls is not None:
                controls.unregister_worker()


def crawl(categories: list[str] | None = None, *, pages: int = 0, delay: float = 0.4,
          workers: int = 1, refresh: bool = False, limit: int = 0,
          rec_depth: int = RECOMMENDATION_BFS_DEPTH,
          stop_event: threading.Event | None = None, controls=None) -> dict:
    cats = categories or list(CATEGORIES)
    crawler = MetaCrawler(
        cats,
        pages=pages,
        delay=delay,
        workers=workers,
        refresh=refresh,
        rec_depth=rec_depth,
    )
    session = cffi_requests.Session()
    try:
        crawler.run(session, limit=limit, stop_event=stop_event, controls=controls)
    finally:
        session.close()
    return {
        "fetched": crawler.fetched,
        "not_found": crawler.not_found,
        "errors": crawler.errors,
        "skipped": crawler.skipped,
    }
