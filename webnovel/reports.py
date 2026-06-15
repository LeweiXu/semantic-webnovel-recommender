from __future__ import annotations

from pathlib import Path

from recsys.catalog import load_catalog
from recsys.store import load_category
from scripts.repo_paths import CATEGORIES, REPORTS_DIR, category_dir


def _human_size(num_bytes: float) -> str:
    """Render a byte count with an appropriate binary unit."""
    value = float(num_bytes)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:,.0f} {unit}" if unit == "B" else f"{value:,.1f} {unit}"
        value /= 1024
    return f"{value:,.1f} TB"


def render_table(
    headers: list[str],
    rows: list[list[str]],
    *,
    aligns: list[str] | None = None,
    footer: list[str] | None = None,
    title: str | None = None,
) -> str:
    """Render a light-box Unicode table. First column left-aligned, rest right.

    A ``footer`` row (e.g. a TOTAL line) is set off with a rule above it.
    """
    columns = len(headers)
    aligns = aligns or (["<"] + [">"] * (columns - 1))
    body = rows + ([footer] if footer else [])
    widths = [len(str(headers[i])) for i in range(columns)]
    for row in body:
        for i in range(columns):
            widths[i] = max(widths[i], len(str(row[i])))

    def rule(left: str, mid: str, right: str) -> str:
        return left + mid.join("─" * (width + 2) for width in widths) + right

    def line(cells: list[str]) -> str:
        rendered = (f" {str(cells[i]):{aligns[i]}{widths[i]}} " for i in range(columns))
        return "│" + "│".join(rendered) + "│"

    out = [title] if title else []
    out.append(rule("┌", "┬", "┐"))
    out.append(line(headers))
    out.append(rule("├", "┼", "┤"))
    out.extend(line(row) for row in rows)
    if footer:
        out.append(rule("├", "┼", "┤"))
        out.append(line(footer))
    out.append(rule("└", "┴", "┘"))
    return "\n".join(out)


def catalogue_report(categories: list[str]) -> str:
    headers = ["Category", "Catalogue", "Live", "404", "Metadata", "Downloaded"]
    rows: list[list[str]] = []
    totals = [0, 0, 0, 0, 0]
    for category in categories:
        catalog = load_catalog(category)
        metadata = load_category(category)
        live = sum(record.fetch_status == "ok" for record in catalog.values())
        dead = len(catalog) - live
        downloaded = sum(record.downloaded for record in metadata.values())
        values = [len(catalog), live, dead, len(metadata), downloaded]
        rows.append([category, *(f"{value:,}" for value in values)])
        totals = [running + value for running, value in zip(totals, values)]
    footer = ["TOTAL", *(f"{value:,}" for value in totals)]
    return render_table(headers, rows, footer=footer, title="Catalogue summary")


def size_report(categories: list[str]) -> str:
    headers = ["Category", "Novels", "Total", "Average"]
    rows: list[list[str]] = []
    grand_files = 0
    grand_bytes = 0
    for category in categories:
        sizes = [path.stat().st_size for path in category_dir(category).rglob("*.txt")]
        total = sum(sizes)
        average = total / len(sizes) if sizes else 0
        rows.append([category, f"{len(sizes):,}", _human_size(total), _human_size(average)])
        grand_files += len(sizes)
        grand_bytes += total
    grand_average = grand_bytes / grand_files if grand_files else 0
    footer = ["TOTAL", f"{grand_files:,}", _human_size(grand_bytes), _human_size(grand_average)]
    return render_table(headers, rows, footer=footer, title="Downloaded library size")


def incomplete_report(categories: list[str]) -> tuple[str, list[str]]:
    headers = ["Category", "Novels", "Failed pages"]
    rows: list[list[str]] = []
    urls: list[str] = []
    total_novels = total_pages = 0
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
        rows.append([category, f"{count:,}", f"{failed_pages:,}"])
        total_novels += count
        total_pages += failed_pages
    footer = ["TOTAL", f"{total_novels:,}", f"{total_pages:,}"]
    return render_table(headers, rows, footer=footer, title="Incomplete downloads"), urls


def write_report(name: str, text: str) -> Path:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    path = REPORTS_DIR / name
    path.write_text(text + "\n", encoding="utf-8")
    return path
