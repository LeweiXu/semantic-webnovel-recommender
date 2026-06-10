#!/usr/bin/env python3
"""
Download novels listed in the catalogue without following previous/next links.

By default, one direct worker processes pending novels newest-to-oldest.
With --windscribe, traffic is split across two exit IPs in a single process:
the VPN worker uses the default route (Windscribe tunnel) oldest-to-newest,
while the direct worker is bound to the LAN interface so it bypasses the tunnel
and keeps the real IP, continuing from the newest end. A shared queue prevents
the workers from claiming the same catalogue URL.

Windscribe's Linux per-app split tunneling and proxy gateway do not provide an
independent route on WSL2 (both follow the host's default route), so the split
is done with curl's CURLOPT_INTERFACE on the direct session instead. This
requires the Windscribe kill-switch firewall to be off, which --windscribe
arranges automatically via windscribe-cli.

Complete output files are the only resume state. This script never reads or
writes state.json.

Usage:
    python scrape_from_catalogue.py
    python scrape_from_catalogue.py --forward
    python scrape_from_catalogue.py --windscribe
    python scrape_from_catalogue.py --windscribe --workers 3
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from curl_cffi import requests as cffi_requests

from scraper import (
    DATA_DIR,
    DELAY_NOVEL,
    DELAY_NOVEL_JITTER,
    INCOMPLETE_LOG,
    OUTPUT_DIR,
    is_complete_file,
    open_run_log,
    scrape_novel,
    source_url_from_file,
    stub_novel,
    write_novel_log,
    write_run_footer,
)

log = logging.getLogger(__name__)

DEFAULT_DIRECT_INTERFACE = "eth0"
PUBLIC_IP_URL = "https://api.ipify.org"


@dataclass
class WorkerResult:
    label: str
    scraped: int = 0
    skipped: int = 0
    failed: int = 0
    log_path: Path | None = None


class CatalogueQueue:
    """Thread-safe queue claimed from opposite ends by the two routes."""

    def __init__(self, urls: list[str], limit: int) -> None:
        self._urls = deque(urls)
        self._limit = limit
        self._claimed = 0
        self._lock = threading.Lock()

    def claim(self, *, forward: bool) -> tuple[int, str] | None:
        with self._lock:
            if not self._urls:
                return None
            if self._limit and self._claimed >= self._limit:
                return None
            url = self._urls.popleft() if forward else self._urls.pop()
            self._claimed += 1
            return self._claimed, url

    @property
    def remaining(self) -> int:
        with self._lock:
            return len(self._urls)


def is_gl_landing_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    if not parsed.path.startswith("/gl/") or not parsed.path.endswith(".html"):
        return False
    name = parsed.path.rsplit("/", 1)[-1][:-5]
    return bool(name) and not name.startswith("index") and not re.search(r"_\d+$", name)


def load_catalogue_urls(path: Path) -> tuple[list[str], int, int]:
    """Return unique live GL landing-page URLs in catalogue order."""
    with path.open(encoding="utf-8") as handle:
        raw = json.load(handle)
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
        if not is_complete_file(path):
            continue
        url = source_url_from_file(path)
        if url:
            complete.add(url)
    return complete


def run_windscribe_cli(*args: str) -> subprocess.CompletedProcess[str]:
    binary = shutil.which("windscribe-cli")
    if not binary:
        raise RuntimeError(
            "windscribe-cli is not installed. Install the verified package with: "
            "sudo apt-get install /tmp/windscribe-cli.deb"
        )
    return subprocess.run(
        [binary, *args],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
    )


def disable_windscribe_firewall() -> None:
    """Turn off the kill switch so the interface-bound direct route can exit.

    With the firewall on, Windscribe drops every packet that does not leave via
    the tunnel, which blocks the direct worker bound to the LAN interface.
    """
    result = run_windscribe_cli("firewall", "off")
    text = f"{result.stdout}\n{result.stderr}".strip()
    if result.returncode != 0:
        raise RuntimeError(f"Could not disable Windscribe firewall: {text}")
    log.info("Windscribe kill-switch firewall is off (required for the direct route).")


def ensure_windscribe_connected(location: str) -> None:
    """Require login, connect Windscribe if needed, and disable the kill switch."""
    status = run_windscribe_cli("status")
    status_text = f"{status.stdout}\n{status.stderr}".strip()
    if status.returncode != 0:
        raise RuntimeError(f"windscribe-cli status failed: {status_text}")
    if "Login state: Logged in" not in status_text:
        raise RuntimeError("Windscribe is not logged in. Run: windscribe-cli login")
    if "Connect state: Connected:" in status_text:
        disable_windscribe_firewall()
        return

    log.info("Connecting Windscribe to %s...", location)
    connected = run_windscribe_cli("connect", location)
    connect_text = f"{connected.stdout}\n{connected.stderr}".strip()
    if connected.returncode != 0:
        raise RuntimeError(f"Windscribe connection failed: {connect_text}")

    status = run_windscribe_cli("status")
    status_text = f"{status.stdout}\n{status.stderr}".strip()
    if status.returncode != 0 or "Connect state: Connected:" not in status_text:
        raise RuntimeError(f"Windscribe did not reach connected state: {status_text}")
    log.info("Windscribe connected.")
    disable_windscribe_firewall()


def detect_direct_interface() -> str:
    """Return the default-route interface that is not the Windscribe tunnel."""
    result = subprocess.run(
        ["ip", "route", "show", "default"],
        check=False,
        capture_output=True,
        text=True,
    )
    for line in result.stdout.splitlines():
        fields = line.split()
        if "dev" in fields:
            dev = fields[fields.index("dev") + 1]
            if not dev.startswith(("utun", "wg", "tun")):
                return dev
    return DEFAULT_DIRECT_INTERFACE


def public_ip(session: cffi_requests.Session) -> str:
    response = session.get(PUBLIC_IP_URL, timeout=15)
    response.raise_for_status()
    address = response.text.strip()
    if not address:
        raise RuntimeError("public IP service returned an empty response")
    return address


def verify_distinct_routes(
    direct_session: cffi_requests.Session,
    windscribe_session: cffi_requests.Session,
    interface: str,
) -> None:
    """Prevent a dual run unless the two sessions use different exit IPs."""
    direct_ip = public_ip(direct_session)
    windscribe_ip = public_ip(windscribe_session)
    log.info("Route check: direct=%s  windscribe=%s", direct_ip, windscribe_ip)
    if direct_ip == windscribe_ip:
        raise RuntimeError(
            f"direct (bound to {interface}) and Windscribe sessions share the "
            f"public IP {direct_ip}. The tunnel is probably down, or "
            f"'{interface}' is not the LAN interface (see --direct-interface)."
        )


def process_catalogue(
    *,
    label: str,
    queue: CatalogueQueue,
    session: cffi_requests.Session,
    output_dir: Path,
    forward: bool,
    workers: int,
    verbose: bool,
    limit: int,
    stop_event: threading.Event,
    chapter_logging: bool = False,
) -> WorkerResult:
    result = WorkerResult(label=label)
    result.log_path, log_file = open_run_log(f"catalogue_{label}", limit, output_dir)
    started = time.monotonic()
    direction = "oldest to newest" if forward else "newest to oldest"
    log.info("[%s] Started: %s", label, direction)

    try:
        while not stop_event.is_set():
            claim = queue.claim(forward=forward)
            if claim is None:
                break
            claim_number, url = claim
            log.info("[%s] Claim %d: %s", label, claim_number, url)

            try:
                novel = scrape_novel(
                    session,
                    url,
                    output_dir,
                    workers=workers,
                    verbose=verbose,
                )
            except FileNotFoundError:
                result.failed += 1
                log.error("[%s] Catalogue URL 404: %s", label, url)
                write_novel_log(log_file, stub_novel(url), "FAIL", chapter_logging)
            except Exception:
                result.failed += 1
                log.exception("[%s] Unexpected failure at %s", label, url)
                write_novel_log(log_file, stub_novel(url), "FAIL", chapter_logging)
            else:
                if novel is None:
                    result.failed += 1
                    write_novel_log(log_file, stub_novel(url), "FAIL", chapter_logging)
                    log.error("[%s] Unrecoverable failure at %s; continuing.", label, url)
                elif novel.skipped:
                    result.skipped += 1
                    write_novel_log(log_file, novel, "SKIP", chapter_logging)
                elif novel.failed_pages:
                    result.failed += 1
                    write_novel_log(log_file, novel, "PARTIAL", chapter_logging)
                    log.warning(
                        "[%s] Incomplete (%d failed pages): %s; logged to %s",
                        label,
                        len(novel.failed_pages),
                        url,
                        INCOMPLETE_LOG,
                    )
                else:
                    result.scraped += 1
                    write_novel_log(log_file, novel, "OK", chapter_logging)

            if not stop_event.is_set() and queue.remaining:
                delay = DELAY_NOVEL + random.uniform(0, DELAY_NOVEL_JITTER)
                log.info("[%s] Sleeping %.1fs before next novel...", label, delay)
                stop_event.wait(delay)
    finally:
        write_run_footer(
            log_file,
            result.scraped,
            result.skipped,
            result.failed,
            time.monotonic() - started,
        )
        session.close()
        log.info(
            "[%s] Done: %d scraped  %d skipped  %d failed; log: %s",
            label,
            result.scraped,
            result.skipped,
            result.failed,
            result.log_path,
        )
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--catalogue",
        default=str(DATA_DIR / "gl_catalog.json"),
        help="Catalogue JSON from scripts/create_catalogue.py",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Claim at most N pending novels across all routes (0 = unlimited)",
    )
    parser.add_argument("--output", default=str(OUTPUT_DIR), help="Output directory")
    parser.add_argument(
        "--workers",
        type=int,
        default=1,
        help="Parallel page fetches per active route (default 1)",
    )
    parser.add_argument("--verbose", action="store_true", help="Print every page on its own line")
    parser.add_argument(
        "--chapter-logging",
        action="store_true",
        help="Log per-chapter character tables instead of the default "
        "per-page counts (per-page surfaces silently empty/failed pages)",
    )
    parser.add_argument(
        "--forward",
        action="store_true",
        help="Direct-only mode: process oldest to newest instead of newest to oldest",
    )
    parser.add_argument(
        "--windscribe",
        action="store_true",
        help="Add a second worker on the Windscribe tunnel (oldest-to-newest) "
        "while the direct worker keeps the real IP via the LAN interface",
    )
    parser.add_argument(
        "--direct-interface",
        default=None,
        help="LAN interface to bind the direct worker to so it bypasses the "
        "tunnel (default: auto-detect the non-tunnel default route)",
    )
    parser.add_argument(
        "--windscribe-location",
        default="best",
        help="Location passed to 'windscribe-cli connect' when disconnected",
    )
    parser.add_argument(
        "--skip-route-check",
        action="store_true",
        help="Skip public-IP comparison (not recommended)",
    )
    args = parser.parse_args()

    if args.limit < 0:
        parser.error("--limit must be 0 or greater")
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    if args.windscribe and args.forward:
        parser.error("--forward cannot be combined with --windscribe")
    return args


def main() -> int:
    args = parse_args()
    catalogue_path = Path(args.catalogue)
    if not catalogue_path.exists() and catalogue_path.parent == Path("."):
        legacy_candidate = DATA_DIR / catalogue_path.name
        if legacy_candidate.exists():
            catalogue_path = legacy_candidate

    try:
        urls, invalid, duplicates = load_catalogue_urls(catalogue_path)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        log.error("Cannot load catalogue %s: %s", catalogue_path, exc)
        return 1
    if not urls:
        log.error("Catalogue %s contains no usable URLs.", catalogue_path)
        return 1

    if invalid:
        log.warning("Ignored %d catalogue entries without a usable URL.", invalid)
    if duplicates:
        log.warning("Ignored %d duplicate catalogue URL(s).", duplicates)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    complete = find_complete_urls(output_dir)
    pending = [url for url in urls if url not in complete]
    queue = CatalogueQueue(pending, args.limit)
    log.info(
        "Catalogue: %d live URL(s), %d complete, %d pending from %s",
        len(urls),
        len(urls) - len(pending),
        len(pending),
        catalogue_path,
    )
    if not pending:
        log.info("Nothing to download.")
        return 0

    direct_session: cffi_requests.Session | None = None
    windscribe_session: cffi_requests.Session | None = None
    try:
        if args.windscribe:
            interface = args.direct_interface or detect_direct_interface()
            ensure_windscribe_connected(args.windscribe_location)
            # The direct worker is pinned to the LAN interface so it bypasses
            # the tunnel; the VPN worker takes the default route through it.
            direct_session = cffi_requests.Session(interface=f"if!{interface}")
            windscribe_session = cffi_requests.Session()
            if not args.skip_route_check:
                verify_distinct_routes(direct_session, windscribe_session, interface)
        else:
            direct_session = cffi_requests.Session()
    except (OSError, RuntimeError, ValueError) as exc:
        if direct_session is not None:
            direct_session.close()
        if windscribe_session is not None:
            windscribe_session.close()
        log.error("Windscribe setup failed: %s", exc)
        return 1

    stop_event = threading.Event()
    jobs = [
        (
            "direct",
            direct_session,
            args.forward,
        ),
    ]
    if windscribe_session is not None:
        jobs.append(("windscribe", windscribe_session, True))

    results: list[WorkerResult] = []
    threads: list[threading.Thread] = []
    result_lock = threading.Lock()

    def run_job(label: str, session: cffi_requests.Session, forward: bool) -> None:
        worker_result = process_catalogue(
            label=label,
            queue=queue,
            session=session,
            output_dir=output_dir,
            forward=forward,
            workers=args.workers,
            verbose=args.verbose or len(jobs) > 1,
            limit=args.limit,
            stop_event=stop_event,
            chapter_logging=args.chapter_logging,
        )
        with result_lock:
            results.append(worker_result)

    for label, session, forward in jobs:
        thread = threading.Thread(
            target=run_job,
            args=(label, session, forward),
            name=f"catalogue-{label}",
        )
        thread.start()
        threads.append(thread)

    interrupted = False
    try:
        for thread in threads:
            while thread.is_alive():
                thread.join(timeout=0.5)
    except KeyboardInterrupt:
        interrupted = True
        stop_event.set()
        log.warning("Interrupted; waiting for active novel downloads to finish.")
        for thread in threads:
            thread.join()

    scraped = sum(result.scraped for result in results)
    skipped = sum(result.skipped for result in results)
    failed = sum(result.failed for result in results)
    log.info(
        "Catalogue run complete: %d scraped  %d skipped  %d failed",
        scraped,
        skipped,
        failed,
    )
    return 130 if interrupted else 0


if __name__ == "__main__":
    sys.exit(main())
