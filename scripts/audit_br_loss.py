#!/usr/bin/env python3
"""Confirm whether the old <p>-only parser silently dropped <br> text from
already-scraped novels.

For each audited novel it re-fetches the reading pages and runs BOTH parsers on
the same HTML: the current parser and a faithful re-implementation of the old
<p>-only parser. Any page where the old parser yields fewer characters is a
page that lost text (loose <br> content outside <p>). This needs no trust in
the saved file — it measures the loss directly.

Audits the oldest-uploaded novels (the likely culprits) plus a random sample
by default; --all checks every scraped novel.

    python scripts/audit_br_loss.py                 # oldest 40 + random 40
    python scripts/audit_br_loss.py --oldest 100 --sample 100
    python scripts/audit_br_loss.py --all
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

_UPLOAD_RE = re.compile(r"上传时间：(.*)")


def old_parse(html: str) -> str:
    """Faithful copy of the pre-fix <p>-only parser."""
    soup = BeautifulSoup(html, "lxml")
    article = soup.find("article", class_="article-content") or soup.find(id="nr1")
    if not article:
        return ""
    paragraphs = []
    for p in article.find_all("p"):
        text = p.get_text()
        if scraper._is_ad(text):
            continue
        stripped = text.strip()
        if stripped:
            paragraphs.append(stripped)
    return "\n\n".join(paragraphs)


def upload_date(path: Path) -> str:
    with path.open(encoding="utf-8") as f:
        for _ in range(10):
            line = f.readline()
            m = _UPLOAD_RE.search(line)
            if m:
                return m.group(1).strip()
    return ""


def pick(output_dir: Path, oldest: int, sample: int, do_all: bool) -> list[tuple[Path, str]]:
    files = []
    for p in output_dir.rglob("*.txt"):
        u = scraper.source_url_from_file(p)
        if u:
            files.append((p, u, upload_date(p)))
    if do_all:
        return [(p, u) for p, u, _ in files]
    files.sort(key=lambda r: r[2])  # oldest upload date first
    chosen = files[:oldest]
    rest = files[oldest:]
    chosen += random.sample(rest, min(sample, len(rest)))
    return [(p, u) for p, u, _ in chosen]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", default=str(scraper.OUTPUT_DIR))
    ap.add_argument("--oldest", type=int, default=40)
    ap.add_argument("--sample", type=int, default=40)
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--tolerance", type=int, default=20,
                    help="ignore per-page deltas smaller than this many chars")
    args = ap.parse_args()

    targets = pick(Path(args.output), args.oldest, args.sample, args.all)
    print(f"Auditing {len(targets)} novel(s)...\n")
    session = cffi_requests.Session()

    affected = []
    checked = 0
    for path, url in targets:
        try:
            resp = scraper.fetch(session, url)
            meta = scraper.parse_landing(resp.text, url)
        except Exception as e:
            print(f"  ?? could not re-fetch landing {url}: {e}")
            continue
        novel_lost = 0
        lost_pages = 0
        for page_url in meta.chapter_urls:
            try:
                html = scraper.fetch(session, page_url).text
            except Exception:
                continue
            new = scraper._nonws_chars(scraper.parse_chapter_page(html).text)
            old = scraper._nonws_chars(old_parse(html))
            if new - old > args.tolerance:
                novel_lost += new - old
                lost_pages += 1
            time.sleep(0.1)
        checked += 1
        if novel_lost:
            affected.append((novel_lost, lost_pages, len(meta.chapter_urls), path))
            print(f"  ⚠ LOST {novel_lost} chars over {lost_pages}/{len(meta.chapter_urls)} "
                  f"pages: {path.name}")
        else:
            print(f"  ok  {path.name}")

    print(f"\n=== {len(affected)}/{checked} audited novels lost text ===")
    for lost, lp, tp, path in sorted(affected, reverse=True):
        print(f"  {lost:>8} chars  {lp}/{tp} pages  {path}")
    return 1 if affected else 0


if __name__ == "__main__":
    raise SystemExit(main())
