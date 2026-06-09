#!/usr/bin/env python3
"""
Download novels listed in a catalogue JSON file without walking site links.

The catalogue must be a JSON array whose entries contain a "url" field, as
produced by estimate_history.py. By default, live entries are processed in
reverse catalogue order (newest to oldest). Use --forward for catalogue order
(oldest to newest). Entries marked fetch_status="not_found" and complete novels
already present under the output directory are skipped without a network
request.

Usage:
    python scrape_from_catalogue.py
    python scrape_from_catalogue.py --forward
    python scrape_from_catalogue.py --catalogue gl_catalog.json --limit 20
    python scrape_from_catalogue.py --workers 3 --verbose
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import re
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

from curl_cffi import requests as cffi_requests

from scraper import (
    DELAY_NOVEL,
    DELAY_NOVEL_JITTER,
    INCOMPLETE_LOG,
    OUTPUT_DIR,
    _is_complete_file,
    _source_url_from_file,
    _stub_novel,
    load_state,
    open_run_log,
    save_state,
    scrape_novel,
    write_novel_log,
    write_run_footer,
)

log = logging.getLogger(__name__)


def is_gl_landing_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    if not parsed.path.startswith("/gl/") or not parsed.path.endswith(".html"):
        return False
    name = parsed.path.rsplit("/", 1)[-1][:-5]
    return bool(name) and not name.startswith("index") and not re.search(r"_\d+$", name)


def load_catalogue_urls(path: Path) -> tuple[list[str], int, int]:
    """Return unique GL landing-page URLs in catalogue order.

    The counts are (invalid entries, duplicate URLs).
    """
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    if not isinstance(raw, list):
        raise ValueError("catalogue root must be a JSON array")

    urls: list[str] = []
    seen: set[str] = set()
    invalid = duplicates = 0

    for entry in raw:
        if isinstance(entry, dict) and entry.get("fetch_status") == "not_found":
            continue
        url = entry.get("url") if isinstance(entry, dict) else None
        if not isinstance(url, str):
            invalid += 1
            continue
        url = url.strip()
        if not is_gl_landing_url(url):
            invalid += 1
            continue
        if url in seen:
            duplicates += 1
            continue
        seen.add(url)
        urls.append(url)

    return urls, invalid, duplicates


def find_complete_urls(output_dir: Path) -> set[str]:
    """Read source URLs from complete output files for zero-request resume."""
    complete: set[str] = set()
    for path in output_dir.rglob("*.txt"):
        if not _is_complete_file(path):
            continue
        url = _source_url_from_file(path)
        if url:
            complete.add(url)
    return complete


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalogue",
        default="gl_catalog.json",
        help="Catalogue JSON from estimate_history.py (default: gl_catalog.json)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Stop after N pending catalogue entries (0 = unlimited)",
    )
    parser.add_argument(
        "--output",
        default=str(OUTPUT_DIR),
        help="Output directory (default: output/)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel page fetches per novel (default 1; try 3-5)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print each page on its own line instead of in-place",
    )
    parser.add_argument(
        "--forward",
        action="store_true",
        help="Process oldest to newest (existing catalogue order); default is newest to oldest",
    )
    args = parser.parse_args()

    if args.limit < 0:
        parser.error("--limit must be 0 or greater")
    if args.workers < 1:
        parser.error("--workers must be at least 1")

    catalogue_path = Path(args.catalogue)
    try:
        urls, invalid, duplicates = load_catalogue_urls(catalogue_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        log.error("Cannot load catalogue %s: %s", catalogue_path, exc)
        return 1

    if not urls:
        log.error("Catalogue %s contains no usable URLs.", catalogue_path)
        return 1

    if invalid:
        log.warning("Ignored %d catalogue entr%s without a usable URL.",
                    invalid, "y" if invalid == 1 else "ies")
    if duplicates:
        log.warning("Ignored %d duplicate catalogue URL(s).", duplicates)

    direction = "forward (oldest to newest)" if args.forward else "backward (newest to oldest)"
    if not args.forward:
        urls.reverse()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    state = load_state()
    sync_scraper_state = "oldest_url" in state and "newest_url" in state
    scraped: set[str] = find_complete_urls(output_dir)
    if sync_scraper_state:
        state_scraped = set(state.get("scraped", []))
        if not scraped.issubset(state_scraped):
            state["scraped"] = sorted(state_scraped | scraped)
            save_state(state)

    pending = [url for url in urls if url not in scraped]
    already_complete = len(urls) - len(pending)
    if args.limit:
        pending = pending[:args.limit]

    log_path, log_fh = open_run_log("catalogue", args.limit, output_dir)
    session = cffi_requests.Session()
    t0 = time.monotonic()
    n_scraped = n_skipped = n_failed = 0
    interrupted = False

    log.info(
        "Catalogue download: %d URL(s), %d already complete, %d queued from %s; %s",
        len(urls),
        already_complete,
        len(pending),
        catalogue_path,
        direction,
    )

    try:
        for index, url in enumerate(pending, start=1):
            log.info("Catalogue [%d/%d]: %s", index, len(pending), url)
            try:
                novel = scrape_novel(
                    session,
                    url,
                    output_dir,
                    workers=args.workers,
                    verbose=args.verbose,
                )
            except FileNotFoundError:
                n_failed += 1
                log.error("Catalogue URL 404 (deleted novel): %s", url)
                write_novel_log(log_fh, _stub_novel(url), "FAIL")
            else:
                if novel is None:
                    n_failed += 1
                    write_novel_log(log_fh, _stub_novel(url), "FAIL")
                    log.error("Unrecoverable failure at %s; continuing.", url)
                elif novel.skipped:
                    n_skipped += 1
                    write_novel_log(log_fh, novel, "SKIP")
                    scraped.add(url)
                    if sync_scraper_state:
                        state["scraped"] = sorted(set(state.get("scraped", [])) | {url})
                        save_state(state)
                elif novel.failed_pages:
                    n_failed += 1
                    write_novel_log(log_fh, novel, "PARTIAL")
                    log.warning(
                        "Incomplete (%d failed pages): %s - logged to %s, "
                        "will retry on the next catalogue run or --repair",
                        len(novel.failed_pages),
                        url,
                        INCOMPLETE_LOG,
                    )
                else:
                    n_scraped += 1
                    write_novel_log(log_fh, novel, "OK")
                    scraped.add(url)
                    if sync_scraper_state:
                        state["scraped"] = sorted(set(state.get("scraped", [])) | {url})
                        save_state(state)

            if index < len(pending):
                delay = DELAY_NOVEL + random.uniform(0, DELAY_NOVEL_JITTER)
                log.info("Sleeping %.1fs before next novel...", delay)
                time.sleep(delay)
    except KeyboardInterrupt:
        interrupted = True
        log.warning("Interrupted; finishing catalogue run log.")
    finally:
        session.close()

    elapsed = time.monotonic() - t0
    write_run_footer(log_fh, n_scraped, n_skipped, n_failed, elapsed)
    log.info(
        "Done: %d scraped  %d skipped  %d failed  - log: %s",
        n_scraped,
        n_skipped,
        n_failed,
        log_path,
    )
    return 130 if interrupted else 0


if __name__ == "__main__":
    sys.exit(main())
