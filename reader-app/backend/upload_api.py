"""Manual .txt upload into library/uploads/, with metadata autodetect.

Uploaded files land in their own store (library/uploads/metadata.jsonl) so they
read like any downloaded novel: chapters are detected from the text, and the
title/author/tags/synopsis come from the confirmation form. Autodetect just
pre-fills that form; the user always confirms before anything is written.
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime
from pathlib import Path

from recsys.store import NovelRecord, load_category, upsert_category, write_category
from scripts.repo_paths import category_dir
from webnovel.library import chapters_from_text, decode_text, detect_language

import backup
import novels

UPLOADS_CATEGORY = "uploads"


def _safe_filename(name: str) -> str:
    name = Path(name).name  # drop any directory part
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', "_", name).strip().strip(".")
    return name or "untitled"


def _parse_filename(stem: str) -> tuple[str, str, str]:
    """Best-effort (title, author, status) from a messy novel filename."""
    status = "完结" if "完结" in stem else ("连载" if "连载" in stem else "")
    # Drop bracketed tag groups like 【GL】, (完结), （gl）, [xxx].
    cleaned = re.sub(r"[【\[（(][^】\]）)]*[】\]）)]", "", stem).strip()
    title, author = cleaned, ""
    m = re.search(r"《([^》]+)》\s*[-—]?\s*(.*)", cleaned)
    if m:
        title, author = m.group(1).strip(), m.group(2).strip(" -—_·")
    elif " - " in cleaned:
        left, right = cleaned.split(" - ", 1)
        title, author = left.strip(), right.strip()
    elif "_" in cleaned:
        left, right = cleaned.rsplit("_", 1)
        title, author = left.strip(), right.strip()
    if len(author) > 30:  # probably not really an author
        author = ""
    return (title or stem).strip(), author, status


def _guess_synopsis(text: str, chapters) -> str:
    """A rough synopsis: the front matter before chapter one, else the opening."""
    if chapters and chapters[0].title == "Front matter":
        return chapters[0].body.strip()[:400]
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return " ".join(lines[:3])[:400]


def detect(filename: str, raw: bytes) -> dict:
    """Suggested metadata for the confirmation form. Writes nothing."""
    text = decode_text(raw)
    stem = Path(filename).stem
    title, author, status = _parse_filename(stem)
    chapters = chapters_from_text(text)
    return {
        "filename": _safe_filename(stem) + ".txt",
        "title": title,
        "author": author,
        "status": status,
        "language": detect_language(text[:2000]),
        "chapter_count": len(chapters),
        "synopsis": _guess_synopsis(text, chapters),
        "tags": [],
    }


def safe_subdir(subdir: str) -> str:
    """Clean a folder path relative to uploads/ ("" is the uploads root)."""
    parts = [
        _safe_filename(part) for part in Path(subdir).parts
        if part not in ("", "/") and not part.startswith((".", "_"))  # also drops ".."
    ]
    return "/".join(part for part in parts if part)


def _taken_stems(root: Path) -> set[str]:
    # Reader slugs are "uploads/<stem>" whatever the folder, so stems have to be
    # unique across the whole uploads tree, not just within one folder.
    return {p.stem.casefold() for p in root.rglob("*.txt") if ".git" not in p.parts}


def save(
    filename: str,
    raw: bytes,
    *,
    title: str,
    author: str = "",
    tags: list[str] | None = None,
    synopsis: str = "",
    status: str = "",
    subdir: str = "",
    sync: bool = True,
) -> dict:
    """Write the file (normalised to UTF-8) and upsert its metadata record.

    `subdir` is a folder under uploads/ to save into (created if needed).
    """
    text = decode_text(raw)
    # Name the file after the confirmed title (clean slug), falling back to the
    # original filename stem.
    stem = _safe_filename(title.strip() or Path(filename).stem)
    root = category_dir(UPLOADS_CATEGORY)
    subdir = safe_subdir(subdir)
    dest_dir = root / subdir if subdir else root
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Don't clobber an existing upload, or reuse its stem anywhere in uploads/.
    taken = _taken_stems(root)
    fname = f"{stem}.txt"
    counter = 2
    while Path(fname).stem.casefold() in taken:
        fname = f"{stem}_{counter}.txt"
        counter += 1
    dest = dest_dir / fname
    dest.write_text(text, encoding="utf-8")

    rel = "/".join(part for part in (UPLOADS_CATEGORY, subdir, fname) if part)
    chapters = chapters_from_text(text)
    now = datetime.now()
    record = NovelRecord(
        url=f"upload:{rel}",
        category=UPLOADS_CATEGORY,
        title=title.strip() or Path(fname).stem,
        author=author.strip(),
        status=status.strip(),
        upload_date=now.strftime("%Y年%m月%d日 %H:%M:%S"),
        year_month=now.strftime("%Y-%m"),
        chapter_count=len(chapters),
        synopsis=synopsis.strip(),
        tags=[t.strip() for t in (tags or []) if t.strip()],
        source="full",
        file=rel,
    )
    upsert_category(UPLOADS_CATEGORY, [record])
    novels.invalidate(record.url)
    if sync:
        backup.schedule()

    return {
        "id": f"{UPLOADS_CATEGORY}/{Path(fname).stem}",  # slug the reader opens by
        "url": record.url,
        "title": record.title,
        "chapter_count": record.chapter_count,
    }


def resync_record(old_rel: str, new_rel: str | None) -> bool:
    """Keep uploads/metadata.jsonl in step with a moved, renamed or deleted file.

    Returns whether a record actually pointed at the file. A move keeps the
    record's url, which is what reading progress is keyed by, so progress
    survives; only its shelf id (built from the file name) changes.
    """
    records = load_category(UPLOADS_CATEGORY)
    match = next((r for r in records.values() if r.file == old_rel), None)
    if match is None:
        return False
    if new_rel is None:
        records.pop(match.url, None)
    else:
        match.file = new_rel
    write_category(UPLOADS_CATEGORY, records)
    novels.invalidate(match.url)
    return True


# ── Server-side CLI, used by scripts/epub_to_txt.py over ssh ─────────────────
#
#   python reader-app/backend/upload_api.py names           JSON list of what's here
#   python reader-app/backend/upload_api.py import <dir>    save <dir>/manifest.json books
#   python reader-app/backend/upload_api.py move <plan>     reorganise existing uploads
#
# Run from the repo root with PYTHONPATH=.:reader-app/backend. Each command
# ends with one backup sync instead of one per book.

def _cli_names() -> dict:
    """Every title and file stem on the server, for "is this already here?"."""
    from recsys.store import load_all
    from scripts.repo_paths import store_dirs

    root = category_dir(UPLOADS_CATEGORY)
    files = sorted(
        p.relative_to(root).as_posix() for p in root.rglob("*.txt") if ".git" not in p.parts
    )
    records = load_all([category for category, _ in store_dirs()])
    titles = sorted({r.title for r in records.values() if r.file})
    uploads = [
        {"file": r.file, "title": r.title, "author": r.author, "synopsis": r.synopsis[:300]}
        for r in load_category(UPLOADS_CATEGORY).values()
    ]
    return {"files": files, "titles": titles, "uploads": uploads}


def _cli_import(staging: Path) -> list[dict]:
    """Save each book in staging/manifest.json:
    [{"path": "x.txt", "folder": "English/Light Novels", "title", "author", ...}]."""
    manifest = json.loads((staging / "manifest.json").read_text(encoding="utf-8"))
    saved = []
    for item in manifest:
        raw = (staging / item["path"]).read_bytes()
        guess = detect(item["path"], raw)
        saved.append(save(
            item["path"], raw,
            title=item.get("title") or guess["title"],
            author=item.get("author") or guess["author"],
            tags=item.get("tags") or [],
            synopsis=item.get("synopsis") or guess["synopsis"],
            status=item.get("status") or guess["status"],
            subdir=item.get("folder", ""),
            sync=False,
        ))
    return saved


def _cli_move(plan_path: Path) -> list[str]:
    """Apply {"<old path under uploads/>": "<new folder>" or "<new folder>/<name>.txt"}.

    A destination ending in .txt renames the file as well as moving it."""
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    root = category_dir(UPLOADS_CATEGORY)
    moved = []
    for old, folder in plan.items():
        source = root / old
        if not source.is_file():
            print(f"skip (missing): {old}", file=sys.stderr)
            continue
        name = source.name
        if folder.lower().endswith(".txt"):
            folder, _, name = folder.rpartition("/")
            name = _safe_filename(name)
        folder = safe_subdir(folder)
        dest = (root / folder if folder else root) / name
        if dest == source:
            continue
        if dest.exists():
            print(f"skip (exists): {old} -> {folder}", file=sys.stderr)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        source.rename(dest)
        resync_record(
            f"{UPLOADS_CATEGORY}/{old}",
            f"{UPLOADS_CATEGORY}/{dest.relative_to(root).as_posix()}",
        )
        moved.append(dest.relative_to(root).as_posix())
    # Drop folders the move left empty.
    for directory in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if directory.is_dir() and ".git" not in directory.parts and not any(directory.iterdir()):
            directory.rmdir()
    return moved


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if not args or args[0] not in {"names", "import", "move"}:
        print("usage: upload_api.py names | import <dir> | move <plan.json>", file=sys.stderr)
        return 2
    if args[0] == "names":
        result = _cli_names()
    elif args[0] == "import":
        result = _cli_import(Path(args[1]))
    else:
        result = _cli_move(Path(args[1]))
    if args[0] != "names":
        print(f"backup pushed: {backup.sync()}", file=sys.stderr)
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
