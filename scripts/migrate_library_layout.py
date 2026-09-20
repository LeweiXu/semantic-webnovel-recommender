#!/usr/bin/env python3
"""Repoint metadata.jsonl `file` paths at the source-grouped library layout.

library/ used to be a folder per category (library/gl/, library/yanqing/, …).
It is now grouped by where a novel came from first (library/52shuku/gl/,
library/uploads/), so every record's `file`, which is stored relative to
library/, needs the source folder in front of it. Uploads already sit directly
under library/uploads/ and come out unchanged.

Safe to run more than once: a record is only rewritten to a path that actually
exists on disk, so a second pass finds nothing to do. Records whose .txt is
missing at both the old and the new location are rewritten structurally and
reported, since there is no file to confirm either way.

    python scripts/migrate_library_layout.py            # report only
    python scripts/migrate_library_layout.py --apply    # write it
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from recsys.store import load_category, write_category  # noqa: E402
from scripts.repo_paths import (  # noqa: E402
    CRAWL_SOURCE, LIBRARY_DIR, STORE_CATEGORIES, category_dir, metadata_path,
)


def structural(category: str, file: str) -> str:
    """Where `file` belongs under the new layout, by path shape alone."""
    parts = list(PurePosixPath(file).parts)
    if parts and parts[0] == CRAWL_SOURCE:
        parts = parts[1:]          # already migrated
    if parts and parts[0] == category:
        parts = parts[1:]          # drop the category folder, re-added below
    base = category_dir(category).relative_to(LIBRARY_DIR)
    return (base.joinpath(*parts)).as_posix()


def migrate(category: str, apply: bool) -> tuple[int, int, int]:
    """Return (rewritten, already fine, rewritten blind because no file)."""
    records = load_category(category)
    changed = fine = blind = 0
    for record in records.values():
        if not record.file or Path(record.file).is_absolute():
            continue
        if (LIBRARY_DIR / record.file).is_file():
            fine += 1
            continue
        target = structural(category, record.file)
        if target == record.file:
            continue
        if (LIBRARY_DIR / target).is_file():
            changed += 1
        else:
            blind += 1
        record.file = target
    if apply and (changed or blind):
        write_category(category, records)
    return changed, fine, blind


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="write the changes")
    args = ap.parse_args()

    totals = [0, 0, 0]
    for category in STORE_CATEGORIES:
        if not metadata_path(category).exists():
            continue
        changed, fine, blind = migrate(category, args.apply)
        totals = [a + b for a, b in zip(totals, (changed, fine, blind))]
        print(f"{category:<14} repointed={changed:<6} already ok={fine:<6} no file on disk={blind}")
    print(f"{'TOTAL':<14} repointed={totals[0]:<6} already ok={totals[1]:<6} no file on disk={totals[2]}")
    if not args.apply:
        print("\ndry run, nothing written. re-run with --apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
