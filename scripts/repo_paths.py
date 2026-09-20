"""Repository paths shared by scripts, independent of the current directory."""
from __future__ import annotations

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
REPORTS_DIR = ROOT_DIR / "reports"
DOCS_DIR = ROOT_DIR / "docs"
LOGS_DIR = ROOT_DIR / "logs"

# library/ is grouped by where a novel came from, and then, for a crawled
# source, by category:
#
#     library/52shuku/<category>/metadata.jsonl   recommender store
#                               /_catalog.jsonl   crawl graph + 404s
#                               /YYYY-MM/*.txt    downloaded full text
#     library/uploads/metadata.jsonl, *.txt, and whatever folders you drop in
#
# Uploads aren't crawled and have no category split, so for them the source
# folder is the store folder. Everything below routes through category_dir() so
# that shape is stated once.
LIBRARY_DIR = ROOT_DIR / "library"

CRAWL_SOURCE = "52shuku"
UPLOADS_CATEGORY = "uploads"
SOURCES = (CRAWL_SOURCE, UPLOADS_CATEGORY)

CATEGORIES = [
    "gl", "yanqing", "bl", "xiandaidushi", "chongsheng",
    "jiakong", "jiakonglishi", "chuanyue", "wuxia",
]

# Every folder that carries a metadata.jsonl, which is what the reader and the
# search index read. Uploads are a store like any other, just not a crawled one.
STORE_CATEGORIES = [*CATEGORIES, UPLOADS_CATEGORY]

# Legacy alias: GL was historically the only category (formerly output/).
OUTPUT_DIR = LIBRARY_DIR / CRAWL_SOURCE / "gl"


def source_for(category: str) -> str:
    """Which source folder a category's files live under."""
    return UPLOADS_CATEGORY if category == UPLOADS_CATEGORY else CRAWL_SOURCE


def category_dir(category: str) -> Path:
    if category == UPLOADS_CATEGORY:
        return LIBRARY_DIR / UPLOADS_CATEGORY
    return LIBRARY_DIR / CRAWL_SOURCE / category


def metadata_path(category: str) -> Path:
    return category_dir(category) / "metadata.jsonl"


def catalog_path(category: str) -> Path:
    return category_dir(category) / "_catalog.jsonl"


def store_dirs() -> list[tuple[str, Path]]:
    """(category, folder) for every metadata.jsonl actually on disk.

    Discovery rather than a fixed list, so a category that was never crawled
    costs nothing and a new one is picked up without a code change.
    """
    found: list[tuple[str, Path]] = []
    uploads = LIBRARY_DIR / UPLOADS_CATEGORY
    if (uploads / "metadata.jsonl").is_file():
        found.append((UPLOADS_CATEGORY, uploads))
    crawl = LIBRARY_DIR / CRAWL_SOURCE
    if crawl.is_dir():
        for child in sorted(crawl.iterdir()):
            if (child / "metadata.jsonl").is_file():
                found.append((child.name, child))
    return found


def resolve_data_input(value: str | Path) -> Path:
    """Resolve a path, falling back to data/<basename> for legacy commands."""
    path = Path(value)
    if path.exists() or path.is_absolute() or path.parent != Path("."):
        return path
    candidate = DATA_DIR / path.name
    return candidate if candidate.exists() else path
