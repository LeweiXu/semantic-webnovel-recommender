"""Filesystem browsing for the Library file explorer.

Walks a raw directory tree so the frontend can explore folders and open novels
that aren't in the metadata index (the big hand-organized Windows library). The
root defaults to the library/ store; override with NOVEL_BROWSE_DIR.

Every path that comes from the client goes through safe_join(), which is the
path-traversal guard. Don't reach into the tree any other way.
"""
from __future__ import annotations

import os
from pathlib import Path

from scripts.repo_paths import LIBRARY_DIR

BROWSE_DIR = Path(os.environ.get("NOVEL_BROWSE_DIR", LIBRARY_DIR)).resolve()

# .txt opens in the reader; these three get a download link (Phase 3). Anything
# else (images, zips, …) shows greyed out.
TEXT_EXTS = {".txt"}
DOC_EXTS = {".epub", ".pdf", ".docx"}


def safe_join(relpath: str) -> Path:
    """Resolve relpath under BROWSE_DIR, rejecting anything outside the root.

    This is the traversal guard: a "../" or an absolute path that escapes the
    browse root raises ValueError instead of reaching a file it shouldn't.
    """
    target = (BROWSE_DIR / relpath).resolve()
    if target != BROWSE_DIR and BROWSE_DIR not in target.parents:
        raise ValueError("path outside browse root")
    return target


def classify(path: Path) -> str:
    if path.is_dir():
        return "dir"
    ext = path.suffix.lower()
    if ext in TEXT_EXTS:
        return "text"
    if ext in DOC_EXTS:
        return "doc"
    return "other"


def _rel(path: Path) -> str:
    """Browse-root-relative posix path, or "" for the root itself."""
    return "" if path == BROWSE_DIR else path.relative_to(BROWSE_DIR).as_posix()


def list_dir(relpath: str = "") -> dict:
    base = safe_join(relpath)
    if not base.is_dir():
        raise ValueError("not a directory")
    entries: list[dict] = []
    for child in base.iterdir():
        # Hide dotfiles and the per-category store files (metadata.jsonl,
        # _catalog.jsonl, and other underscore-prefixed bookkeeping).
        if child.name.startswith((".", "_")) or child.name == "metadata.jsonl":
            continue
        kind = classify(child)
        try:
            size = None if kind == "dir" else child.stat().st_size
        except OSError:
            size = None
        entries.append({"name": child.name, "path": _rel(child), "kind": kind, "size": size})
    # Folders first, then files, each alphabetical (case-insensitive).
    entries.sort(key=lambda e: (e["kind"] != "dir", e["name"].casefold()))
    rel = _rel(base)
    parent = None if rel == "" else _rel(base.parent)
    return {"path": rel, "parent": parent, "entries": entries}
