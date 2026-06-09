#!/usr/bin/env python3
"""
estimate_history.py — build a canonical list of every GL novel by crawling the
catalogue graph, then emit a comprehensive report (date span, per-year counts,
URL schemes, deletion gaps, and MISSING segments / broken chains).

Why a graph crawl (not a linear 上一篇 walk):
  The chain alone cannot cross a deleted novel in the MODERN /gl/DD_b/ scheme —
  the shard is the upload day, so the chronological predecessor lives in a
  different shard and an in-shard id-probe can never reach it. So we treat every
  page's 上一篇 / 下一篇 AND its recommendation block ("相关推荐", which links to
  novels in OTHER shards/days) as graph edges, and BFS the whole component.
    - prev/next walk each contiguous chain segment (both directions).
    - recommendations hop ACROSS deletion gaps to other segments.
    - a small same-shard id-probe bridges tiny same-day gaps.
  Chain links are always exhausted before recommendation links are considered.
  Recommendation links are then explored breadth-first up to
  RECOMMENDATION_BFS_DEPTH.

Resumable: --seed-map accepts a url_map.json (inspect_urls.py) or a prior
gl_catalog.json; those novels seed the crawl (never re-fetched) and their
prev/next/recommendation frontiers are enqueued. Confirmed 404 URLs are stored
in the catalogue and are never requested again.

Usage:
    python scripts/estimate_history.py <start_url> [--budget N] [--delay 0.15] ...
    python scripts/estimate_history.py --seed-map data/url_map.json
    python scripts/estimate_history.py --seed-map data/gl_catalog.json
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from collections import deque
from datetime import date
from pathlib import Path
from urllib.parse import urljoin, urlparse

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

from analyze_catalogue_chains import build_chains
from repo_paths import DATA_DIR, REPORTS_DIR, resolve_data_input
from scraper import fetch, parse_landing, step_url_id, _b62_to_int

_DATE_RE = re.compile(r"(\d{4})年(\d{2})月(\d{2})日")
RECOMMENDATION_BFS_DEPTH = 3
PROBE_404_DELAY = 1.0


# ── URL helpers ──────────────────────────────────────────────────────────────

def parse_url_parts(url: str) -> tuple[str | None, str | None, str | None]:
    """(category, shard, id) from any /gl/... URL shape."""
    segs = [s for s in urlparse(url).path.split("/") if s]
    if not segs:
        return None, None, None
    category = segs[0]
    ident = segs[-1][:-5] if segs[-1].endswith(".html") else segs[-1]
    shard = segs[-2] if len(segs) >= 3 else None
    return category, shard, ident


def is_gl_landing_url(url: str | None) -> bool:
    """True for a GL novel landing page. Rejects index pages, chapter pages
    (_N.html), and non-/gl/ links (e.g. /zuozhe/ author pages)."""
    if not url:
        return False
    path = urlparse(url).path
    if not path.startswith("/gl/"):
        return False
    m = re.search(r"/([^/]+)\.html$", path)
    if not m:
        return False
    name = m.group(1)
    if name.startswith("index"):
        return False
    if re.search(r"_\d+$", name):   # chapter page like bkec8_2
        return False
    return True


def scheme_of(shard: str | None, ident: str) -> str:
    if shard and re.fullmatch(r"\d{2}_[a-z]", shard):
        return "modern (DD_b/base62)"
    if shard and re.fullmatch(r"[a-z]", shard):
        return "mid (/gl/b/base62)"
    if shard is None and ident.isdigit():
        return "old (/gl/numeric)"
    return "other"


def year_month(upload_date: str) -> tuple[str, str] | None:
    m = _DATE_RE.search(upload_date)
    return (m.group(1), m.group(2)) if m else None


def parse_date(upload_date: str | None) -> date | None:
    m = _DATE_RE.search(upload_date or "")
    if not m:
        return None
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    except ValueError:
        return None


def normalize_title_author(title: str | None, author: str | None) -> tuple[str, str]:
    """Recover combined 'title_author' values from legacy seed records."""
    clean_title = (title or "").strip()
    clean_author = (author or "").strip()
    if not clean_author and "_" in clean_title:
        clean_title, clean_author = (
            part.strip() for part in clean_title.split("_", 1)
        )
    return clean_title, clean_author


def record_of(url: str, m, recommendation_urls: set[str] | None = None,
              recommendation_depth: int = 0) -> dict:
    category, shard, ident = parse_url_parts(url)
    title, author = normalize_title_author(m.title, m.author)
    return {
        "url": url,
        "fetch_status": "ok",
        "title": title,
        "author": author,
        "upload_date": m.upload_date,
        "category": category,
        "shard": shard,
        "id": ident,
        "id_num": _b62_to_int(ident) if ident else None,
        "scheme": scheme_of(shard, ident or ""),
        "prev_url": m.prev_url,
        "next_url": m.next_url,
        "recommendation_urls": sorted(recommendation_urls or ()),
        "recommendation_depth": recommendation_depth,
        "recommendations_crawled": True,
    }


def not_found_record(url: str) -> dict:
    category, shard, ident = parse_url_parts(url)
    return {
        "url": url,
        "fetch_status": "not_found",
        "title": None,
        "author": None,
        "upload_date": None,
        "category": category,
        "shard": shard,
        "id": ident,
        "id_num": _b62_to_int(ident) if ident else None,
        "scheme": scheme_of(shard, ident or ""),
        "prev_url": None,
        "next_url": None,
        "recommendation_urls": [],
        "recommendation_depth": None,
        "recommendations_crawled": True,
    }


def parse_recommendations(html: str, base_url: str) -> set[str]:
    """Extract GL novel URLs from the page's recommendation block. These link to
    novels in other shards/days, so they bridge deletion gaps the chain can't."""
    soup = BeautifulSoup(html, "lxml")
    out: set[str] = set()
    for a in soup.select("[class*=relate] a[href]"):
        absu = urljoin(base_url, a.get("href", ""))
        if is_gl_landing_url(absu):
            out.add(absu)
    return out


