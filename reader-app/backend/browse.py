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

from scripts.repo_paths import LIBRARY_DIR, UPLOADS_CATEGORY

BROWSE_DIR = Path(os.environ.get("NOVEL_BROWSE_DIR", LIBRARY_DIR)).resolve()

# Only files you put there yourself can be renamed or deleted through the app.
# The crawled sources are described by their metadata.jsonl, so moving those
# files around from the file explorer would just desync the two.
MANAGED_ROOT = UPLOADS_CATEGORY

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
        entries.append({
            "name": child.name, "path": _rel(child), "kind": kind, "size": size,
            # Whether the client may offer rename/delete on it. Decided here so
            # the rule lives in one place rather than being re-derived from the
            # path shape in the UI.
            "managed": managed(child),
        })
    # Folders first, then files, each alphabetical (case-insensitive).
    entries.sort(key=lambda e: (e["kind"] != "dir", e["name"].casefold()))
    rel = _rel(base)
    parent = None if rel == "" else _rel(base.parent)
    return {"path": rel, "parent": parent, "entries": entries}


def managed(path: Path) -> bool:
    """True when this path sits inside the writable (uploads) subtree."""
    root = BROWSE_DIR / MANAGED_ROOT
    return path != root and root in path.parents


def assert_managed(path: Path) -> None:
    if not managed(path):
        raise ValueError(f"only files under {MANAGED_ROOT}/ can be changed")


def search(query: str, limit: int = 30, root: str = MANAGED_ROOT) -> list[dict]:
    """Readable files under `root` whose name matches, newest first.

    Only the uploads subtree is searched. Everything crawled is already in a
    metadata.jsonl, and the catalogue search covers that far better than a
    filename match ever could.
    """
    needle = query.strip().casefold()
    if not needle:
        return []
    try:
        base = safe_join(root)
    except ValueError:
        return []
    if not base.is_dir():
        return []
    hits: list[dict] = []
    for child in base.rglob("*"):
        if len(hits) >= limit:
            break
        if child.name.startswith((".", "_")) or not child.is_file():
            continue
        kind = classify(child)
        if kind not in ("text", "doc"):
            continue
        if needle not in child.stem.casefold():
            continue
        try:
            size = child.stat().st_size
        except OSError:
            size = None
        hits.append({"name": child.name, "path": _rel(child), "kind": kind, "size": size})
    hits.sort(key=lambda hit: hit["name"].casefold())
    return hits


def rename(relpath: str, new_name: str) -> str:
    """Rename a file in place, returning its new browse-relative path."""
    target = safe_join(relpath)
    assert_managed(target)
    if not target.is_file():
        raise ValueError("not a file")
    name = new_name.strip()
    # A name, not a path: renaming must never be a way to move a file out.
    if not name or name != Path(name).name or name.startswith("."):
        raise ValueError("invalid file name")
    if Path(name).suffix.lower() != target.suffix.lower():
        name = f"{name}{target.suffix}"
    destination = target.with_name(name)
    if destination.exists():
        raise ValueError("a file with that name already exists")
    target.rename(destination)
    return _rel(destination)


def delete(relpath: str) -> None:
    target = safe_join(relpath)
    assert_managed(target)
    if not target.is_file():
        raise ValueError("not a file")
    target.unlink()
