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
from collections import deque
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
from recsys.routes import Route, open_windscribe_routes  # noqa: E402
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
    def __init__(self, categories: list[str], *, pages: int = 0, delay: float = 0.1,
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

        # All frontier/graph/counter mutation happens under this lock; only the
        # network fetch in each worker runs outside it. `_active` counts workers
        # with a claimed URL still in flight, so the run ends only when the
        # frontier is empty AND no fetch is outstanding.
        self._lock = threading.Lock()
        self._cond = threading.Condition(self._lock)
        self._active = 0
        self._since_ckpt = 0

    @property
    def total_metadata(self) -> int:
        """Current number of metadata records across the selected categories."""
        return sum(len(records) for records in self.store.values())

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

    def _claim_next_locked(self, limit: int) -> tuple[str, int] | None:
        """Pop the next url that actually needs fetching, expanding known nodes
        inline. Caller must hold `self._lock`. Chain frontier drains before the
        recommendation frontier."""
        if limit and self.fetched >= limit:
            return None
        while self.chain_queue or self.rec_queue:
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
            return url, depth
        return None

    def checkpoint(self) -> None:
        for cat in list(self.dirty):
            write_category(cat, self.store[cat])
            write_catalog(cat, self.ledger[cat])
        self.dirty.clear()

    def _worker(self, route: Route, *, limit: int, stop_event: threading.Event) -> None:
        """One route's loop: claim a url (locked), fetch it (unlocked), integrate
        the result (locked). Shared state is only ever touched under the lock."""
        while not stop_event.is_set():
            with self._cond:
                item = self._claim_next_locked(limit)
                if item is None:
                    # No fetchable url right now. If nothing is in flight either,
                    # the crawl is finished; otherwise wait for a peer to produce
                    # more frontier (or time out to re-check stop_event).
                    if self._active == 0:
                        self._cond.notify_all()
                        return
                    self._cond.wait(timeout=0.5)
                    continue
                url, depth = item
                self._active += 1

            result = _fetch_landing(route.session, url, self.targets, self.pages)

            with self._cond:
                self._integrate(result, depth)
                self._active -= 1
                self._since_ckpt += 1
                if result.status == "ok":
                    log.info("[%s] %d metadata, %d fetched this run "
                             "(q:%d/%d) via %s %s",
                             parse_url_parts(result.url)[0], self.total_metadata,
                             self.fetched,
                             len(self.chain_queue), len(self.rec_queue),
                             route.label, result.url)
                if self._since_ckpt >= CHECKPOINT_EVERY:
                    self.checkpoint()
                    self._since_ckpt = 0
                self._cond.notify_all()

            if not stop_event.is_set():
                stop_event.wait(self.delay + random.uniform(0, self.delay * 0.5))

    def run(self, session, *, limit: int = 0,
            stop_event: threading.Event | None = None, controls=None,
            windscribe: bool = False, windscribe_location: str = "best",
            direct_interface: str | None = None, skip_route_check: bool = False,
            emit=None) -> None:
        stop_event = stop_event or threading.Event()
        emit = emit or (lambda message: log.info("%s", message))

        own_sessions: list = []
        if windscribe:
            routes = open_windscribe_routes(
                windscribe_location, direct_interface=direct_interface,
                skip_route_check=skip_route_check, emit=emit,
            )
            own_sessions = [route.session for route in routes]
            seed_session = routes[0].session
        else:
            # One shared session, `workers` threads pulling from the frontier.
            routes = [Route("direct", session) for _ in range(self.workers)]
            seed_session = session

        try:
            self.seed(seed_session)
            log.info("Seeded frontier: %d chain, %d rec candidates "
                     "(targets: %s; routes: %s)",
                     len(self.chain_queue), len(self.rec_queue),
                     ",".join(sorted(self.targets)),
                     ", ".join(route.label for route in routes))

            threads = [
                threading.Thread(
                    target=self._worker,
                    kwargs=dict(route=route, limit=limit, stop_event=stop_event),
                    name=f"crawl-{index}-{route.label}",
                )
                for index, route in enumerate(routes)
            ]
            try:
                for thread in threads:
                    thread.start()
                for thread in threads:
                    while thread.is_alive():
                        thread.join(0.5)
            except KeyboardInterrupt:
                stop_event.set()
                for thread in threads:
                    thread.join()
                raise
        finally:
            self.checkpoint()
            for sess in own_sessions:
                sess.close()


def crawl(categories: list[str] | None = None, *, pages: int = 0, delay: float = 0.1,
          workers: int = 1, refresh: bool = False, limit: int = 0,
          rec_depth: int = RECOMMENDATION_BFS_DEPTH,
          stop_event: threading.Event | None = None, controls=None,
          windscribe: bool = False, windscribe_location: str = "best",
          direct_interface: str | None = None, skip_route_check: bool = False,
          emit=None) -> dict:
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
        crawler.run(
            session, limit=limit, stop_event=stop_event, controls=controls,
            windscribe=windscribe, windscribe_location=windscribe_location,
            direct_interface=direct_interface, skip_route_check=skip_route_check,
            emit=emit,
        )
    finally:
        session.close()
    return {
        "total_metadata": crawler.total_metadata,
        "fetched": crawler.fetched,
        "not_found": crawler.not_found,
        "errors": crawler.errors,
        "skipped": crawler.skipped,
    }