def adapt_seed_record(rec: dict) -> dict | None:
    """Normalise a seed record (gl_catalog.json shape with 'url', or
    inspect_urls.py url_map.json shape with 'source_url'+'source') into catalogue
    format. None if no usable URL."""
    if rec.get("url"):
        out = dict(rec)
        had_recommendations = "recommendation_urls" in out
        out["title"], out["author"] = normalize_title_author(
            out.get("title"),
            out.get("author"),
        )
        out.setdefault("fetch_status", "ok")
        out.setdefault("recommendation_urls", [])
        out.setdefault("recommendation_depth", 0 if out["fetch_status"] == "ok" else None)
        out.setdefault("recommendations_crawled", had_recommendations)
        return out
    url = rec.get("source_url")
    if not url:
        return None
    src = rec.get("source") or {}
    shard, ident = src.get("shard"), src.get("id")
    title, author = normalize_title_author(
        rec.get("title"),
        rec.get("author"),
    )
    return {
        "url": url,
        "fetch_status": "ok",
        "title": title,
        "author": author,
        "upload_date": rec.get("upload_date", ""),
        "category": src.get("category"),
        "shard": shard,
        "id": ident,
        "id_num": src.get("id_num"),
        "scheme": scheme_of(shard, ident or ""),
        "prev_url": rec.get("prev_url"),
        "next_url": rec.get("next_url"),
        "recommendation_urls": [],
        "recommendation_depth": 0,
        "recommendations_crawled": False,
    }


def write_catalog(path: str, records: list[dict]) -> int:
    by_url = {r["url"]: r for r in records}
    catalog = sorted(
        by_url.values(),
        key=lambda r: (
            r.get("fetch_status") != "ok",
            r.get("upload_date") or "",
            r["url"],
        ),
    )
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)
    return len(catalog)


