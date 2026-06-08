#!/usr/bin/env python3
"""
inspect_urls.py — extract and structure the URL pattern of scraped novels.

Reads the preamble of every output/**/*.txt, pulls out the 来源 / 上一篇 / 下一篇
URLs and upload time, decomposes each URL into (category, shard, id), decodes the
base62 id to an integer, and writes a structured JSON + a human-readable table
sorted by upload time. Also flags chain inconsistencies (a novel whose 上一篇 URL
is not the 来源 of any scraped novel — i.e. a potential gap / broken link).

Usage:
    python inspect_urls.py [--output output] [--json url_map.json]
"""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

# URL shapes seen on the site:
#   /gl/07_b/bkecS.html   → category=gl, shard=07_b, id=bkecS   (modern)
#   /gl/12764.html        → category=gl, shard=None,  id=12764   (old numeric)
#   /gl/hvsq.html         → category=gl, shard=None,  id=hvsq    (old base62)
_URL_RE = re.compile(r"/([a-z]+)/(?:([0-9]{2}_[a-z])/)?([0-9A-Za-z]+)\.html")

# base62 alphabet per context.md: 0-9 A-Z a-z
_B62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
_B62_IDX = {c: i for i, c in enumerate(_B62)}


def b62_decode(s: str) -> int | None:
    """Decode a base62 string to int. Returns None if any char is out of alphabet."""
    n = 0
    for c in s:
        if c not in _B62_IDX:
            return None
        n = n * 62 + _B62_IDX[c]
    return n


def parse_url(url: str) -> dict:
    """Decompose a novel URL into its components."""
    if not url or url == "—":
        return {}
    m = _URL_RE.search(url)
    if not m:
        return {"raw": url, "parse_error": True}
    category, shard, ident = m.group(1), m.group(2), m.group(3)
    return {
        "category": category,
        "shard": shard,
        "id": ident,
        "id_num": b62_decode(ident),
    }


def _field(lines: list[str], prefix: str) -> str:
    """Return the text after the first line starting with prefix (Chinese colon)."""
    for line in lines:
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return ""


def _nav_url(lines: list[str], label: str) -> str:
    """Return the URL line belonging to a 上一篇/下一篇 block."""
    for i, line in enumerate(lines):
        if line.startswith(label):
            for j in range(i + 1, min(i + 3, len(lines))):
                if "URL:" in lines[j]:
                    return lines[j].split("URL:", 1)[1].strip()
    return ""


def parse_preamble(path: Path) -> dict:
    # Only the header matters; read the first ~20 lines.
    with path.open(encoding="utf-8") as f:
        lines = [next(f, "").rstrip("\n") for _ in range(20)]

    source = _field(lines, "来源：")
    prev_url = _nav_url(lines, "上一篇：")
    next_url = _nav_url(lines, "下一篇：")

    return {
        "file": str(path),
        "title": _field(lines, "标题："),
        "upload_date": _field(lines, "上传时间："),
        "source_url": source,
        "source": parse_url(source),
        "prev_url": prev_url,
        "next_url": next_url,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", default="output", help="Output directory (default: output)")
    ap.add_argument("--json", default="url_map.json", help="Structured JSON output path")
    args = ap.parse_args()

    base = Path(args.output)
    files = sorted(base.rglob("*.txt"))
    if not files:
        print(f"No .txt files found under {base}/")
        return 1

    records = [parse_preamble(f) for f in files]
    # Sort by upload time (string sort works for 'YYYY年MM月DD日 HH:MM:SS')
    records.sort(key=lambda r: r["upload_date"])

    # Build the set of source URLs we actually have, for chain-gap detection.
    have_urls = {r["source_url"] for r in records if r["source_url"]}

    # Flag novels whose 上一篇 points to something we don't have (a gap).
    gaps = []
    for r in records:
        if r["prev_url"] and r["prev_url"] not in have_urls:
            gaps.append(r)

    with open(args.json, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    # ── Human-readable table ──────────────────────────────────────────────────
    print(f"{'upload time':<21} {'shard':<6} {'id':<8} {'id_num':>10}  title")
    print("-" * 78)
    prev_num = None
    for r in records:
        src = r["source"]
        shard = src.get("shard") or "—"
        ident = src.get("id") or "?"
        id_num = src.get("id_num")
        id_num_s = str(id_num) if id_num is not None else "—"
        # Mark a jump in id_num vs the previous row (by upload order)
        delta = ""
        if id_num is not None and prev_num is not None:
            d = id_num - prev_num
            if d != 1:
                delta = f"  Δ{d:+d}"
        prev_num = id_num if id_num is not None else prev_num
        print(f"{r['upload_date']:<21} {shard:<6} {ident:<8} {id_num_s:>10}  {r['title']}{delta}")

    print("-" * 78)
    print(f"Total novels         : {len(records)}")
    print(f"Structured JSON      : {args.json}")
    print(f"Chain gaps (上一篇 not in our set): {len(gaps)}")
    for g in gaps[:20]:
        print(f"  {g['title']}")
        print(f"     have : {g['source_url']}")
        print(f"     wants: {g['prev_url']}  (missing)")
    if len(gaps) > 20:
        print(f"  … and {len(gaps) - 20} more")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
