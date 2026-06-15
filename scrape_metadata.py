#!/usr/bin/env python3
"""
Crawl novel metadata (synopsis + tags, no chapter text) across 52shuku categories.

Discovers novels via prev/next chains and cross-category recommendation links,
writing per-category <category>/metadata.jsonl (recommender records) and
<category>/_catalog.jsonl (crawl graph + 404s, for resume). By default it crawls
all categories and skips novels already in the store (e.g. the downloaded GL
corpus); use --refresh to re-fetch them.

Usage:
    python scrape_metadata.py                       # all categories
    python scrape_metadata.py --category yanqing,bl
    python scrape_metadata.py --category yanqing --pages 2 --limit 500
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent / "scripts"))
from repo_paths import CATEGORIES  # noqa: E402

from recsys.crawl import crawl  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("scrape_metadata")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--category", help="Comma-separated categories (default: all). "
                   f"Known: {','.join(CATEGORIES)}")
    p.add_argument("--pages", type=int, default=0,
                   help="Also fetch the first N reading pages as an excerpt (default 0)")
    p.add_argument("--limit", type=int, default=0,
                   help="Stop after fetching N new landings (0 = unlimited)")
    p.add_argument("--delay", type=float, default=0.4,
                   help="Base seconds between fetch batches (jittered)")
    p.add_argument("--workers", type=int, default=1,
                   help="Concurrent landing fetches per batch")
    p.add_argument("--refresh", action="store_true",
                   help="Re-fetch novels already in the store / ledger")
    args = p.parse_args()
    if args.pages < 0 or args.limit < 0 or args.workers < 1:
        p.error("--pages/--limit must be >= 0 and --workers >= 1")
    return args


def main() -> int:
    args = parse_args()
    cats = ([c.strip() for c in args.category.split(",") if c.strip()]
            if args.category else list(CATEGORIES))
    unknown = [c for c in cats if c not in CATEGORIES]
    if unknown:
        log.warning("Unknown categories (will still try): %s", ",".join(unknown))

    stop_event = threading.Event()
    try:
        stats = crawl(cats, pages=args.pages, delay=args.delay, workers=args.workers,
                      refresh=args.refresh, limit=args.limit, stop_event=stop_event)
    except KeyboardInterrupt:
        stop_event.set()
        log.warning("Interrupted — checkpoint written; re-run to resume.")
        return 130
    log.info("Done: %d fetched, %d not_found, %d errors, %d skipped",
             stats["fetched"], stats["not_found"], stats["errors"], stats["skipped"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