def bridge(session, dead_url: str, records_by_url: dict[str, dict],
           prefetched: dict[str, object], max_steps: int = 30,
           delay: float = 0.15, on_not_found=None):
    """Probe older IDs within one shard, persisting every confirmed 404.

    A live response is cached so the caller does not request it twice.
    """
    for k in range(1, max_steps + 1):
        cand = step_url_id(dead_url, -k)
        if cand is None:
            return None, k
        known = records_by_url.get(cand)
        if known:
            if is_live_record(known):
                return cand, k
            continue
        try:
            resp = fetch(session, cand, max_retries=2)
            prefetched[cand] = resp
            return cand, k
        except FileNotFoundError:
            records_by_url[cand] = not_found_record(cand)
            if on_not_found:
                on_not_found()
            time.sleep(max(delay, PROBE_404_DELAY))
            continue
        except Exception:
            time.sleep(delay)
            continue
    return None, max_steps


# ── Report ───────────────────────────────────────────────────────────────────

def is_live_record(record: dict) -> bool:
    return record.get("fetch_status", "ok") == "ok"


def follows_chain(records_by_url: dict[str, dict], start_url: str, target_url: str,
                  direction: str) -> bool:
    """Return True if target is reachable through known prev/next links."""
    field = "next_url" if direction == "next" else "prev_url"
    seen: set[str] = set()
    current = start_url
    while current and current not in seen:
        if current == target_url:
            return True
        seen.add(current)
        record = records_by_url.get(current)
        if not record or not is_live_record(record):
            return False
        current = record.get(field)
    return False


