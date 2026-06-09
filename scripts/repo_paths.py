"""Repository paths shared by scripts, independent of the current directory."""
from __future__ import annotations

from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT_DIR / "data"
REPORTS_DIR = ROOT_DIR / "reports"
DOCS_DIR = ROOT_DIR / "docs"
OUTPUT_DIR = ROOT_DIR / "output"
LOGS_DIR = ROOT_DIR / "logs"


def resolve_data_input(value: str | Path) -> Path:
    """Resolve a path, falling back to data/<basename> for legacy commands."""
    path = Path(value)
    if path.exists() or path.is_absolute() or path.parent != Path("."):
        return path
    candidate = DATA_DIR / path.name
    return candidate if candidate.exists() else path
