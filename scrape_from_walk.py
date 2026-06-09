#!/usr/bin/env python3
"""
Walk the 52shuku GL previous/next chain without using a catalogue.

Usage:
    python scrape_from_walk.py --seed URL
    python scrape_from_walk.py --forward
    python scrape_from_walk.py --backward
    python scrape_from_walk.py --backward --limit 5
    python scrape_from_walk.py --backward --resume URL
    python scrape_from_walk.py --get URL
    python scrape_from_walk.py --repair
"""
from __future__ import annotations

import argparse
import logging
import random
import sys
import time
from datetime import datetime
from pathlib import Path

from curl_cffi import requests as cffi_requests

from scraper import (
    DELAY_CHAPTER,
    DELAY_NOVEL,
    DELAY_NOVEL_JITTER,
    INCOMPLETE_LOG,
    LOG_DIR,
    OUTPUT_DIR,
    backfill_scraped,
    fetch,
    find_incomplete,
    load_state,
    open_run_log,
    parse_landing,
    save_state,
    scrape_novel,
    step_url_id,
    stub_novel,
    title_to_filename,
    update_nav_in_file,
    upload_month_dir,
    write_novel_log,
    write_run_footer,
)

log = logging.getLogger(__name__)

BROKEN_CHAIN_LOG = LOG_DIR / "broken_chain.log"

# Probe nearby IDs within the same shard when a chain link points to a deleted
# novel. Reverse-link verification prevents an unrelated live page from being
# treated as the missing neighbour.
PROBE_BUDGET = 150


def bridge_gap(
    session: cffi_requests.Session,
    dead_url: str,
    is_forward: bool,
) -> str | None:
    """Probe the same shard for a live novel adjacent to a deleted chain link."""
    step = 1 if is_forward else -1
    dead_seen = {dead_url}
    reverse_label = "上一篇" if is_forward else "下一篇"

    for distance in range(1, PROBE_BUDGET + 1):
        candidate = step_url_id(dead_url, step * distance)
        if candidate is None:
            break
        time.sleep(DELAY_CHAPTER)
        try:
            response = fetch(session, candidate)
        except FileNotFoundError:
            dead_seen.add(candidate)
            continue
        except Exception:
            continue

        meta = parse_landing(response.text, candidate)
        reverse_url = meta.prev_url if is_forward else meta.next_url
        if reverse_url in dead_seen:
            log.info(
                "Bridge verified: %s %s -> %s (a probed dead link)",
                candidate,
                reverse_label,
                reverse_url,
            )
            return candidate
        log.warning(
            "Bridge candidate %s found, but its %s (%s) is not one of the "
            "probed dead pages; not auto-continuing.",
            candidate,
            reverse_label,
            reverse_url or "-",
        )
        return None

    return None


def log_broken_chain(
    reached_from: str | None,
    dead_url: str,
    direction: str,
) -> None:
    LOG_DIR.mkdir(exist_ok=True)
    with BROKEN_CHAIN_LOG.open("a", encoding="utf-8") as handle:
        handle.write(f"{datetime.now().isoformat()}  direction={direction}\n")
        handle.write(f"  reached_from: {reached_from or '(start)'}\n")
        handle.write(f"  dead_link:    {dead_url}\n\n")


def run_seed(args: argparse.Namespace, session: cffi_requests.Session, state: dict) -> int:
    output_dir = Path(args.output)
    log_path, log_file = open_run_log("seed", args.limit, output_dir)
    started = time.monotonic()
    url = args.seed.strip()

    try:
        novel = scrape_novel(
            session,
            url,
            output_dir,
            workers=args.workers,
            verbose=args.verbose,
        )
    except FileNotFoundError:
        log.error("Seed URL 404 (no such novel): %s", url)
        write_run_footer(log_file, 0, 0, 1, time.monotonic() - started)
        return 1

    if not novel:
        write_run_footer(log_file, 0, 0, 1, time.monotonic() - started)
        return 1

    state["oldest_url"] = url
    state["newest_url"] = url
    state["scraped"] = sorted(set(state.get("scraped", [])) | {url})
    save_state(state)
    log.info("State initialised: oldest=newest=%s", url)
    write_novel_log(log_file, novel, "OK" if not novel.skipped else "SKIP")
    write_run_footer(log_file, 1, 0, 0, time.monotonic() - started)
    log.info("Run log: %s", log_path)
    return 0