def build_report(records: list[dict], stop_reason: str, gaps: list[dict],
                 elapsed: float, avg_mb: float, gap_days: int = 7) -> str:
    lines: list[str] = []
    def p(s=""): lines.append(s)

    p("=" * 70)
    p("52shuku GL catalogue — history report")
    p("=" * 70)

    records_by_url = {r["url"]: r for r in records}
    live_records = [r for r in records if is_live_record(r)]
    not_found_records = [r for r in records if not is_live_record(r)]

    if not live_records:
        p("No novels recorded.")
        p(f"Confirmed 404 URLs: {len(not_found_records)}")
        p(f"Walk wall-time    : {elapsed:.0f}s")
        p(f"Stop reason       : {stop_reason}")
        p("=" * 70)
        return "\n".join(lines)

    by_date = sorted(live_records, key=lambda r: r.get("upload_date") or "")
    oldest, newest = by_date[0], by_date[-1]

    p(f"Catalogue entries : {len(records)}")
    p(f"Live novels       : {len(live_records)}")
    p(f"Confirmed 404 URLs: {len(not_found_records)}")
    p(f"Newest novel      : {newest['upload_date']}  {newest['title']}")
    p(f"Oldest novel      : {oldest['upload_date']}  {oldest['title']}")

    ym_new = year_month(newest.get("upload_date") or "")
    ym_old = year_month(oldest.get("upload_date") or "")
    if ym_new and ym_old:
        months = (int(ym_new[0]) - int(ym_old[0])) * 12 + (int(ym_new[1]) - int(ym_old[1]))
        p(f"Span              : ~{months} months ({months/12:.1f} years)")

    p(f"Walk wall-time    : {elapsed:.0f}s")
    p(f"Stop reason       : {stop_reason}")
    p("")

    p("Novels per year (by upload date):")
    per_year: dict[str, int] = {}
    for r in live_records:
        ym = year_month(r.get("upload_date") or "")
        if ym:
            per_year[ym[0]] = per_year.get(ym[0], 0) + 1
    for yr in sorted(per_year):
        bar = "█" * round(per_year[yr] / max(per_year.values()) * 40)
        p(f"  {yr}: {per_year[yr]:5d}  {bar}")
    p("")

    p("URL schemes:")
    per_scheme: dict[str, int] = {}
    for r in live_records:
        per_scheme[r["scheme"]] = per_scheme.get(r["scheme"], 0) + 1
    for sch, count in sorted(per_scheme.items(), key=lambda kv: -kv[1]):
        p(f"  {sch:28s}: {count:5d}")
    p("")

    breaks = [
        r for r in live_records
        if r.get("prev_url") and r["prev_url"] not in records_by_url
    ]
    breaks.sort(key=lambda r: r.get("upload_date") or "")
    p(f"Chain breaks (上一篇 missing from catalogue): {len(breaks)}")
    for r in breaks[:25]:
        p(f"  {r['upload_date']}  {(r['title'] or '')[:22]:22s}  → missing {r['prev_url']}")
    if len(breaks) > 25:
        p(f"  … and {len(breaks) - 25} more")
    p("")

    known_deletions = [
        r for r in live_records
        if r.get("prev_url")
        and (prev := records_by_url.get(r["prev_url"]))
        and not is_live_record(prev)
    ]
    known_deletions.sort(key=lambda r: r.get("upload_date") or "")
    p(f"Known deleted 上一篇 links (recorded 404): {len(known_deletions)}")
    for r in known_deletions[:25]:
        p(f"  {r['upload_date']}  {(r['title'] or '')[:22]:22s}  → 404 {r['prev_url']}")
    if len(known_deletions) > 25:
        p(f"  … and {len(known_deletions) - 25} more")
    p("")

    # Old pages sometimes carry upload dates inconsistent with chain order.
    # Suppress a date gap when its boundaries are connected through the known
    # next/prev chain, even if an intermediate record sorts to another year.
    dated_records = sorted(
        ((d, r) for r in live_records if (d := parse_date(r.get("upload_date")))),
        key=lambda item: (item[0], item[1]["upload_date"], item[1]["url"]),
    )
    big = [
        (before_date, after_date, (after_date - before_date).days, before, after)
        for (before_date, before), (after_date, after)
        in zip(dated_records, dated_records[1:])
        if (after_date - before_date).days > gap_days
        and not follows_chain(records_by_url, before["url"], after["url"], "next")
        and not follows_chain(records_by_url, after["url"], before["url"], "prev")
    ]
    p(f"Date-coverage gaps (>{gap_days} days and no catalogue chain path): {len(big)}")
    for before_date, after_date, days, before, after in big:
        p(f"  {before_date} → {after_date}   ({days} days empty)")
        p(f"    before: {before['upload_date']}  {before['title']}")
        p(f"            {before['url']}")
        p(f"    after : {after['upload_date']}  {after['title']}")
        p(f"            {after['url']}")
    p("")

    dated_by_url = {
        r["url"]: parse_date(r.get("upload_date"))
        for r in live_records
    }
    inversions: list[tuple[dict, dict]] = []
    for r in live_records:
        next_record = records_by_url.get(r.get("next_url"))
        if not next_record or not is_live_record(next_record):
            continue
        current_date = dated_by_url.get(r["url"])
        next_date = dated_by_url.get(next_record["url"])
        if current_date and next_date and next_date < current_date:
            inversions.append((r, next_record))
    p(f"Chain date inversions (下一篇 has an older upload date): {len(inversions)}")
    for before, after in inversions[:25]:
        p(f"  {before['upload_date']}  {before['url']}")
        p(f"    → {after['upload_date']}  {after['url']}")
    if len(inversions) > 25:
        p(f"  … and {len(inversions) - 25} more")
    p("")

    p(f"Same-shard gaps bridged: {len(gaps)}")
    if gaps:
        for gap in gaps[:10]:
            p(f"  -{gap['steps']:>3} at {gap['dead']} → {gap['live']}")
        if len(gaps) > 10:
            p(f"  … and {len(gaps) - 10} more")
    p("")

    est_gb = len(live_records) * avg_mb / 1024
    p(f"Estimated disk size: ~{est_gb:.1f} GB  (at {avg_mb:.1f} MB/novel avg)")
    p("=" * 70)
    return "\n".join(lines)


