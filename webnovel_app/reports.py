from __future__ import annotations

from pathlib import Path

from recsys.catalog import load_catalog
from recsys.store import load_category
from scripts.repo_paths import CATEGORIES, REPORTS_DIR, category_dir


def catalogue_report(categories: list[str]) -> str:
    lines = ["Catalogue summary"]
    total_live = total_dead = total_meta = total_downloaded = 0
    for category in categories:
        catalog = load_catalog(category)
        metadata = load_category(category)
        live = sum(record.fetch_status == "ok" for record in catalog.values())
        dead = len(catalog) - live
        downloaded = sum(record.downloaded for record in metadata.values())
        lines.append(
            f"  {category:<15} catalogue={len(catalog):>6,} live={live:>6,} "
            f"404={dead:>5,} metadata={len(metadata):>6,} downloaded={downloaded:>6,}"
        )
        total_live += live
        total_dead += dead
        total_meta += len(metadata)
        total_downloaded += downloaded
    lines.append(
        f"  {'TOTAL':<15} live={total_live:>6,} 404={total_dead:>5,} "
        f"metadata={total_meta:>6,} downloaded={total_downloaded:>6,}"
    )
    return "\n".join(lines)


def size_report(categories: list[str]) -> str:
    lines = ["Downloaded library size"]
    all_sizes: list[int] = []
    for category in categories:
        files = list(category_dir(category).rglob("*.txt"))
        sizes = [path.stat().st_size for path in files]
        all_sizes.extend(sizes)
        total = sum(sizes)
        average = total / len(sizes) if sizes else 0
        lines.append(
            f"  {category:<15} novels={len(files):>6,} "
            f"total={total / 1024 / 1024:>10,.2f} MB "
            f"average={average / 1024:>8,.2f} KB"
        )
    total = sum(all_sizes)
    average = total / len(all_sizes) if all_sizes else 0
    lines.append(
        f"  {'TOTAL':<15} novels={len(all_sizes):>6,} "
        f"total={total / 1024 / 1024 / 1024:>10,.2f} GB "
        f"average={average / 1024:>8,.2f} KB"
    )
    return "\n".join(lines)


def incomplete_report(categories: list[str]) -> tuple[str, list[str]]:
    lines = ["Incomplete downloads"]
    urls: list[str] = []
    for category in categories:
        count = failed_pages = 0
        for path in category_dir(category).rglob("*.txt"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if "页面获取失败" not in text:
                continue
            count += 1
            failed_pages += text.count("[页面获取失败:")
            source = next(
                (line[3:].strip() for line in text.splitlines() if line.startswith("来源：")),
                "",
            )
            if source:
                urls.append(source)
        lines.append(
            f"  {category:<15} novels={count:>5,} failed_pages={failed_pages:>5,}"
        )
    lines.append(f"  Total affected novels: {len(urls):,}")
    return "\n".join(lines), urls


def write_report(name: str, text: str) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / name
    path.write_text(text + "\n", encoding="utf-8")
    return path