def run_single(args: argparse.Namespace, session: cffi_requests.Session) -> int:
    output_dir = Path(args.output)
    log_path, log_file = open_run_log("get", args.limit, output_dir)
    started = time.monotonic()
    url = args.get.strip()

    try:
        novel = scrape_novel(
            session,
            url,
            output_dir,
            workers=args.workers,
            verbose=args.verbose,
            month_subdir=False,
            force=True,
        )
    except FileNotFoundError:
        log.error("URL 404 (no such novel): %s", url)
        write_run_footer(log_file, 0, 0, 1, time.monotonic() - started)
        return 1

    if not novel:
        write_run_footer(log_file, 0, 0, 1, time.monotonic() - started)
        return 1

    write_novel_log(log_file, novel, "OK")
    write_run_footer(log_file, 1, 0, 0, time.monotonic() - started)
    log.info("Run log: %s", log_path)
    return 0


def run_repair(
    args: argparse.Namespace,
    session: cffi_requests.Session,
    state: dict,
) -> int:
    output_dir = Path(args.output)
    log_path, log_file = open_run_log("repair", args.limit, output_dir)
    started = time.monotonic()
    targets = find_incomplete(output_dir)
    log.info("Found %d incomplete novel(s) to repair.", len(targets))
    scraped = set(state.get("scraped", []))
    repaired = failed = 0

    for path, source_url in targets:
        if not source_url:
            log.warning("Cannot repair %s; no source URL in preamble", path)
            failed += 1
            continue
        try:
            novel = scrape_novel(
                session,
                source_url,
                output_dir,
                workers=args.workers,
                verbose=args.verbose,
                force=True,
            )
        except FileNotFoundError:
            log.error("Repair source was deleted: %s", source_url)
            failed += 1
            continue

        if novel and not novel.failed_pages:
            repaired += 1
            write_novel_log(log_file, novel, "REPAIRED")
            scraped.add(source_url)
        else:
            failed += 1
            write_novel_log(log_file, novel or stub_novel(source_url), "STILL-PARTIAL")
        time.sleep(DELAY_NOVEL + random.uniform(0, DELAY_NOVEL_JITTER))

    if state:
        state["scraped"] = sorted(scraped)
        save_state(state)
    write_run_footer(log_file, repaired, 0, failed, time.monotonic() - started)
    log.info("Repair done: %d fixed, %d still failing; log: %s", repaired, failed, log_path)
    return 0


