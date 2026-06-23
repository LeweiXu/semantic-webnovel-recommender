"""Repository paths shared by scripts, independent of the current directory."""
from __future__ import annotations

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
REPORTS_DIR = ROOT_DIR / "reports"
DOCS_DIR = ROOT_DIR / "docs"
LOGS_DIR = ROOT_DIR / "logs"

# All downloaded categories live under a single library/ folder. Each category
# is a self-contained subfolder (library/gl/, library/yanqing/, …) holding its
# metadata.jsonl (recommender store), _catalog.jsonl (crawl graph + 404s), and
# YYYY-MM/*.txt downloaded full text.
LIBRARY_DIR = ROOT_DIR / "library"

CATEGORIES = [
    "gl", "yanqing", "bl", "xiandaidushi", "chongsheng",
    "jiakong", "jiakonglishi", "chuanyue", "wuxia",
]

# Legacy alias: GL was historically the only category (formerly output/).
OUTPUT_DIR = LIBRARY_DIR / "gl"


def category_dir(category: str) -> Path:
    return LIBRARY_DIR / category


def metadata_path(category: str) -> Path:
    return LIBRARY_DIR / category / "metadata.jsonl"


def catalog_path(category: str) -> Path:
    return LIBRARY_DIR / category / "_catalog.jsonl"


def resolve_data_input(value: str | Path) -> Path:
    """Resolve a path, falling back to data/<basename> for legacy commands."""
    path = Path(value)
    if path.exists() or path.is_absolute() or path.parent != Path("."):
        return path
    candidate = DATA_DIR / path.name
    return candidate if candidate.exists() else path
