#!/usr/bin/env python3
"""Download full novel text from the crawled catalogue.

Pending work is derived from each category's ``_catalog.jsonl`` (live URLs) minus
the novels already complete on disk. Downloaded files update their local-file
status and bounded metadata record; they do NOT rebuild the embedding index
(run ``python recommend.py update`` for that).

Subcommands:
  categories   Download whole categories, newest first
  novel        Download a single title or URL
  repair       Re-download files that contain failed-page markers

Examples:
  # Download every pending novel in gl, newest first
  python download.py categories gl

  # Two categories, oldest first, capped at 100 novels
  python download.py categories yanqing bl --forward --limit 100

  # One novel by title (disambiguated interactively) or by URL
  python download.py novel "诡秘之主"
  python download.py novel https://www.52shuku.net/gl/180.html

  # Repair incomplete gl downloads
  python download.py repair --category gl

  # Split a bulk run across the direct route and a Windscribe tunnel
  python download.py categories all --windscribe \\
      --windscribe-location "Singapore - SMRT"
"""
from __future__ import annotations

import argparse
import sys

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


def cmd_categories(args) -> int:
    from webnovel.downloads import download_categories

    categories = parse_categories(args.categories)
    code, _stats = download_categories(
        categories,
        limit=args.limit,
        forward=args.forward,
        workers=args.workers,
        verbose=args.verbose,
        chapter_logging=args.chapter_logging,
        windscribe=args.windscribe,
        windscribe_location=args.windscribe_location,
        direct_interface=args.direct_interface,
        skip_route_check=args.skip_route_check,
    )
    return code


def cmd_novel(args) -> int:
    from webnovel.downloads import download_novel
    from webnovel.targets import choose_resolution, print_candidates

    resolution = choose_resolution(args.target, interactive=sys.stdin.isatty())
    if not resolution.url:
        if resolution.candidates:
            print(f"Ambiguous target: {args.target}")
            print_candidates(resolution.candidates)
        else:
            print(f"No novel matched: {args.target}")
        return 1
    return download_novel(
        resolution.url,
        workers=args.workers,
        force=args.force,
        verbose=args.verbose,
    )[0]


def cmd_repair(args) -> int:
    from webnovel.downloads import download_novel
    from webnovel.reports import incomplete_report

    categories = parse_categories(args.category)
    _text, urls = incomplete_report(categories)
    if args.limit:
        urls = urls[: args.limit]
    if not urls:
        print("No incomplete downloads found.")
        return 0
    failed = 0
    for url in urls:
        code, _ = download_novel(url, workers=args.workers, force=True, verbose=args.verbose)
        failed += code != 0
    print(f"Repair complete: {len(urls) - failed} repaired, {failed} failed")
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="download.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    categories = sub.add_parser(
        "categories",
        help="Download whole categories",
        description="Download every pending novel in the selected categories.",
    )
    categories.add_argument(
        "categories", nargs="+", metavar="CATEGORY",
        help=f"One or more categories, or 'all'. Choices: {', '.join(CATEGORIES)}",
    )
    categories.add_argument("--limit", type=int, default=0, metavar="N",
                            help="Maximum novels to download (0 = all pending)")
    categories.add_argument("--forward", action="store_true",
                            help="Oldest first (default is newest first)")
    categories.add_argument("--workers", type=int, default=1, metavar="N",
                            help="Parallel reading-page requests within the active novel (default: 1)")
    categories.add_argument("--verbose", action="store_true",
                            help="Log every chapter page fetch")
    categories.add_argument("--chapter-logging", action="store_true",
                            help="Record per-chapter detail in the run log")
    categories.add_argument("--windscribe", action="store_true",
                            help="Split work across the direct route and a Windscribe tunnel")
    categories.add_argument("--windscribe-location", default="best", metavar="LOC",
                            help="Windscribe location to connect (default: best)")
    categories.add_argument("--direct-interface", metavar="IFACE",
                            help="Force the direct-route network interface (e.g. eth0)")
    categories.add_argument("--skip-route-check", action="store_true",
                            help="Skip the public-IP comparison before a Windscribe run")
    categories.set_defaults(func=cmd_categories)

    novel = sub.add_parser(
        "novel",
        help="Download one title or URL",
        description="Download a single novel. A title is resolved against the metadata "
        "store; an unknown but valid URL is downloaded and registered.",
    )
    novel.add_argument("target", help="Novel title (in the store) or a novel landing URL")
    novel.add_argument("--workers", type=int, default=2, metavar="N",
                       help="Parallel reading-page requests (default: 2)")
    novel.add_argument("--force", action="store_true",
                       help="Re-download even if a complete local file exists")
    novel.add_argument("--verbose", action="store_true", help="Log every chapter page fetch")
    novel.set_defaults(func=cmd_novel)

    repair = sub.add_parser(
        "repair",
        help="Re-download incomplete files",
        description="Re-download novels whose saved text contains failed-page markers.",
    )
    repair.add_argument("--category", action="append", metavar="CATS",
                        help="Category, comma list, or 'all' (default: all)")
    repair.add_argument("--limit", type=int, default=0, metavar="N",
                        help="Maximum novels to repair (0 = all affected)")
    repair.add_argument("--workers", type=int, default=2, metavar="N",
                        help="Parallel reading-page requests (default: 2)")
    repair.add_argument("--verbose", action="store_true", help="Log every chapter page fetch")
    repair.set_defaults(func=cmd_repair)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
