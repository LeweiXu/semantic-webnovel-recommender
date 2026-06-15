from __future__ import annotations

import argparse
import json
import subprocess
import sys
import threading
from dataclasses import asdict
from pathlib import Path

from recsys.embed import DEFAULT_MODEL
from scripts.repo_paths import CATEGORIES, DATA_DIR, REPORTS_DIR, ROOT_DIR

from webnovel_app.downloads import download_categories, download_novel
from webnovel_app.library import (
    clipboard_copy,
    interactive_reader,
    list_library,
    live_first_chapter,
    local_chapters,
    local_path,
    print_record,
)
from webnovel_app.reports import (
    catalogue_report,
    incomplete_report,
    size_report,
    write_report,
)
from webnovel_app.targets import choose_resolution, print_candidates


def parse_categories(value: str | list[str] | None) -> list[str]:
    if value is None:
        return list(CATEGORIES)
    values = [value] if isinstance(value, str) else value
    result: list[str] = []
    for item in values:
        result.extend(part.strip() for part in item.split(",") if part.strip())
    if "all" in result:
        return list(CATEGORIES)
    return list(dict.fromkeys(result))


def cmd_metadata_crawl(args) -> int:
    from recsys.crawl import crawl

    categories = parse_categories(args.category)
    unknown = [category for category in categories if category not in CATEGORIES]
    if unknown:
        print(f"Unknown categories: {', '.join(unknown)}")
        return 2
    stop_event = threading.Event()
    try:
        stats = crawl(
            categories,
            pages=args.pages,
            delay=args.delay,
            workers=args.workers,
            refresh=args.refresh,
            limit=args.limit,
            rec_depth=args.recommendation_depth,
            stop_event=stop_event,
        )
    except KeyboardInterrupt:
        stop_event.set()
        print("Interrupted; the latest checkpoint has been written.")
        return 130
    print(
        f"Metadata crawl: {stats['fetched']} fetched, "
        f"{stats['not_found']} not found, {stats['errors']} errors, "
        f"{stats['skipped']} known nodes traversed"
    )
    return 1 if stats["errors"] else 0


def cmd_metadata_sync(args) -> int:
    from recsys.extract import sync

    stats = sync(
        limit=args.limit,
        rebuild=args.rebuild,
        categories=parse_categories(args.category),
    )
    print(f"Metadata sync: {stats[0]} added, {stats[1]} updated, {stats[2]} skipped")
    return 0


def cmd_metadata_status(args) -> int:
    print(catalogue_report(parse_categories(args.category)))
    return 0


def cmd_metadata_migrate(args) -> int:
    from recsys.catalog import seed_gl_from_catalog

    count = seed_gl_from_catalog(Path(args.source))
    print(f"Migrated {count:,} GL catalogue record(s) into gl/_catalog.jsonl.")
    return 0 if count else 1


def _resolve_or_error(target: str, *, downloaded_only: bool = False):
    resolution = choose_resolution(
        target,
        downloaded_only=downloaded_only,
        interactive=sys.stdin.isatty(),
    )
    if resolution.url:
        return resolution
    if resolution.candidates:
        print(f"Ambiguous target: {target}")
        print_candidates(resolution.candidates)
    else:
        print(f"No novel matched: {target}")
    return None


def cmd_download_novel(args) -> int:
    resolution = _resolve_or_error(args.target)
    if resolution is None:
        return 1
    return download_novel(
        resolution.url,
        workers=args.workers,
        force=args.force,
        verbose=args.verbose,
    )[0]


def cmd_download_categories(args) -> int:
    categories = parse_categories(args.categories)
    return download_categories(
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
    )[0]


def cmd_download_repair(args) -> int:
    categories = parse_categories(args.category)
    _, urls = incomplete_report(categories)
    if args.limit:
        urls = urls[:args.limit]
    failed = 0
    for url in urls:
        code, _ = download_novel(url, workers=args.workers, force=True, verbose=args.verbose)
        failed += code != 0
    print(f"Repair complete: {len(urls) - failed} repaired, {failed} failed")
    return 1 if failed else 0


def cmd_library_list(args) -> int:
    records = list_library(
        query=args.query or "",
        categories=set(parse_categories(args.category)) if args.category else None,
        downloaded_only=args.downloaded,
        limit=args.limit,
    )
    for index, record in enumerate(records, 1):
        marker = "downloaded" if record.downloaded else "metadata"
        print(
            f"{index:>3}. 《{record.title}》 - {record.author or '?'} "
            f"[{record.category}] [{marker}] {record.year_month}"
        )
    print(f"\n{len(records)} record(s) shown.")
    return 0


