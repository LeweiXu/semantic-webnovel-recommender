#!/usr/bin/env python3
"""
check_incomplete.py — scan scraped .txt files for failed-page placeholders.

The scraper writes "[页面获取失败: <url>]" in place of any chapter page that
could not be fetched (after retries). This finds every novel that contains one,
so you can re-download it. Prints the offending page URLs per novel.

Usage:
    python check_incomplete.py [--output output] [--urls broken_pages.txt]
"""
from __future__ import annotations

import argparse
import re
from pathlib import Path

MARKER = "页面获取失败"
_FAIL_RE = re.compile(r"\[页面获取失败:\s*(\S+?)\]")
_SOURCE_RE = re.compile(r"^来源：(\S+)", re.MULTILINE)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", default="output", help="Output directory (default: output)")
    ap.add_argument("--urls", help="Optional: write affected novels' 来源 URLs to this file")
    args = ap.parse_args()

    base = Path(args.output)
    files = sorted(base.rglob("*.txt"))
    if not files:
        print(f"No .txt files under {base}/")
        return 1

    affected: list[tuple[Path, list[str], str | None]] = []
    total_failed_pages = 0
    for f in files:
        text = f.read_text(encoding="utf-8")
        if MARKER in text:
            failed_urls = _FAIL_RE.findall(text)
            total_failed_pages += len(failed_urls)
            src = _SOURCE_RE.search(text)
            affected.append((f, failed_urls, src.group(1) if src else None))

    print(f"Scanned {len(files)} novels under {base}/")
    print(f"Incomplete novels (≥1 failed page): {len(affected)}")
    print(f"Total failed pages across all novels: {total_failed_pages}")

    for f, urls, src in affected:
        print(f"\n  {f.relative_to(base)}  — {len(urls)} failed page(s)")
        print(f"      来源: {src or '?'}")
        for u in urls:
            print(f"      missing: {u}")

    if args.urls and affected:
        with open(args.urls, "w", encoding="utf-8") as out:
            for _, _, src in affected:
                if src:
                    out.write(src + "\n")
        print(f"\nWrote {len(affected)} 来源 URLs → {args.urls}")

    if not affected:
        print("\n✓ No novels contain failed-page placeholders.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
