#!/usr/bin/env python3
"""
report_size.py — summarise the size of the scraped output.

Usage:
    python scripts/report_size.py [--output output]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from repo_paths import OUTPUT_DIR


def _fmt(kb: float) -> str:
    if kb >= 1024 * 1024:
        return f"{kb / 1024 / 1024:.2f} GB"
    if kb >= 1024:
        return f"{kb / 1024:.2f} MB"
    return f"{kb:.1f} KB"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--output", default=str(OUTPUT_DIR), help="Output directory (default: output)")
    args = ap.parse_args()

    base = Path(args.output)
    files = sorted(base.rglob("*.txt"))
    if not files:
        print(f"No .txt files found under {base}/")
        return 1

    sizes = [f.stat().st_size for f in files]
    total_b = sum(sizes)
    total_kb = total_b / 1024
    avg_kb = total_kb / len(files)

    largest = max(files, key=lambda f: f.stat().st_size)
    smallest = min(files, key=lambda f: f.stat().st_size)

    print(f"Output directory : {base}/")
    print(f"Novels (.txt)    : {len(files)}")
    print(f"Total size       : {_fmt(total_kb)}  ({total_b:,} bytes)")
    print(f"Average per novel: {_fmt(avg_kb)}")
    print(f"Largest          : {_fmt(largest.stat().st_size / 1024)}  {largest.name}")
    print(f"Smallest         : {_fmt(smallest.stat().st_size / 1024)}  {smallest.name}")

    # Per-month breakdown
    print("\nBy month:")
    months: dict[str, list[int]] = {}
    for f in files:
        month = f.parent.name
        months.setdefault(month, []).append(f.stat().st_size)
    for month in sorted(months):
        s = months[month]
        print(f"  {month}: {len(s):4d} novels  {_fmt(sum(s) / 1024):>10}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