def cmd_library_info(args) -> int:
    resolution = _resolve_or_error(args.target)
    if resolution is None or resolution.record is None:
        print("Metadata must be crawled before `library info` can display a novel.")
        return 1
    print_record(resolution.record)
    return 0


def cmd_library_read(args) -> int:
    resolution = _resolve_or_error(args.target)
    if resolution is None:
        return 1
    record = resolution.record
    path = local_path(record) if record else None
    if path and path.exists():
        chapters = local_chapters(path)
        if args.full:
            print("\n\n".join(chapter.text() for chapter in chapters))
            return 0
        position = max(0, (args.chapter or 1) - 1)
        if position >= len(chapters):
            print(f"Chapter {args.chapter} does not exist; novel has {len(chapters)} sections.")
            return 1
        count = max(1, args.copy or 1)
        payload = "\n\n".join(
            chapter.text() for chapter in chapters[position:position + count]
        )
        if args.copy:
            try:
                backend = clipboard_copy(payload)
            except Exception as exc:
                print(f"Copy failed: {exc}")
                return 1
            print(f"Copied {min(count, len(chapters) - position)} chapter(s) via {backend}.")
            return 0
        if args.chapter:
            print(payload)
            return 0
        return interactive_reader(record, chapters)

    text, truncated = live_first_chapter(resolution.url, args.page_limit)
    print(text)
    if truncated:
        print(f"\n[Preview stopped at the {args.page_limit}-page safety limit.]")
    return 0


def cmd_index_status(_args) -> int:
    from recsys.index import load_index
    from recsys.store import load_all

    store = load_all()
    index = load_index()
    if index is None:
        print(f"Index missing; {len(store):,} metadata record(s) are ready to embed.")
        return 1
    store_urls, index_urls = set(store), set(index.urls)
    stale = sum(
        store[url].content_hash != index.hashes.get(url)
        for url in store_urls & index_urls
    )
    print(
        f"Index: {len(index.urls):,} vectors ({index.model}, dim={index.dim})\n"
        f"Metadata: {len(store):,} records\n"
        f"New: {len(store_urls - index_urls):,}  Changed: {stale:,}  "
        f"Removed: {len(index_urls - store_urls):,}"
    )
    return 1 if (store_urls != index_urls or stale) else 0


def cmd_index_build(args) -> int:
    from recsys.index import build

    stats = build(model_name=args.model, device=args.device, rebuild=args.rebuild)
    print(
        f"Index: {stats['embedded']} embedded, {stats['reused']} reused, "
        f"{stats['total']} total ({stats['model']}, dim={stats['dim']})"
    )
    return 0


def cmd_report_catalogue(args) -> int:
    text = catalogue_report(parse_categories(args.category))
    print(text)
    if args.write:
        print(f"Wrote {write_report('catalogue_summary.txt', text)}")
    return 0


def cmd_report_size(args) -> int:
    text = size_report(parse_categories(args.category))
    print(text)
    return 0


def cmd_report_incomplete(args) -> int:
    text, urls = incomplete_report(parse_categories(args.category))
    print(text)
    if args.urls:
        path = Path(args.urls)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(urls) + ("\n" if urls else ""), encoding="utf-8")
        print(f"Wrote {path}")
    return 0


