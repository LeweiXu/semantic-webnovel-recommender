from __future__ import annotations

import logging
import random
import threading
import time
from collections import deque
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from curl_cffi import requests as cffi_requests

from recsys.catalog import CatalogRecord, load_catalog, write_catalog
from recsys.extract import parse_txt
from recsys.routes import open_windscribe_routes
from recsys.store import load_category, upsert_category
from scraper import (
    DELAY_NOVEL,
    DELAY_NOVEL_JITTER,
    INCOMPLETE_LOG,
    category_dir_for_url,
    category_of_url,
    is_complete_file,
    is_novel_landing_url,
    open_run_log,
    scrape_novel,
    source_url_from_file,
    stub_novel,
    write_novel_log,
    write_run_footer,
)
from scripts.repo_paths import CATEGORIES, category_dir

log = logging.getLogger("webnovel.download")

# Serialises store/catalog writes so the dual-route (--windscribe) download's two
# threads don't interleave their read-modify-write on the same category files.
# Only the quick registration is held here; the slow network fetch stays outside.
_STORE_LOCK = threading.Lock()


@dataclass
class DownloadStats:
    downloaded: int = 0
    skipped: int = 0
    failed: int = 0

    def add(self, other: "DownloadStats") -> None:
        self.downloaded += other.downloaded
        self.skipped += other.skipped
        self.failed += other.failed


class DownloadQueue:
    def __init__(self, urls: list[str], limit: int = 0) -> None:
        self.urls = deque(urls)
        self.limit = limit
        self.claimed = 0
        self.lock = threading.Lock()

    def claim(self, forward: bool) -> str | None:
        with self.lock:
            if not self.urls or (self.limit and self.claimed >= self.limit):
                return None
            self.claimed += 1
            return self.urls.popleft() if forward else self.urls.pop()

    def __bool__(self) -> bool:
        with self.lock:
            return bool(self.urls)


def _register_download(url: str, novel) -> tuple[int, int]:
    category = category_of_url(url) or "gl"
    record = parse_txt(novel.file_path, url)
    added = updated = 0
    with _STORE_LOCK:
        if record is not None:
            added, updated = upsert_category(category, [record])

        catalog = load_catalog(category)
        prior = catalog.get(url)
        catalog[url] = CatalogRecord(
            url=url,
            category=category,
            fetch_status="ok",
            prev_url=novel.meta.prev_url,
            next_url=novel.meta.next_url,
            rec_urls=list(prior.rec_urls) if prior else [],
            has_meta=record is not None,
        )
        write_catalog(category, catalog)
    return added, updated


def download_novel(
    url: str,
    *,
    workers: int = 2,
    force: bool = False,
    verbose: bool = False,
    event_callback=None,
) -> tuple[int, object | None]:
    emit = event_callback or print
    if not is_novel_landing_url(url):
        emit(f"Unsupported novel URL: {url}")
        return 1, None
    output_dir = category_dir_for_url(url)
    session = cffi_requests.Session()
    try:
        novel = scrape_novel(
            session,
            url,
            output_dir,
            workers=workers,
            verbose=verbose,
            force=force,
        )
    except FileNotFoundError:
        category = category_of_url(url) or "gl"
        catalog = load_catalog(category)
        catalog[url] = CatalogRecord(url, category, "not_found")
        write_catalog(category, catalog)
        emit(f"Novel does not exist (404): {url}")
        return 1, None
    finally:
        session.close()

    if novel is None:
        emit("Download failed; see logs for details.")
        return 1, None
    added, updated = _register_download(url, novel)
    state = "already downloaded" if novel.skipped else "saved"
    emit(
        f"{state}: {novel.file_path} "
        f"(metadata +{added} ~{updated}; index unchanged)"
    )
    if novel.failed_pages:
        emit(f"Warning: {len(novel.failed_pages)} page(s) failed; see {INCOMPLETE_LOG}")
        return 1, novel
    return 0, novel


def _complete_urls(categories: list[str]) -> set[str]:
    complete: set[str] = set()
    for category in categories:
        base = category_dir(category)
        if not base.exists():
            continue
        for path in base.rglob("*.txt"):
            if is_complete_file(path):
                url = source_url_from_file(path)
                if url:
                    complete.add(url)
    return complete


def catalogue_urls(categories: list[str]) -> list[str]:
    metadata = {}
    for category in categories:
        metadata.update(load_category(category))
    live: dict[str, CatalogRecord] = {}
    for category in categories:
        for url, record in load_catalog(category).items():
            if record.fetch_status == "ok":
                live[url] = record
    return sorted(
        live,
        key=lambda url: (
            metadata[url].upload_date if url in metadata else "",
            url,
        ),
    )


