#!/usr/bin/env python3
"""Library and catalogue reports.

Subcommands:
  catalogue    Per-category catalogue, metadata, and downloaded counts
  size         Downloaded disk usage per category
  incomplete   Downloaded files containing failed-page markers
  chains       Prev/next link continuity analysis from the crawl graph

Examples:
  python report.py catalogue
  python report.py catalogue --category gl,yanqing --write
  python report.py size
  python report.py incomplete --urls reports/incomplete_urls.txt
  python report.py chains --category gl
"""
from __future__ import annotations

import argparse
from pathlib import Path

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


def cmd_catalogue(args) -> int:
    from webnovel.reports import catalogue_report, write_report

    text = catalogue_report(parse_categories(args.category))
    print(text)
    if args.write:
        print(f"\nWrote {write_report('catalogue_summary.txt', text)}")
    return 0


def cmd_size(args) -> int:
    from webnovel.reports import size_report

    print(size_report(parse_categories(args.category)))
    return 0


def cmd_incomplete(args) -> int:
    from webnovel.reports import incomplete_report

    text, urls = incomplete_report(parse_categories(args.category))
    print(text)
    if args.urls:
        path = Path(args.urls)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(urls) + ("\n" if urls else ""), encoding="utf-8")
        print(f"\nWrote {len(urls)} URL(s) to {path}")
    return 0


def cmd_chains(args) -> int:
    from dataclasses import asdict

    from recsys.catalog import load_catalog
    from recsys.store import load_category
    from scripts.analyze_catalogue_chains import (
        build_chains,
        merge_display_chains,
        render_report,
    )

    for category in parse_categories(args.category):
        # The chain analyser works on plain dicts: catalogue records (prev/next,
        # fetch_status) enriched with title/date from the metadata store.
        metadata = load_category(category)
        by_url: dict[str, dict] = {}
        for url, record in load_catalog(category).items():
            item = asdict(record)
            meta = metadata.get(url)
            if meta:
                item.update(
                    title=meta.title,
                    author=meta.author,
                    upload_date=meta.upload_date,
                )
            by_url[url] = item
        if not by_url:
            print(f"[{category}] no catalogue entries; run scrape_metadata.py first.\n")
            continue
        live = {url: rec for url, rec in by_url.items() if rec.get("fetch_status", "ok") == "ok"}
        strict = build_chains(by_url, live)
        chains = merge_display_chains(strict, by_url)
        print(render_report(chains, by_url, live, strict_chain_count=len(strict), title=category))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="report.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    catalogue = sub.add_parser(
        "catalogue", help="Per-category coverage counts",
        description="Show catalogue, metadata, and downloaded counts per category.",
    )
    catalogue.add_argument("--category", action="append", metavar="CATS",
                           help="Category, comma list, or 'all' (default: all)")
    catalogue.add_argument("--write", action="store_true",
                           help="Also write reports/catalogue_summary.txt")
    catalogue.set_defaults(func=cmd_catalogue)

    size = sub.add_parser(
        "size", help="Downloaded disk usage",
        description="Show per-category and total downloaded disk usage.",
    )
    size.add_argument("--category", action="append", metavar="CATS",
                      help="Category, comma list, or 'all' (default: all)")
    size.set_defaults(func=cmd_size)

    incomplete = sub.add_parser(
        "incomplete", help="Files with failed pages",
        description="List downloaded files that contain failed-page markers.",
    )
    incomplete.add_argument("--category", action="append", metavar="CATS",
                            help="Category, comma list, or 'all' (default: all)")
    incomplete.add_argument("--urls", metavar="PATH",
                            help="Write the affected source URLs to this file")
    incomplete.set_defaults(func=cmd_incomplete)

    chains = sub.add_parser(
        "chains", help="Prev/next chain continuity",
        description="Analyse previous/next link continuity in each category's crawl "
        "graph and print the chain report (unbroken segments, confirmed-404 and "
        "non-reciprocal boundaries, merged display chains).",
    )
    chains.add_argument("--category", action="append", metavar="CATS",
                        help="Category, comma list, or 'all' (default: all)")
    chains.set_defaults(func=cmd_chains)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