def cmd_report_chains(args) -> int:
    from scripts.analyze_catalogue_chains import (
        build_chains,
        merge_display_chains,
        render_report,
    )
    from recsys.catalog import load_catalog
    from recsys.store import load_category

    exit_code = 0
    for category in parse_categories(args.category):
        metadata = load_category(category)
        by_url = {}
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
        live = {
            url: record
            for url, record in by_url.items()
            if record.get("fetch_status", "ok") == "ok"
        }
        strict = build_chains(by_url, live)
        chains = merge_display_chains(strict, by_url)
        report = render_report(chains, by_url, live, len(strict), title=category)
        print(report)
        if args.write:
            path = write_report(f"{category}_catalog_chains.txt", report)
            json_path = DATA_DIR / f"{category}_catalog_chains.json"
            json_path.write_text(json.dumps(chains, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Wrote {path} and {json_path}")
    return exit_code


def cmd_report_urls(args) -> int:
    forwarded = ["--output", str(ROOT_DIR / args.category)]
    if args.json:
        forwarded += ["--json", args.json]
    return _run_script("scripts/inspect_urls.py", forwarded)


def _run_script(script: str, forwarded: list[str]) -> int:
    return subprocess.call([sys.executable, str(ROOT_DIR / script), *forwarded])


def cmd_watch(args) -> int:
    forwarded = []
    if args.output:
        forwarded += ["--output", args.output]
    forwarded += ["--interval", str(args.interval)]
    if args.no_log_file:
        forwarded.append("--no-log-file")
    return _run_script("scripts/watch_output.py", forwarded)


def cmd_admin_windscribe(args) -> int:
    return _run_script("scripts/configure_windscribe.py", args.admin_args)


def cmd_admin_audit(args) -> int:
    names = {
        "rate-limit": "scripts/probe_rate_limit.py",
        "br-loss": "scripts/audit_br_loss.py",
        "br-sample": "scripts/audit_br_sample.py",
    }
    script = names[args.audit]
    return _run_script(script, args.audit_args)


def _add_recommend_flags(parser: argparse.ArgumentParser) -> None:
    from recsys.cli import _add_recommend_flags as add

    add(parser)


def build_parser() -> argparse.ArgumentParser:
    from recsys import cli as recommendation

    parser = argparse.ArgumentParser(
        prog="webnovel",
        description="Crawl, recommend, download, and read 52shuku web novels.",
    )
    groups = parser.add_subparsers(dest="group", required=True)

    metadata = groups.add_parser("metadata", help="Crawl and inspect metadata")
    metadata_sub = metadata.add_subparsers(dest="command", required=True)
    crawl = metadata_sub.add_parser("crawl", help="Update metadata and catalogue files")
    crawl.add_argument("--category", action="append", help="Category, comma list, or all")
    crawl.add_argument("--pages", type=int, default=0, help="Opening pages stored as excerpt")
    crawl.add_argument("--limit", type=int, default=0)
    crawl.add_argument("--delay", type=float, default=0.4)
    crawl.add_argument("--workers", type=int, default=1)
    crawl.add_argument("--refresh", action="store_true")
    crawl.add_argument("--recommendation-depth", type=int, default=4)
    crawl.set_defaults(func=cmd_metadata_crawl)
    sync = metadata_sub.add_parser("sync-files", help="Sync downloaded files into metadata")
    sync.add_argument("--category", action="append")
    sync.add_argument("--limit", type=int, default=0)
    sync.add_argument("--rebuild", action="store_true")
    sync.set_defaults(func=cmd_metadata_sync)
    status = metadata_sub.add_parser("status", help="Show per-category coverage")
    status.add_argument("--category", action="append")
    status.set_defaults(func=cmd_metadata_status)
    migrate = metadata_sub.add_parser("migrate-legacy-gl", help="Import data/gl_catalog.json")
    migrate.add_argument("--source", default=str(DATA_DIR / "gl_catalog.json"))
    migrate.set_defaults(func=cmd_metadata_migrate)

    download = groups.add_parser("download", help="Download full novels")
    download_sub = download.add_subparsers(dest="command", required=True)
    novel = download_sub.add_parser("novel", help="Download a title or URL")
    novel.add_argument("target")
    novel.add_argument("--workers", type=int, default=2)
    novel.add_argument("--force", action="store_true")
    novel.add_argument("--verbose", action="store_true")
    novel.set_defaults(func=cmd_download_novel)
    categories = download_sub.add_parser("categories", help="Download selected catalogues")
    categories.add_argument("categories", nargs="+")
    categories.add_argument("--limit", type=int, default=0)
    categories.add_argument("--forward", action="store_true", help="Oldest first")
    categories.add_argument("--workers", type=int, default=1)
    categories.add_argument("--verbose", action="store_true")
    categories.add_argument("--chapter-logging", action="store_true")
    categories.add_argument("--windscribe", action="store_true")
    categories.add_argument("--windscribe-location", default="best")
    categories.add_argument("--direct-interface")
    categories.add_argument("--skip-route-check", action="store_true")
    categories.set_defaults(func=cmd_download_categories)
    repair = download_sub.add_parser("repair", help="Re-download incomplete novels")
    repair.add_argument("--category", action="append")
    repair.add_argument("--limit", type=int, default=0)
    repair.add_argument("--workers", type=int, default=2)
    repair.add_argument("--verbose", action="store_true")
    repair.set_defaults(func=cmd_download_repair)

    recommend = groups.add_parser("recommend", help="Recommendation system")
    recommend_sub = recommend.add_subparsers(dest="command", required=True)
    like = recommend_sub.add_parser("like")
    like.add_argument("title")
    _add_recommend_flags(like)
    like.set_defaults(func=recommendation.cmd_like)
    query = recommend_sub.add_parser("query")
    query.add_argument("text")
    query.add_argument("--parse", action="store_true")
    _add_recommend_flags(query)
    query.set_defaults(func=recommendation.cmd_query)
    tags = recommend_sub.add_parser("tags")
    tags.add_argument("tags", nargs="+")
    tags.add_argument("-n", "--num", type=int, default=20)
    tags.add_argument("--status")
    tags.add_argument("--category")
    tags.set_defaults(func=recommendation.cmd_tags)
    repl = recommend_sub.add_parser("repl")
    repl.add_argument("-n", "--num", type=int, default=10)
    repl.add_argument("--model", default=DEFAULT_MODEL)
    repl.add_argument("--device", default="auto")
    repl.set_defaults(func=recommendation.cmd_repl)

    library = groups.add_parser("library", help="Browse and read novels")
    library_sub = library.add_subparsers(dest="command", required=True)
    listing = library_sub.add_parser("list")
    listing.add_argument("query", nargs="?")
    listing.add_argument("--category", action="append")
    listing.add_argument("--downloaded", action="store_true")
    listing.add_argument("--limit", type=int, default=50)
    listing.set_defaults(func=cmd_library_list)
    info = library_sub.add_parser("info")
    info.add_argument("target")
    info.set_defaults(func=cmd_library_info)
    read = library_sub.add_parser("read")
    read.add_argument("target")
    read.add_argument("--chapter", type=int)
    read.add_argument("--full", action="store_true")
    read.add_argument("--copy", type=int)
    read.add_argument("--page-limit", type=int, default=10)
    read.set_defaults(func=cmd_library_read)

    index = groups.add_parser("index", help="Embedding index maintenance")
    index_sub = index.add_subparsers(dest="command", required=True)
    index_status = index_sub.add_parser("status")
    index_status.set_defaults(func=cmd_index_status)
    for name, rebuild in (("update", False), ("rebuild", True)):
        build = index_sub.add_parser(name)
        build.add_argument("--model", default=DEFAULT_MODEL)
        build.add_argument("--device", default="auto")
        build.set_defaults(func=cmd_index_build, rebuild=rebuild)

    report = groups.add_parser("report", help="Library and catalogue reports")
    report_sub = report.add_subparsers(dest="command", required=True)
    for name, func in (
        ("catalogue", cmd_report_catalogue),
        ("size", cmd_report_size),
    ):
        item = report_sub.add_parser(name)
        item.add_argument("--category", action="append")
        if name == "catalogue":
            item.add_argument("--write", action="store_true")
        item.set_defaults(func=func)
    incomplete = report_sub.add_parser("incomplete")
    incomplete.add_argument("--category", action="append")
    incomplete.add_argument("--urls")
    incomplete.set_defaults(func=cmd_report_incomplete)
    chains = report_sub.add_parser("chains")
    chains.add_argument("--category", action="append")
    chains.add_argument("--write", action="store_true")
    chains.set_defaults(func=cmd_report_chains)
    urls = report_sub.add_parser("urls")
    urls.add_argument("--category", default="gl", choices=CATEGORIES)
    urls.add_argument("--json")
    urls.set_defaults(func=cmd_report_urls)

    watch = groups.add_parser("watch", help="Watch for newly downloaded novels")
    watch.add_argument("--output")
    watch.add_argument("--interval", type=float, default=2.0)
    watch.add_argument("--no-log-file", action="store_true")
    watch.set_defaults(func=cmd_watch)

    admin = groups.add_parser("admin", help="Advanced diagnostics and setup")
    admin_sub = admin.add_subparsers(dest="command", required=True)
    windscribe = admin_sub.add_parser("windscribe")
    windscribe.add_argument("admin_args", nargs=argparse.REMAINDER)
    windscribe.set_defaults(func=cmd_admin_windscribe)
    audit = admin_sub.add_parser("audit")
    audit.add_argument("audit", choices=["br-loss", "br-sample", "rate-limit"])
    audit.add_argument("audit_args", nargs=argparse.REMAINDER)
    audit.set_defaults(func=cmd_admin_audit)
    return parser


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        from webnovel_app.tui import run_tui

        return run_tui()
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)