def run_walk(
    args: argparse.Namespace,
    session: cffi_requests.Session,
    state: dict,
) -> int:
    output_dir = Path(args.output)
    if not state and not args.resume:
        log.error("No state.json; run with --seed <URL> first.")
        return 1

    backfill_scraped(state, output_dir)
    scraped: set[str] = set(state.get("scraped", []))
    is_forward = args.forward
    mode_label = "forward" if is_forward else "backward"
    direction_label = "下一篇" if is_forward else "上一篇"
    log_path, log_file = open_run_log(mode_label, args.limit, output_dir)
    started = time.monotonic()

    if args.resume:
        current_url = args.resume.strip()
        log.info("Resume %s walk at %s (inclusive)", direction_label, current_url)
    else:
        boundary_url = state["newest_url"] if is_forward else state["oldest_url"]
        log.info("Chain walk %s from boundary: %s", direction_label, boundary_url)
        try:
            response = fetch(session, boundary_url)
        except Exception as exc:
            log.error("Cannot fetch boundary %s: %s", boundary_url, exc)
            write_run_footer(log_file, 0, 0, 1, time.monotonic() - started)
            return 1

        boundary_meta = parse_landing(response.text, boundary_url)
        current_url = boundary_meta.next_url if is_forward else boundary_meta.prev_url
        if not current_url:
            log.info("No %s from boundary; already at chain end.", direction_label)
            write_run_footer(log_file, 0, 0, 0, time.monotonic() - started)
            return 0

        nav_direction = "next" if is_forward else "prev"
        nav_title = boundary_meta.next_title if is_forward else boundary_meta.prev_title
        nav_url = boundary_meta.next_url if is_forward else boundary_meta.prev_url
        boundary_file = upload_month_dir(
            boundary_meta.upload_date,
            output_dir,
        ) / title_to_filename(
            boundary_meta.title,
            boundary_meta.author,
            boundary_meta.status,
        )
        if update_nav_in_file(boundary_file, nav_direction, nav_title, nav_url):
            log.info(
                "Updated %s preamble of %s -> %s",
                direction_label,
                boundary_file.name,
                nav_url,
            )

    scraped_count = skipped_count = failed_count = 0
    last_good_url: str | None = None

    while current_url:
        if args.limit and scraped_count + skipped_count >= args.limit:
            log.info("Reached --limit %d.", args.limit)
            break
        if current_url in scraped:
            log.info(
                "Already scraped %s; chain rejoined known territory or a loop.",
                current_url,
            )
            break

        try:
            novel = scrape_novel(
                session,
                current_url,
                output_dir,
                workers=args.workers,
                verbose=args.verbose,
            )
        except FileNotFoundError:
            log.warning(
                "404 (deleted novel): %s; probing up to %d IDs in-shard.",
                current_url,
                PROBE_BUDGET,
            )
            bridged = bridge_gap(session, current_url, is_forward)
            if bridged:
                log.warning("Bridged gap; resuming at %s", bridged)
                current_url = bridged
                continue
            failed_count += 1
            log_broken_chain(last_good_url, current_url, direction_label)
            log.error(
                "Broken chain at %s; in-shard bridge failed. Logged to %s.",
                current_url,
                BROKEN_CHAIN_LOG,
            )
            log.error(
                "Find the next live novel, then run: python scrape_from_walk.py --%s --resume <URL>",
                mode_label,
            )
            break

        if novel is None:
            failed_count += 1
            write_novel_log(log_file, stub_novel(current_url), "FAIL")
            log.error("Unrecoverable failure at %s; stopping.", current_url)
            break

        if novel.skipped:
            skipped_count += 1
            write_novel_log(log_file, novel, "SKIP")
        elif novel.failed_pages:
            failed_count += 1
            write_novel_log(log_file, novel, "PARTIAL")
            log.warning(
                "Incomplete (%d failed pages): %s; logged to %s",
                len(novel.failed_pages),
                current_url,
                INCOMPLETE_LOG,
            )
        else:
            scraped_count += 1
            write_novel_log(log_file, novel, "OK")

        if is_forward:
            state["newest_url"] = current_url
        else:
            state["oldest_url"] = current_url
        if not novel.failed_pages:
            scraped.add(current_url)
            state["scraped"] = sorted(scraped)
        save_state(state)
        last_good_url = current_url

        next_url = novel.meta.next_url if is_forward else novel.meta.prev_url
        if not next_url:
            log.info("No %s from %s; reached chain end.", direction_label, current_url)
            break
        current_url = next_url

        delay = DELAY_NOVEL + random.uniform(0, DELAY_NOVEL_JITTER)
        log.info("Sleeping %.1fs before next novel...", delay)
        time.sleep(delay)

    write_run_footer(
        log_file,
        scraped_count,
        skipped_count,
        failed_count,
        time.monotonic() - started,
    )
    log.info(
        "Done: %d scraped  %d skipped  %d failed; log: %s",
        scraped_count,
        skipped_count,
        failed_count,
        log_path,
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--seed", metavar="URL", help="Scrape URL and initialise state")
    mode.add_argument("--forward", action="store_true", help="Walk newer novels")
    mode.add_argument("--backward", action="store_true", help="Walk older novels")
    mode.add_argument(
        "--get",
        metavar="URL",
        help="Download one novel directly into --output without changing state",
    )
    mode.add_argument(
        "--repair",
        action="store_true",
        help="Re-download novels containing failed-page placeholders",
    )
    parser.add_argument("--limit", type=int, default=0, help="Stop after N novels (0 = unlimited)")
    parser.add_argument("--output", default=str(OUTPUT_DIR), help="Output directory (default: output/)")
    parser.add_argument("--workers", type=int, default=1, help="Parallel page fetches per novel")
    parser.add_argument("--verbose", action="store_true", help="Print every page on its own line")
    parser.add_argument(
        "--resume",
        metavar="URL",
        help="With --forward/--backward, start at this URL inclusively",
    )
    args = parser.parse_args()

    if args.resume and not (args.forward or args.backward):
        parser.error("--resume requires --forward or --backward")
    if args.limit < 0:
        parser.error("--limit must be 0 or greater")
    if args.workers < 1:
        parser.error("--workers must be at least 1")

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    session = cffi_requests.Session()
    state = load_state()
    try:
        if args.seed:
            return run_seed(args, session, state)
        if args.get:
            return run_single(args, session)
        if args.repair:
            return run_repair(args, session, state)
        return run_walk(args, session, state)
    finally:
        session.close()


if __name__ == "__main__":
    sys.exit(main())
