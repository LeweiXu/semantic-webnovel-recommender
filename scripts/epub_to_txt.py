#!/usr/bin/env python3
"""Convert local epubs to .txt and upload the books the server doesn't have yet.

Three steps, meant to be run in order on a folder of novels (e.g. Novels/):

    python scripts/epub_to_txt.py convert Novels/
        Write <name>.txt next to every <name>.epub that doesn't have one yet.
        Text only, in spine order; illustrations are dropped.

    python scripts/epub_to_txt.py missing Novels/ > missing.json
        List the local .txt books (converted or not) that aren't on the server,
        matched loosely on file name and epub title. Fill in each entry's
        "folder" (a single-level folder under uploads/), then:

    python scripts/epub_to_txt.py upload Novels/ missing.json [--dry-run]
        Copy those books to the server and save them through upload_api, so
        they get metadata records and land in the novels-backup repo.

Talks to the server over ssh, the same way scripts/deploy.sh does (the
"homeserver" alias by default; override with NOVEL_SERVER / NOVEL_DEST).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

from webnovel.epub import epub_metadata, epub_to_text

SERVER = os.environ.get("NOVEL_SERVER", "homeserver")
DEST = os.environ.get("NOVEL_DEST", "Novel_Project").rstrip("/")
SERVER_PYTHON = os.environ.get("NOVEL_PYTHON", "~/venv-novel/bin/python")
STAGING = f"{DEST}/data/epub_import"


def _remote(command: str) -> str:
    """Run upload_api.py on the server and return its stdout."""
    full = (
        f"cd {DEST} && PYTHONPATH=.:reader-app/backend "
        f"{SERVER_PYTHON} reader-app/backend/upload_api.py {command}"
    )
    result = subprocess.run(["ssh", SERVER, full], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"server command failed: {result.stderr.strip()}")
    sys.stderr.write(result.stderr)
    return result.stdout


# ── convert ──────────────────────────────────────────────────────────────────

def convert(root: Path) -> int:
    made = skipped = failed = 0
    for epub in sorted(root.rglob("*.epub")):
        txt = epub.with_suffix(".txt")
        if txt.exists():
            skipped += 1
            continue
        try:
            text = epub_to_text(epub, progress=False)
        except Exception as exc:  # broken zips, DRM, odd packaging
            print(f"failed: {epub.relative_to(root)}: {exc}", file=sys.stderr)
            failed += 1
            continue
        if not text.strip():
            print(f"empty: {epub.relative_to(root)}", file=sys.stderr)
            failed += 1
            continue
        txt.write_text(text + "\n", encoding="utf-8")
        made += 1
    print(f"converted {made}, already had .txt {skipped}, failed {failed}", file=sys.stderr)
    return 0 if not failed else 1


# ── missing ──────────────────────────────────────────────────────────────────

_BRACKETED = re.compile(r"[\[【（(][^\]】）)]*[\]】）)]")
_SPLITS = re.compile(r"_| - |作者|by ", re.IGNORECASE)


def name_keys(name: str) -> set[str]:
    """Loose comparison keys for a title or file stem.

    Drops bracketed tags/site names, punctuation and case, and also keys the
    part before an "_author" / " - author" / "作者" tail, so "公主病_鱼霜【完结】"
    and "公主病" meet."""
    name = unicodedata.normalize("NFKC", name)
    keys = set()
    for part in [name, _SPLITS.split(name)[0]]:
        part = _BRACKETED.sub("", part).replace("《", "").replace("》", "")
        key = "".join(ch for ch in part.casefold() if ch.isalnum())
        if len(key) >= 2:
            keys.add(key)
    return keys


def server_keys() -> set[str]:
    names = json.loads(_remote("names"))
    keys: set[str] = set()
    for file in names["files"]:
        keys |= name_keys(Path(file).stem)
    for title in names["titles"]:
        keys |= name_keys(title)
    return keys


def local_books(root: Path) -> list[dict]:
    """Every .txt under root, with epub title/author when it came from one."""
    books = []
    for txt in sorted(root.rglob("*.txt")):
        meta = {"title": "", "author": ""}
        epub = txt.with_suffix(".epub")
        if epub.exists():
            try:
                meta = epub_metadata(epub)
            except Exception:
                pass
        books.append({
            "path": txt.relative_to(root).as_posix(),
            "title": meta["title"] or txt.stem,
            "author": meta["author"],
            "from_epub": epub.exists(),
            "folder": "",
        })
    return books


def missing(root: Path) -> int:
    on_server = server_keys()
    out = [
        book for book in local_books(root)
        if not (name_keys(Path(book["path"]).stem) | name_keys(book["title"])) & on_server
    ]
    print(f"{len(out)} local books not on the server", file=sys.stderr)
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


# ── upload ───────────────────────────────────────────────────────────────────

def upload(root: Path, plan_path: Path, dry_run: bool) -> int:
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    books = [book for book in plan if book.get("folder") and not book.get("skip")]
    unplaced = [book["path"] for book in plan if not book.get("folder") and not book.get("skip")]
    if unplaced:
        print(f"{len(unplaced)} entries have no folder and will be skipped", file=sys.stderr)
    print(f"uploading {len(books)} books", file=sys.stderr)
    if dry_run:
        for book in books:
            print(f"  {book['folder']}/  <- {book['path']}")
        return 0

    with tempfile.TemporaryDirectory() as tmp:
        staging = Path(tmp)
        manifest = []
        for index, book in enumerate(books):
            # Flat, ascii staging names: the real name comes from the title.
            name = f"{index:04d}.txt"
            shutil.copyfile(root / book["path"], staging / name)
            manifest.append({**book, "path": name})
        (staging / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False), encoding="utf-8",
        )
        subprocess.run(["ssh", SERVER, f"rm -rf {STAGING} && mkdir -p {STAGING}"], check=True)
        subprocess.run(["rsync", "-az", f"{staging}/", f"{SERVER}:{STAGING}/"], check=True)
    try:
        # _remote runs from inside DEST, so the staging path is relative to it.
        saved = json.loads(_remote(f"import {STAGING.removeprefix(DEST + '/')}"))
    finally:
        subprocess.run(["ssh", SERVER, f"rm -rf {STAGING}"], check=False)
    print(f"saved {len(saved)} books", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("convert").add_argument("root", type=Path)
    sub.add_parser("missing").add_argument("root", type=Path)
    up = sub.add_parser("upload")
    up.add_argument("root", type=Path)
    up.add_argument("plan", type=Path)
    up.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "convert":
        return convert(args.root)
    if args.command == "missing":
        return missing(args.root)
    return upload(args.root, args.plan, args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
