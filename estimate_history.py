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
  We stop when the queue AND the recommendation pool are exhausted, or --budget
  new novels are fetched.

Resumable: --seed-map accepts a url_map.json (inspect_urls.py) or a prior
gl_catalog.json; those novels seed the crawl (never re-fetched) and their
prev/next frontiers are enqueued. The catalogue is saved incrementally.

Usage:
    python estimate_history.py <start_url> [--budget N] [--delay 0.15] ...
    python estimate_history.py --seed-map url_map.json
    python estimate_history.py --seed-map gl_catalog.json   # resume a prior crawl
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
from urllib.parse import urljoin, urlparse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

from scraper import fetch, parse_landing, step_url_id, _b62_to_int

_DATE_RE = re.compile(r"(\d{4})年(\d{2})月(\d{2})日")


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


def record_of(url: str, m) -> dict:
    category, shard, ident = parse_url_parts(url)
    return {
        "url": url,
        "title": m.title,
        "author": m.author,
        "upload_date": m.upload_date,
        "category": category,
        "shard": shard,
        "id": ident,
        "id_num": _b62_to_int(ident) if ident else None,
        "scheme": scheme_of(shard, ident or ""),
        "prev_url": m.prev_url,
        "next_url": m.next_url,
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
        return rec
    url = rec.get("source_url")
    if not url:
        return None
    src = rec.get("source") or {}
    shard, ident = src.get("shard"), src.get("id")
    return {
        "url": url,
        "title": rec.get("title", ""),
        "author": rec.get("author", ""),
        "upload_date": rec.get("upload_date", ""),
        "category": src.get("category"),
        "shard": shard,
        "id": ident,
        "id_num": src.get("id_num"),
        "scheme": scheme_of(shard, ident or ""),
        "prev_url": rec.get("prev_url"),
        "next_url": rec.get("next_url"),
    }


def write_catalog(path: str, records: list[dict]) -> int:
    by_url = {r["url"]: r for r in records}
    catalog = sorted(by_url.values(), key=lambda r: r["upload_date"])
    with open(path, "w", encoding="utf-8") as f:
        json.dump(catalog, f, ensure_ascii=False, indent=2)
    return len(catalog)


def bridge(session, dead_url, max_steps=30):
    """Same-shard id-probe to cross a tiny (same-day) deletion gap. Returns
    (live_url, steps_tried) or (None, steps_tried). Cross-day gaps are handled by
    recommendations instead, so this budget is small."""
    for k in range(1, max_steps + 1):
        cand = step_url_id(dead_url, -k)
        if cand is None:
            return None, k
        try:
            fetch(session, cand, max_retries=2)
            return cand, k
        except FileNotFoundError:
            continue
        except Exception:
            continue
    return None, max_steps


# ── Report ───────────────────────────────────────────────────────────────────

def build_report(records: list[dict], stop_reason: str, gaps: list[dict],
                 elapsed: float, avg_mb: float, gap_days: int = 7) -> str:
    lines: list[str] = []
    def p(s=""): lines.append(s)

    p("=" * 70)
    p("52shuku GL catalogue — history report")
    p("=" * 70)

    if not records:
        p("No novels recorded.")
        p(f"Walk wall-time    : {elapsed:.0f}s")
        p(f"Stop reason       : {stop_reason}")
        p("=" * 70)
        return "\n".join(lines)

    by_date = sorted(records, key=lambda r: r["upload_date"])
    oldest, newest = by_date[0], by_date[-1]

    p(f"Novels discovered : {len(records)}")
    p(f"Newest novel      : {newest['upload_date']}  {newest['title']}")
    p(f"Oldest novel      : {oldest['upload_date']}  {oldest['title']}")

    ym_new, ym_old = year_month(newest["upload_date"]), year_month(oldest["upload_date"])
    if ym_new and ym_old:
        months = (int(ym_new[0]) - int(ym_old[0])) * 12 + (int(ym_new[1]) - int(ym_old[1]))
        p(f"Span              : ~{months} months ({months/12:.1f} years)")

    p(f"Walk wall-time    : {elapsed:.0f}s")
    p(f"Stop reason       : {stop_reason}")
    p("")

    # Per-year counts
    p("Novels per year (by upload date):")
    per_year: dict[str, int] = {}
    for r in records:
        ym = year_month(r["upload_date"])
        if ym:
            per_year[ym[0]] = per_year.get(ym[0], 0) + 1
    for yr in sorted(per_year):
        bar = "█" * round(per_year[yr] / max(per_year.values()) * 40)
        p(f"  {yr}: {per_year[yr]:5d}  {bar}")
    p("")

    # URL-scheme breakdown
    p("URL schemes:")
    per_scheme: dict[str, int] = {}
    for r in records:
        per_scheme[r["scheme"]] = per_scheme.get(r["scheme"], 0) + 1
    for sch, c in sorted(per_scheme.items(), key=lambda kv: -kv[1]):
        p(f"  {sch:28s}: {c:5d}")
    p("")

    # ── Missing segments / broken chains ─────────────────────────────────────
    # A novel whose 上一篇 is not in our set marks the bottom edge of a found
    # segment — i.e. older novel(s) are missing there. (The single global oldest
    # is excluded: it legitimately points beyond what exists.)
    found = {r["url"] for r in records}
    oldest_url = oldest["url"]
    breaks = [r for r in records
              if r.get("prev_url") and r["prev_url"] not in found and r["url"] != oldest_url]
    breaks.sort(key=lambda r: r["upload_date"])
    p(f"Chain breaks (上一篇 missing → older novels not yet found): {len(breaks)}")
    for r in breaks[:25]:
        p(f"  {r['upload_date']}  {r['title'][:22]:22s}  → missing {r['prev_url']}")
    if len(breaks) > 25:
        p(f"  … and {len(breaks) - 25} more")
    p("")

    # Date-coverage gaps: stretches with no novels at all.
    dates = sorted(d for d in (parse_date(r["upload_date"]) for r in records) if d)
    big = [(a, b, (b - a).days) for a, b in zip(dates, dates[1:]) if (b - a).days > gap_days]
    p(f"Date-coverage gaps (>{gap_days} days with no novels): {len(big)}")
    for a, b, d in big[:25]:
        p(f"  {a} → {b}   ({d} days empty)")
    if len(big) > 25:
        p(f"  … and {len(big) - 25} more")
    p("")

    # Deletion gaps bridged in-shard
    p(f"Same-shard gaps bridged: {len(gaps)}")
    if gaps:
        for g in gaps[:10]:
            p(f"  -{g['steps']:>3} at {g['dead']} → {g['live']}")
        if len(gaps) > 10:
            p(f"  … and {len(gaps) - 10} more")
    p("")

    est_gb = len(records) * avg_mb / 1024
    p(f"Estimated disk size: ~{est_gb:.1f} GB  (at {avg_mb:.1f} MB/novel avg)")
    p("=" * 70)
    return "\n".join(lines)


# ── Main: graph crawl ──────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("start_url", nargs="?", help="Seed URL for the crawl (optional if --seed-map given)")
    ap.add_argument("--seed-map", help="url_map.json or gl_catalog.json of already-known novels")
    ap.add_argument("--budget", type=int, default=20000, help="Max NEW novels to fetch this run")
    ap.add_argument("--delay", type=float, default=0.15)
    ap.add_argument("--json", default="gl_catalog.json")
    ap.add_argument("--report", default="gl_catalog_report.txt")
    ap.add_argument("--avg-mb", type=float, default=1.2)
    ap.add_argument("--bridge-steps", type=int, default=30, help="Same-shard id-probe budget per gap")
    ap.add_argument("--prime", type=int, default=10, help="When seeding from a catalogue (no stored "
                                                          "recommendations), harvest recs from this many oldest known novels")
    args = ap.parse_args()

    if not args.start_url and not args.seed_map:
        ap.error("provide a start_url or --seed-map")

    session = cffi_requests.Session()
    records: list[dict] = []
    visited: set[str] = set()       # fetched or known-dead URLs
    gaps: list[dict] = []
    rec_pool: set[str] = set()      # recommendation URLs seen, not yet visited
    queue: deque[str] = deque()     # chain frontier (prev/next edges)
    fetched = 0
    t0 = time.monotonic()
    stop_reason = "exhausted (chain + recommendations fully explored)"
    rec_hops = 0
    interrupted = False

    def enqueue(u: str | None) -> None:
        if is_gl_landing_url(u) and u not in visited:
            queue.append(u)

    try:
        # ── Seed ────────────────────────────────────────────────────────────
        if args.seed_map:
            with open(args.seed_map, encoding="utf-8") as f:
                raw = json.load(f)
            seeded = 0
            for rec in raw:
                a = adapt_seed_record(rec)
                if a:
                    records.append(a)
                    visited.add(a["url"])
                    seeded += 1
            log.info("Seeded %d known novels from %s", seeded, args.seed_map)
            for a in records:                   # enqueue chain frontiers of known set
                enqueue(a.get("prev_url"))
                enqueue(a.get("next_url"))
            # Catalogue seeds carry no recommendations. Prime the rec pool by
            # harvesting recs from the oldest known novels — most likely to point
            # into the older territory beyond the chain break.
            if args.prime and records:
                oldest_known = sorted(records, key=lambda r: r["upload_date"])[:args.prime]
                log.info("Priming recommendation pool from %d oldest known novels…", len(oldest_known))
                for r in oldest_known:
                    try:
                        resp = fetch(session, r["url"], max_retries=2)
                        for ru in parse_recommendations(resp.text, r["url"]):
                            if ru not in visited:
                                rec_pool.add(ru)
                    except Exception:
                        pass
                    time.sleep(args.delay)
                log.info("Primed recommendation pool: %d candidate(s)", len(rec_pool))
        if args.start_url:
            enqueue(args.start_url.strip())

        log.info("Crawl start: %d queued frontier(s), %d seeded, budget=%d new",
                 len(queue), len(records), args.budget)

        while fetched < args.budget:
            if not queue:
                # Chain frontier drained — hop across a gap via a recommendation.
                nxt = None
                while rec_pool:
                    cand = rec_pool.pop()
                    if cand not in visited:
                        nxt = cand
                        break
                if nxt is None:
                    break
                rec_hops += 1
                log.info("Chain drained — recommendation hop #%d → %s (pool: %d left)",
                         rec_hops, nxt, len(rec_pool))
                queue.append(nxt)
                continue

            cur = queue.popleft()
            if cur in visited:
                continue

            try:
                resp = fetch(session, cur, max_retries=3)
            except FileNotFoundError:
                visited.add(cur)
                bridged, tried = bridge(session, cur, max_steps=args.bridge_steps)
                if bridged and bridged not in visited:
                    gaps.append({"dead": cur, "live": bridged, "steps": tried})
                    queue.append(bridged)
                    log.warning("404 gap at %s; bridged -%d to %s", cur, tried, bridged)
                continue
            except Exception:
                visited.add(cur)
                log.exception("Error fetching %s; skipping", cur)
                continue

            visited.add(cur)
            m = parse_landing(resp.text, cur)
            records.append(record_of(cur, m))
            fetched += 1

            enqueue(m.prev_url)
            enqueue(m.next_url)
            for ru in parse_recommendations(resp.text, cur):
                if ru not in visited:
                    rec_pool.add(ru)

            rate = fetched / (time.monotonic() - t0)
            log.info("[+%5d | %6d total] %s  %s  (%.1f/s, q=%d, rec=%d)  %s",
                     fetched, len(records), m.upload_date, m.title[:34], rate,
                     len(queue), len(rec_pool), cur)

            if fetched % 200 == 0:
                write_catalog(args.json, records)

            time.sleep(args.delay)
        else:
            stop_reason = f"budget exhausted ({args.budget} new novels)"
    except KeyboardInterrupt:
        interrupted = True
        stop_reason = f"keyboard interrupt ({fetched} new novels fetched this run)"
        log.warning("Interrupted; writing %d collected novel(s) and report", len(records))

    elapsed = time.monotonic() - t0
    n_catalog = write_catalog(args.json, records)
    report = build_report(records, stop_reason, gaps, elapsed, args.avg_mb)
    with open(args.report, "w", encoding="utf-8") as f:
        f.write(report + "\n")

    log.info("Crawl finished: +%d new this run, %d total in %.1fs, %d recommendation hops (%s)",
             fetched, n_catalog, elapsed, rec_hops, stop_reason)
    print("\n" + report, flush=True)
    print(f"\nCanonical list → {args.json}  ({n_catalog} novels)", flush=True)
    print(f"Report         → {args.report}", flush=True)
    return 130 if interrupted else 0


if __name__ == "__main__":
    sys.exit(main())