def _process_queue(
    label: str,
    queue: DownloadQueue,
    session: cffi_requests.Session,
    *,
    forward: bool,
    workers: int,
    verbose: bool,
    stop_event: threading.Event,
    chapter_logging: bool,
    controls=None,
    event_callback=None,
) -> DownloadStats:
    stats = DownloadStats()
    log_path, log_file = open_run_log(f"catalogue_{label}", queue.limit, Path("."))
    started = time.monotonic()
    try:
        while not stop_event.is_set():
            if controls is not None and not controls.safe_point():
                break
            url = queue.claim(forward)
            if url is None:
                break
            output_dir = category_dir_for_url(url)
            try:
                lock = controls.network_lock if controls is not None else None
                with lock or nullcontext():
                    novel = scrape_novel(
                        session, url, output_dir, workers=workers, verbose=verbose
                    )
            except FileNotFoundError:
                category = category_of_url(url) or "gl"
                with _STORE_LOCK:
                    catalog = load_catalog(category)
                    catalog[url] = CatalogRecord(url, category, "not_found")
                    write_catalog(category, catalog)
                stats.failed += 1
                write_novel_log(log_file, stub_novel(url), "404", chapter_logging)
                continue
            if novel is None:
                stats.failed += 1
                write_novel_log(log_file, stub_novel(url), "FAIL", chapter_logging)
            elif novel.skipped:
                stats.skipped += 1
                write_novel_log(log_file, novel, "SKIP", chapter_logging)
                _register_download(url, novel)
            else:
                _register_download(url, novel)
                if novel.failed_pages:
                    stats.failed += 1
                    write_novel_log(log_file, novel, "PARTIAL", chapter_logging)
                else:
                    stats.downloaded += 1
                    write_novel_log(log_file, novel, "OK", chapter_logging)
            if queue and not stop_event.is_set():
                delay = DELAY_NOVEL + random.uniform(0, DELAY_NOVEL_JITTER)
                if controls is not None:
                    controls.interruptible_wait(delay)
                else:
                    stop_event.wait(delay)
    finally:
        write_run_footer(
            log_file,
            stats.downloaded,
            stats.skipped,
            stats.failed,
            time.monotonic() - started,
        )
        session.close()
        log.info("[%s] run log: %s", label, log_path)
    return stats


def download_categories(
    categories: list[str],
    *,
    limit: int = 0,
    forward: bool = False,
    workers: int = 1,
    verbose: bool = False,
    chapter_logging: bool = False,
    windscribe: bool = False,
    windscribe_location: str = "best",
    direct_interface: str | None = None,
    skip_route_check: bool = False,
    controls=None,
    event_callback=None,
) -> tuple[int, DownloadStats]:
    emit = event_callback or print
    unknown = [category for category in categories if category not in CATEGORIES]
    if unknown:
        emit(f"Unknown categories: {', '.join(unknown)}")
        return 2, DownloadStats()
    urls = catalogue_urls(categories)
    complete = _complete_urls(categories)
    pending = [url for url in urls if url not in complete]
    emit(
        f"Catalogue: {len(urls):,} live, {len(complete & set(urls)):,} complete, "
        f"{len(pending):,} pending"
    )
    if not pending:
        return 0, DownloadStats()

    queue = DownloadQueue(pending, limit)
    stop_event = controls.stop_event if controls is not None else threading.Event()
    sessions: list[tuple[str, cffi_requests.Session, bool]] = []
    try:
        if windscribe:
            # Direct route runs newest→oldest, the tunnel oldest→newest, so the
            # two ends of the shared queue are consumed without collisions.
            routes = open_windscribe_routes(
                windscribe_location,
                direct_interface=direct_interface,
                skip_route_check=skip_route_check,
                emit=emit,
            )
            directions = {"direct": False, "windscribe": True}
            sessions = [
                (route.label, route.session, directions[route.label])
                for route in routes
            ]
        else:
            sessions = [("direct", cffi_requests.Session(), forward)]
    except Exception as exc:
        for _, session, _ in sessions:
            session.close()
        emit(f"Route setup failed: {exc}")
        return 1, DownloadStats()

    results: list[DownloadStats] = []
    lock = threading.Lock()

    def run(label: str, session: cffi_requests.Session, direction: bool) -> None:
        if controls is not None:
            controls.register_worker()
        try:
            result = _process_queue(
                label,
                queue,
                session,
                forward=direction,
                workers=workers,
                verbose=verbose or len(sessions) > 1,
                stop_event=stop_event,
                chapter_logging=chapter_logging,
                controls=controls,
                event_callback=event_callback,
            )
            with lock:
                results.append(result)
        finally:
            if controls is not None:
                controls.unregister_worker()

    threads = [
        threading.Thread(target=run, args=item, name=f"download-{item[0]}")
        for item in sessions
    ]
    for thread in threads:
        thread.start()
    interrupted = False
    try:
        for thread in threads:
            while thread.is_alive():
                thread.join(0.5)
    except KeyboardInterrupt:
        interrupted = True
        stop_event.set()
        emit("Interrupted; waiting for active novel downloads to finish.")
        for thread in threads:
            thread.join()

    total = DownloadStats()
    for result in results:
        total.add(result)
    emit(
        f"Done: {total.downloaded} downloaded, {total.skipped} skipped, "
        f"{total.failed} failed. Embedding index was not changed."
    )
    return (130 if interrupted else (1 if total.failed else 0)), total
