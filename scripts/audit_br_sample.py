#!/usr/bin/env python3
"""Randomly sample reading pages from already-downloaded novels and flag any
whose main-text HTML contains <br>.

The old <p>-only parser ignored everything outside <p> tags, so a reading page
that lays its text out with loose <br> (no wrapping <p>) was silently dropped.
This samples random pages and asks the *current* parser whether the page has
content <br> (the trailing ad block's <br> is excluded). A hit means the old
parser would have mishandled that novel.

No landing requests are needed: the run logs in logs/ record each novel's page
count, and chapter URLs are deterministic — landing ``.../X.html`` has pages
``.../X_2.html`` … ``.../X_<n+1>.html``. So each check is a single page fetch.

Two sampling modes:
  * total (default): --pages is the total number of random pages to check
    across the whole corpus, irrespective of novel;
  * per-novel (--per-novel): --pages is the number of random pages to check
    in each novel.
With --pages omitted, every page is checked.

    python scripts/audit_br_sample.py --pages 500              # 500 pages total
    python scripts/audit_br_sample.py --pages 3 --per-novel    # 3 pages/novel
    python scripts/audit_br_sample.py --pages 500 --audit-whole

Exit code is 1 if any novel was flagged, else 0.
"""
from __future__ import annotations

import argparse
import random
import re
import sys
import time
from pathlib import Path

from bs4 import BeautifulSoup
from curl_cffi import requests as cffi_requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import scraper  # noqa: E402

# Summary line, e.g. "...  74p→68章  641KB  OK" or new "...  5p  8847字 ...".
_PAGES_RE = re.compile(r"\s(\d+)p[→\s]")
_GL_RE = re.compile(r"https?://\S+/gl/\S+\.html")


def page_counts_from_logs(log_dir: Path) -> dict[str, int]:
    """Map each logged novel landing URL to its recorded page count."""
    counts: dict[str, int] = {}
    for log_path in sorted(log_dir.glob("*.log")):
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, line in enumerate(lines):
            m = _PAGES_RE.search(line)
            if not m or i + 1 >= len(lines):
                continue
            url_m = _GL_RE.search(lines[i + 1])
            if not url_m:
                continue
            pages = int(m.group(1))
            url = url_m.group(0)
            counts[url] = max(counts.get(url, 0), pages)
    return counts


def infer_page_urls(landing_url: str, pages: int) -> list[str]:
    """Landing .../X.html -> [.../X_2.html, ..., .../X_<pages+1>.html]."""
    base = landing_url[:-5]  # strip ".html"
    return [f"{base}_{i}.html" for i in range(2, 2 + pages)]


def content_has_br(html: str) -> bool:
    """True if the cleaned reading content uses <br> (ad block excluded)."""
    return scraper.parse_chapter_page(html).has_br


def old_parse_chars(html: str) -> int:
    """Non-whitespace chars the pre-fix <p>-only parser would have kept."""
    soup = BeautifulSoup(html, "lxml")
    article = soup.find("article", class_="article-content") or soup.find(id="nr1")
    if not article:
        return 0
    kept = [p.get_text().strip() for p in article.find_all("p")
            if p.get_text().strip() and not scraper._is_ad(p.get_text())]
    return scraper._nonws_chars("\n\n".join(kept))


def audit_whole(session, page_urls: list[str]) -> tuple[int, int, int]:
    """Scan every page. Returns (pages_with_br, total_pages, chars_lost)."""
    br_pages = chars_lost = 0
    for page_url in page_urls:
        try:
            html = scraper.fetch(session, page_url).text
        except Exception:
            continue
        page = scraper.parse_chapter_page(html)
        if page.has_br:
            br_pages += 1
            chars_lost += max(0, scraper._nonws_chars(page.text) - old_parse_chars(html))
        time.sleep(0.1)
    return br_pages, len(page_urls), chars_lost


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--logs", default=str(scraper.LOG_DIR), help="directory of run logs")
    ap.add_argument("--pages", type=int, default=None,
                    help="pages to check: total across the corpus, or per novel "
                    "with --per-novel (default: every page)")
    ap.add_argument("--per-novel", action="store_true",
                    help="treat --pages as a count per novel instead of a global total")
    ap.add_argument("--audit-whole", action="store_true",
                    help="when a sampled page has <br>, scan that novel's whole page set")
    ap.add_argument("--seed", type=int, default=None, help="RNG seed for reproducibility")
    args = ap.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    counts = page_counts_from_logs(Path(args.logs))
    if not counts:
        print(f"No novel page counts found in {args.logs}/*.log", file=sys.stderr)
        return 2

    if args.per_novel:
        # N random pages within each novel
        pool = []
        for url, n in counts.items():
            purls = infer_page_urls(url, n)
            k = min(args.pages, n) if args.pages is not None else n
            pool += [(url, purl) for purl in random.sample(purls, k)]
        random.shuffle(pool)
        scope = f"{args.pages} page(s)/novel" if args.pages is not None else "every page"
    else:
        # N random pages across the whole corpus
        pool = [(url, purl)
                for url, n in counts.items()
                for purl in infer_page_urls(url, n)]
        random.shuffle(pool)
        if args.pages is not None:
            pool = pool[:args.pages]
        scope = f"{len(pool)} page(s) total"
    print(f"{len(counts)} novels in logs; checking {scope} ({len(pool)} fetch(es))...\n")

    session = cffi_requests.Session()
    checked = 0
    flagged: dict[str, str] = {}   # novel url -> note
    audited: set[str] = set()

    for novel_url, page_url in pool:
        try:
            html = scraper.fetch(session, page_url).text
        except Exception as e:
            print(f"  ??  {page_url}: {e}")
            continue
        checked += 1

        if content_has_br(html):
            note = ""
            if args.audit_whole and novel_url not in audited:
                audited.add(novel_url)
                br_pages, total, lost = audit_whole(
                    session, infer_page_urls(novel_url, counts[novel_url]))
                note = f"  → {br_pages}/{total} pages have <br>, ~{lost} chars lost"
            flagged.setdefault(novel_url, "")
            if note:
                flagged[novel_url] = note
            print(f"  [{checked}] BR  {page_url}{note}")
        else:
            print(f"  [{checked}] ok  {page_url}")
        time.sleep(0.1)

    print(f"\n=== {len(flagged)} novel(s) flagged from {checked} page checks ===")
    for url, note in sorted(flagged.items()):
        print(f"  {url}{note}")
    return 1 if flagged else 0


if __name__ == "__main__":
    raise SystemExit(main())