# ── Main: graph crawl ──────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("start_url", nargs="?", help="Seed URL for the crawl (optional if --seed-map given)")
    ap.add_argument("--seed-map", help="data/url_map.json or data/gl_catalog.json of already-known novels")
    ap.add_argument("--budget", type=int, default=20000, help="Max NEW novels to fetch this run")
    ap.add_argument("--delay", type=float, default=0.15)
    ap.add_argument("--json", default=str(DATA_DIR / "gl_catalog.json"))
    ap.add_argument("--report", default=str(REPORTS_DIR / "gl_catalog_report.txt"))
    ap.add_argument("--avg-mb", type=float, default=1.2)
    ap.add_argument(
        "--bridge-steps",
        type=int,
        default=0,
        help="Optional same-shard ID probe budget after a confirmed 404 "
             "(default: 0; disabled to avoid 404-heavy request bursts)",
    )
    ap.add_argument(
        "--recommendation-depth",
        type=int,
        default=RECOMMENDATION_BFS_DEPTH,
        help=f"Recommendation BFS depth (default: {RECOMMENDATION_BFS_DEPTH})",
    )
    ap.add_argument(
        "--prime",
        type=int,
        default=1,
        help="When a seed catalogue has no stored recommendation edges, use this "
             "many oldest live novels as BFS roots (default: 1)",
    )
    args = ap.parse_args()

    if not args.start_url and not args.seed_map:
        ap.error("provide a start_url or --seed-map")
    if args.budget < 0:
        ap.error("--budget must be 0 or greater")
    if args.recommendation_depth < 0:
        ap.error("--recommendation-depth must be 0 or greater")
    if args.prime < 0:
        ap.error("--prime must be 0 or greater")

    session = cffi_requests.Session()
    records_by_url: dict[str, dict] = {}
    gaps: list[dict] = []
    chain_queue: deque[tuple[str, int, bool]] = deque()
    recommendation_queue: deque[tuple[str, int]] = deque()
    root_queue: deque[tuple[str, int]] = deque()
    chain_scheduled: dict[str, int] = {}
    recommendation_scheduled: dict[str, int] = {}
    prefetched: dict[str, object] = {}
    attempted_this_run: set[str] = set()
    boundary_targets: set[str] = set()
    refresh_targets: set[str] = set()
    fetched = 0
    t0 = time.monotonic()
    stop_reason = "exhausted (chain first, then recommendation BFS)"
    rec_hops = 0
    interrupted = False

    def checkpoint() -> None:
        write_catalog(args.json, list(records_by_url.values()))

    def enqueue_chain(u: str | None, depth: int, refresh: bool = False) -> None:
        if not is_gl_landing_url(u):
            return
        known = records_by_url.get(u)
        if known:
            if not refresh or not is_live_record(known):
                return
        elif u in attempted_this_run:
            return
        previous_depth = chain_scheduled.get(u)
        if previous_depth is not None and previous_depth <= depth:
            return
        chain_scheduled[u] = depth
        chain_queue.append((u, depth, refresh))

    def enqueue_recommendation(u: str | None, depth: int) -> None:
        if depth > args.recommendation_depth or not is_gl_landing_url(u):
            return
        known = records_by_url.get(u)
        if known and (not is_live_record(known) or known.get("recommendations_crawled")):
            return
        if not known and u in attempted_this_run:
            return
        previous_depth = recommendation_scheduled.get(u)
        if previous_depth is not None and previous_depth <= depth:
            return
        recommendation_scheduled[u] = depth
        recommendation_queue.append((u, depth))

    def enqueue_record_edges(record: dict) -> None:
        if not is_live_record(record):
            return
        depth = record.get("recommendation_depth")
        if not isinstance(depth, int):
            depth = 0
        enqueue_chain(record.get("prev_url"), depth)
        enqueue_chain(record.get("next_url"), depth)
        if record.get("recommendations_crawled") and depth < args.recommendation_depth:
            for url in record.get("recommendation_urls") or ():
                enqueue_recommendation(url, depth + 1)

    try:
        # ── Seed ────────────────────────────────────────────────────────────
        if args.seed_map:
            seed_map = resolve_data_input(args.seed_map)
            with seed_map.open(encoding="utf-8") as f:
                raw = json.load(f)
            seeded = 0
            for rec in raw:
                a = adapt_seed_record(rec)
                if a:
                    records_by_url[a["url"]] = a
                    seeded += 1
            log.info("Seeded %d known novels from %s", seeded, seed_map)

            live_records = {
                url: record
                for url, record in records_by_url.items()
                if is_live_record(record)
            }
            chains = build_chains(records_by_url, live_records)
            for chain in chains:
                for side in ("start", "end"):
                    boundary_info = chain[f"{side}_boundary"]
                    if boundary_info["kind"] == "missing_from_catalogue":
                        target = boundary_info["target_url"]
                        boundary_targets.add(target)
                        enqueue_chain(target, 0)

            # The newest chain end legitimately has next_url=null today, but it
            # may gain a next link after new uploads. Refresh it once per run.
            newest_chain_end = max(
                (
                    chain["end"]
                    for chain in chains
                    if chain["end_boundary"]["kind"] == "chain_end"
                ),
                key=lambda record: (record.get("upload_date") or "", record["url"]),
                default=None,
            )
            if newest_chain_end:
                refresh_targets.add(newest_chain_end["url"])
                enqueue_chain(newest_chain_end["url"], 0, refresh=True)
                log.info("Will refresh newest chain end: %s", newest_chain_end["url"])

            log.info(
                "Analysed %d chain(s): queued %d missing boundary URL(s)",
                len(chains),
                len(boundary_targets),
            )

            if args.prime:
                uncrawled = sorted(
                    (
                        r for r in records_by_url.values()
                        if is_live_record(r) and not r.get("recommendations_crawled")
                    ),
                    key=lambda r: (r.get("upload_date") or "", r["url"]),
                )
                for record in uncrawled[:args.prime]:
                    root_queue.append((record["url"], 0))
                    refresh_targets.add(record["url"])
                if root_queue:
                    log.info(
                        "Queued %d known novel(s) as recommendation BFS root(s)",
                        len(root_queue),
                    )
        if args.start_url:
            start_url = args.start_url.strip()
            if start_url in records_by_url:
                root_queue.appendleft((start_url, 0))
                enqueue_record_edges(records_by_url[start_url])
            else:
                enqueue_chain(start_url, 0)

        log.info(
            "Crawl start: %d chain frontier(s), %d recommendation candidate(s), "
            "%d catalogue entries, budget=%d new, recommendation depth=%d",
            len(chain_queue),
            len(recommendation_queue),
            len(records_by_url),
            args.budget,
            args.recommendation_depth,
        )

        while True:
            if not chain_queue:
                while root_queue and not chain_queue:
                    root_url, root_depth = root_queue.popleft()
                    enqueue_chain(root_url, root_depth, refresh=True)
                    if chain_queue:
                        log.info("Chain exhausted; harvesting BFS root %s", root_url)

                while recommendation_queue and not chain_queue:
                    rec_url, rec_depth = recommendation_queue.popleft()
                    if recommendation_scheduled.get(rec_url) != rec_depth:
                        continue
                    rec_hops += 1
                    known = records_by_url.get(rec_url)
                    enqueue_chain(
                        rec_url,
                        rec_depth,
                        refresh=bool(known and is_live_record(known)),
                    )
                    if chain_queue:
                        log.info(
                            "Chain exhausted; recommendation BFS hop #%d depth=%d → %s "
                            "(%d queued)",
                            rec_hops,
                            rec_depth,
                            rec_url,
                            len(recommendation_queue),
                        )

                if not chain_queue:
                    break

            cur, depth, refresh = chain_queue.popleft()
            if chain_scheduled.get(cur) != depth:
                continue

            known = records_by_url.get(cur)
            if known and (not refresh or not is_live_record(known)):
                continue
            if not known and fetched >= args.budget:
                stop_reason = f"budget exhausted ({args.budget} new novels)"
                break

            attempted_this_run.add(cur)
            was_known = known is not None
            try:
                resp = prefetched.pop(cur, None)
                if resp is None:
                    # Missing chain boundaries are checked once per run. Long
                    # exponential retries make a transient error look like a
                    # stall and can amplify rate limiting.
                    single_attempt = cur in boundary_targets or cur in refresh_targets
                    max_retries = 1 if single_attempt else 3
                    timeout = 8 if single_attempt else 20
                    resp = fetch(
                        session,
                        cur,
                        max_retries=max_retries,
                        timeout=timeout,
                    )
            except FileNotFoundError:
                records_by_url[cur] = not_found_record(cur)
                checkpoint()
                older_refs = [
                    r["url"] for r in records_by_url.values()
                    if is_live_record(r) and r.get("next_url") == cur
                ]
                newer_refs = [
                    r["url"] for r in records_by_url.values()
                    if is_live_record(r) and r.get("prev_url") == cur
                ]
                accounted_for = bool(older_refs or newer_refs)
                log.warning(
                    "404 recorded in catalogue: %s (older-side refs=%d, newer-side refs=%d)",
                    cur,
                    len(older_refs),
                    len(newer_refs),
                )
                if older_refs:
                    log.info("404 older-side chain endpoint(s): %s", ", ".join(older_refs))
                if newer_refs:
                    log.info("404 newer-side chain endpoint(s): %s", ", ".join(newer_refs))
                if accounted_for:
                    log.info("404 boundary is represented by existing catalogue links; no ID probe.")
                elif args.bridge_steps:
                    bridged, tried = bridge(
                        session,
                        cur,
                        records_by_url,
                        prefetched,
                        max_steps=args.bridge_steps,
                        delay=args.delay,
                        on_not_found=checkpoint,
                    )
                    if bridged:
                        gaps.append({"dead": cur, "live": bridged, "steps": tried})
                        enqueue_chain(bridged, depth)
                        log.warning("404 gap at %s; bridged -%d to %s", cur, tried, bridged)
                continue
            except Exception:
                log.exception("Error fetching %s; leaving it unconfirmed for a future run", cur)
                continue

            m = parse_landing(resp.text, cur)
            recommendation_urls = parse_recommendations(resp.text, cur)
            record = record_of(cur, m, recommendation_urls, depth)
            records_by_url[cur] = record
            if not was_known:
                fetched += 1

            enqueue_record_edges(record)

            rate = fetched / max(time.monotonic() - t0, 0.001)
            log.info(
                "[+%5d | %6d live, %5d 404] depth=%d  %s  %s  "
                "(%.1f/s, chain=%d, rec=%d)  %s",
                fetched,
                sum(is_live_record(r) for r in records_by_url.values()),
                sum(not is_live_record(r) for r in records_by_url.values()),
                depth,
                m.upload_date,
                m.title[:34],
                rate,
                len(chain_queue),
                len(recommendation_queue),
                cur,
            )

            if fetched and fetched % 200 == 0:
                checkpoint()

            time.sleep(args.delay)
    except KeyboardInterrupt:
        interrupted = True
        stop_reason = f"keyboard interrupt ({fetched} new novels fetched this run)"
        log.warning(
            "Interrupted; writing %d collected catalogue entries and report",
            len(records_by_url),
        )
    finally:
        session.close()

    elapsed = time.monotonic() - t0
    records = list(records_by_url.values())
    n_catalog = write_catalog(args.json, records)
    report = build_report(records, stop_reason, gaps, elapsed, args.avg_mb)
    report_path = Path(args.report)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    with report_path.open("w", encoding="utf-8") as f:
        f.write(report + "\n")

    log.info(
        "Crawl finished: +%d live this run, %d catalogue entries in %.1fs, "
        "%d recommendation hops (%s)",
        fetched,
        n_catalog,
        elapsed,
        rec_hops,
        stop_reason,
    )
    print("\n" + report, flush=True)
    print(f"\nCanonical list → {args.json}  ({n_catalog} novels)", flush=True)
    print(f"Report         → {args.report}", flush=True)
    return 130 if interrupted else 0


if __name__ == "__main__":
    sys.exit(main())
