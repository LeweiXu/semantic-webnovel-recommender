#!/usr/bin/env python3
"""
probe_rate_limit.py
===================

GOAL
----
Empirically (and *safely*) estimate how aggressively we can query 52shuku.net
before Cloudflare/the origin starts blocking us. We are NOT trying to get
blocked on purpose and stay blocked -- we ramp up gradually, watch for the
*first* sign of trouble, and stop immediately.

WHAT "BLOCKED" LOOKS LIKE ON THIS SITE
--------------------------------------
From earlier manual testing we know a missing/blocked page does NOT always
return a clean HTTP 404. It can come back as:
  - HTTP 403 / 503 with a Cloudflare challenge body.
  - A JS interstitial (status 200) with "Just a moment" but NO novel content.
  - A connection reset / timeout.
  - HTTP 429 (explicit rate limit).

IMPORTANT: Cloudflare ALWAYS injects __CF$cv$params and challenge-platform
scripts into real content pages (standard JS fingerprinting). Those two strings
are NOT block signals -- they appear on every legitimate page. The actual
hard-block markers are "Just a moment", "cf-browser-verification", and
"Attention Required", which appear on interstitial-only pages that lack any
real novel content.

We use curl_cffi (browser TLS fingerprint impersonation) instead of plain
requests. This reduces the probability that Cloudflare escalates from soft
JS injection to a hard interstitial.

STRATEGY
--------
1. Hit a small pool of URLs we already KNOW are valid GL novel pages (so a
   non-200 means *us being blocked*, not a real missing page).
2. Run several "bursts". Each burst fires N requests with a fixed delay
   between them. We start gentle (long delay) and get progressively more
   aggressive (shorter delay, bigger burst).
3. After each burst we print a summary. The moment we detect a CHALLENGED /
   RATE_LIMITED / repeated ERROR response, we STOP and report the last
   setting that was still clean. That last-clean setting is our safe ceiling.

This gives us a defensible "queries per minute we can sustain" number to feed
into the real scraper's throttle, without getting our IP stuck behind a
challenge for hours.

USAGE
-----
    python scripts/probe_rate_limit.py
    python scripts/probe_rate_limit.py --max-aggressive

Be a good citizen: run this ONCE to characterise the site, write the number
into docs/context.md, and don't re-run it repeatedly.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
from dataclasses import dataclass, field
from statistics import mean

from curl_cffi import requests as cffi_requests

# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------

# Known-good GL novel URLs (confirmed live earlier). Using real pages means a
# failure is attributable to throttling, not to a bad URL. We rotate through
# these so we're not re-requesting the exact same path every time (which is
# both more representative of real scraping and slightly less suspicious).
KNOWN_GOOD_URLS = [
    "https://www.52shuku.net/gl/06_b/bkec8.html",
    "https://www.52shuku.net/gl/06_b/bkec7.html",
    "https://www.52shuku.net/gl/06_b/bkec6.html",
    "https://www.52shuku.net/gl/06_b/bkec5.html",
    "https://www.52shuku.net/gl/04_b/bkeaz.html",
    "https://www.52shuku.net/gl/04_b/bkeay.html",
    "https://www.52shuku.net/gl/04_b/bkeax.html",
    "https://www.52shuku.net/gl/04_b/bkeaw.html",
]

# curl_cffi impersonation target. chrome136 matches a recent Chrome release and
# produces a TLS ClientHello / HTTP/2 fingerprint that Cloudflare recognises
# as a real browser rather than a bot library.
IMPERSONATE = "chrome124"

REQUEST_TIMEOUT = 20  # seconds

# Burst plan: (label, num_requests, delay_seconds_between_requests).
# Ordered from gentle -> aggressive. We abort as soon as a burst shows trouble.
BURST_PLAN = [
    # ("warmup     (5 req, 5.0s delay)", 5, 5.0),
    # ("gentle     (8 req, 3.0s delay)", 8, 3.0),
    # ("moderate   (20 req, 1.0s delay)", 20, 1.0),
    # ("brisk      (100 req, 1.0s delay)", 100, 0.5),
    ("fast       (100 req, 0.1s delay)", 1000, 0.5),
]

# Only run with --max-aggressive, because this is the one most likely to trip
# protection. Kept separate so the default run stays safe.
AGGRESSIVE_BURST = ("flat-out   (20 req, 0.0s delay)", 20, 0.0)


# --------------------------------------------------------------------------
# Response classification
# --------------------------------------------------------------------------

OK = "OK"
NOT_FOUND = "NOT_FOUND"
CHALLENGED = "CHALLENGED"
RATE_LIMITED = "RATE_LIMITED"
ERROR = "ERROR"

# Hard-block markers: these appear on CF interstitial pages that contain NO
# real content. __CF$cv$params and challenge-platform are injected into every
# real page by Cloudflare and are NOT block signals -- do not include them here.
HARD_BLOCK_MARKERS = (
    "Just a moment",
    "cf-browser-verification",
    "Attention Required",
)

# Markers that only appear on real GL novel landing pages.
# If any of these are present, the response is genuine content regardless of
# what CF scripts are also injected.
CONTENT_MARKERS = (
    "小说简介",
    "上一篇",
    "下一篇",
)


def classify(resp: cffi_requests.Response | None, exc: Exception | None) -> str:
    if exc is not None:
        # Timeouts and connection resets are how a hard block often shows up.
        return ERROR
    assert resp is not None
    status = resp.status_code
    body = resp.text or ""

    if status == 429:
        return RATE_LIMITED

    # If genuine novel content is present, the response is OK even if Cloudflare
    # has injected its standard JS fingerprinting scripts alongside the content.
    if any(m in body for m in CONTENT_MARKERS):
        return OK

    # No real content -- check whether this looks like a hard CF block.
    if status in (403, 503) or any(m in body for m in HARD_BLOCK_MARKERS):
        return CHALLENGED

    if status == 404:
        return NOT_FOUND

    # 200 with no content and no recognised challenge -- unexpected, flag it.
    if status == 200:
        return CHALLENGED

    return ERROR


# --------------------------------------------------------------------------
# Probing
# --------------------------------------------------------------------------


@dataclass
class BurstResult:
    label: str
    delay: float
    counts: dict = field(default_factory=dict)
    latencies: list = field(default_factory=list)
    aborted: bool = False

    @property
    def clean(self) -> bool:
        """A burst is clean if every response was OK (or a genuine 404,
        which here would be unexpected since we use known-good URLs)."""
        bad = (
            self.counts.get(CHALLENGED, 0)
            + self.counts.get(RATE_LIMITED, 0)
            + self.counts.get(ERROR, 0)
        )
        return bad == 0

    def summary(self) -> str:
        parts = [f"{k}={v}" for k, v in sorted(self.counts.items())]
        lat = f"{mean(self.latencies):.2f}s avg" if self.latencies else "n/a"
        flag = "  <-- TROUBLE" if not self.clean else ""
        return f"  {self.label:32s} | {' '.join(parts):28s} | {lat}{flag}"


def run_burst(session: cffi_requests.Session, label: str, n: int, delay: float) -> BurstResult:
    result = BurstResult(label=label, delay=delay)
    for i in range(n):
        url = KNOWN_GOOD_URLS[i % len(KNOWN_GOOD_URLS)]
        start = time.monotonic()
        resp = None
        exc = None
        try:
            resp = session.get(url, timeout=REQUEST_TIMEOUT, impersonate=IMPERSONATE)
        except Exception as e:  # noqa: BLE001
            exc = e
        elapsed = time.monotonic() - start
        result.latencies.append(elapsed)

        verdict = classify(resp, exc)
        result.counts[verdict] = result.counts.get(verdict, 0) + 1

        # Live per-request line so you can watch it happen.
        code = resp.status_code if resp is not None else "ERR"
        print(f"    [{i + 1:2d}/{n}] {code} {verdict:12s} {elapsed:5.2f}s  {url}")

        # Bail out of the burst immediately on the first sign of a block.
        if verdict in (CHALLENGED, RATE_LIMITED):
            result.aborted = True
            print("    !! Block signal detected -- aborting burst early.")
            break
        # One-off ERROR could be a transient network blip; tolerate a single
        # one, but two in a burst is treated as trouble by .clean anyway.

        if i < n - 1 and delay > 0:
            # Small jitter so we're not a perfectly periodic signal.
            time.sleep(delay + random.uniform(0, delay * 0.3))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--max-aggressive",
        action="store_true",
        help="Also run the zero-delay flat-out burst (most likely to trip protection).",
    )
    args = parser.parse_args()

    print("=" * 78)
    print("52shuku.net rate-limit probe  (curl_cffi / chrome136 impersonation)")
    print("Ramps from gentle to aggressive; STOPS at the first block signal.")
    print("=" * 78)

    plan = list(BURST_PLAN)
    if args.max_aggressive:
        plan.append(AGGRESSIVE_BURST)

    session = cffi_requests.Session()
    results: list[BurstResult] = []
    last_clean: BurstResult | None = None

    for label, n, delay in plan:
        print(f"\n>>> Burst: {label}")
        result = run_burst(session, label, n, delay)
        results.append(result)
        if result.clean:
            last_clean = result
        else:
            print("\nStopping ramp: this burst showed a block signal.")
            break
        # Breather between bursts so one burst doesn't bleed into the next.
        time.sleep(3.0)

    # ---- Report -------------------------------------------------------------
    print("\n" + "=" * 78)
    print("SUMMARY")
    print("=" * 78)
    for r in results:
        print(r.summary())

    print("\n" + "-" * 78)
    if last_clean is not None and all(r.clean for r in results):
        print(
            "All bursts in the plan stayed clean. The site tolerated everything\n"
            f"we tried, down to a {last_clean.delay:.1f}s delay. You can likely run\n"
            "the scraper at the most aggressive tested setting, but KEEP a safety\n"
            "margin -- see the recommendation below."
        )
        safe_delay = max(last_clean.delay, 1.0)
    elif last_clean is not None:
        print(
            f"Last CLEAN setting was: '{last_clean.label.strip()}'\n"
            f"  -> delay between requests: {last_clean.delay:.1f}s\n"
            "The next, more aggressive setting tripped a block signal."
        )
        # Recommend backing OFF from the last clean setting, not sitting on it.
        safe_delay = max(last_clean.delay * 2, 2.0)
    else:
        print(
            "Even the gentlest burst showed trouble. The site may already be\n"
            "challenging this IP, or a much longer delay is needed. Wait a while\n"
            "and/or start the real scraper at >=5s delay with backoff."
        )
        safe_delay = 5.0

    rpm = 60.0 / safe_delay if safe_delay > 0 else float("inf")
    print(
        f"\nRECOMMENDED SCRAPER THROTTLE: ~{safe_delay:.1f}s between requests "
        f"(~{rpm:.0f} req/min),\nplus randomized jitter and exponential backoff on any "
        "CHALLENGED/429 response.\n"
        "Record this number in docs/context.md so the scraper phase uses it."
    )
    print("-" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
