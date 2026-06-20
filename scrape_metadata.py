#!/usr/bin/env python3
"""Crawl 52shuku metadata and catalogue graphs.

Walks each selected category, recording one recommender record per novel
(``<category>/metadata.jsonl``) and a resumable crawl graph including confirmed
404s (``<category>/_catalog.jsonl``). The crawler prioritises previous/next
chain links and only explores recommendation links once the chain frontier is
exhausted.

Examples:
  # Update every category from its index page and known chain links
  python scrape_metadata.py

  # Only two categories, capped at 200 new landing pages
  python scrape_metadata.py --category yanqing,bl --limit 200

  # Store a bounded opening excerpt from the first two reading pages
  python scrape_metadata.py --category all --pages 2

  # Re-fetch known records (needed once after a parser upgrade)
  python scrape_metadata.py --category yanqing --refresh

  # Roughly double throughput by splitting fetches across the direct route and
  # a Windscribe tunnel (two distinct public IPs)
  python scrape_metadata.py --windscribe --windscribe-location "Singapore - SMRT"
"""
from __future__ import annotations

import argparse
import sys
import threading

from scripts.repo_paths import CATEGORIES


def parse_categories(values: list[str] | None) -> list[str]:
    if not values:
        return list(CATEGORIES)
    result: list[str] = []
    for item in values:
        result.extend(part.strip() for part in item.split(",") if part.strip())
    if "all" in result:
        return list(CATEGORIES)
    return list(dict.fromkeys(result))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scrape_metadata.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--category",
        action="append",
        metavar="CATS",
        help="Category, comma-separated list, or 'all' (repeatable; default: all). "
        f"Choices: {', '.join(CATEGORIES)}",
    )
    parser.add_argument(
        "--pages", type=int, default=0, metavar="N",
        help="Opening reading pages fetched per novel and kept as an excerpt (0 = landing page only)",
    )
    parser.add_argument(
        "--limit", type=int, default=0, metavar="N",
        help="Maximum new landing pages fetched this run (0 = unlimited)",
    )
    parser.add_argument(
        "--delay", type=float, default=0.1, metavar="SECONDS",
        help="Base delay between fetches on each route, lightly jittered (default: 0.1)",
    )
    parser.add_argument(
        "--workers", type=int, default=1, metavar="N",
        help="Concurrent landing-page requests over the direct route (default: 1; "
        "keep low to avoid rate limits). Ignored under --windscribe, which uses "
        "one worker per route.",
    )
    parser.add_argument(
        "--refresh", action="store_true",
        help="Re-fetch and re-parse novels already present in the store",
    )
    parser.add_argument(
        "--recommendation-depth", type=int, default=4, metavar="N",
        dest="rec_depth",
        help="Maximum recommendation BFS depth once the chain frontier is exhausted (default: 4)",
    )

    windscribe = parser.add_argument_group(
        "Windscribe dual-route",
        "Split fetches across the direct route and a Windscribe tunnel (two "
        "public IPs, one worker each) to roughly double throughput. Each route "
        "keeps its own --delay; the shared graph is built under a lock.",
    )
    windscribe.add_argument(
        "--windscribe", action="store_true",
        help="Enable the direct + Windscribe dual-route crawl",
    )
    windscribe.add_argument(
        "--windscribe-location", default="best", metavar="LOC",
        help="Windscribe location to connect (default: best)",
    )
    windscribe.add_argument(
        "--direct-interface", metavar="IFACE",
        help="Force the direct-route network interface (e.g. eth0)",
    )
    windscribe.add_argument(
        "--skip-route-check", action="store_true",
        help="Skip the public-IP comparison before the run",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    categories = parse_categories(args.category)
    unknown = [category for category in categories if category not in CATEGORIES]
    if unknown:
        print(f"Unknown categories: {', '.join(unknown)}", file=sys.stderr)
        return 2

    from recsys.crawl import crawl

    stop_event = threading.Event()
    try:
        stats = crawl(
            categories,
            pages=args.pages,
            delay=args.delay,
            workers=args.workers,
            refresh=args.refresh,
            limit=args.limit,
            rec_depth=args.rec_depth,
            stop_event=stop_event,
            windscribe=args.windscribe,
            windscribe_location=args.windscribe_location,
            direct_interface=args.direct_interface,
            skip_route_check=args.skip_route_check,
            emit=print,
        )
    except KeyboardInterrupt:
        stop_event.set()
        print("\nInterrupted; the latest checkpoint has been written.")
        return 130
    print(
        f"Metadata crawl: {stats['total_metadata']} metadata total, "
        f"{stats['fetched']} fetched this run, {stats['not_found']} not found, "
        f"{stats['errors']} errors, {stats['skipped']} known nodes traversed"
    )
    return 1 if stats["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
